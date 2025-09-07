# C1C Matchmaker — bot_clanmatch_prefix.py
# Env vars:
#   GSHEET_ID | GOOGLE_SHEET_ID | CONFIG_SHEET_ID
#   GOOGLE_SERVICE_ACCOUNT_JSON | SERVICE_ACCOUNT_JSON
#   DISCORD_TOKEN
#   C1C_MATCH_TAB (optional, default "bot_info")

import os, json, logging, asyncio, re
from typing import Optional, Dict, List
from datetime import datetime, timezone

# === C1C Canonical Helpers (harmonized) =====================================
import sys, logging, json, os, re as _reh
from typing import Optional as _Optional
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except Exception:
    _ZoneInfo = None
from datetime import datetime as _dt, timezone as _tz

def c1c_get_logger(name="c1c"):
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)
    return logging.getLogger(name)

def c1c_make_intents():
    import discord as _discord
    intents = _discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.guilds = True
    intents.reactions = True
    return intents

# Emoji resolver
import discord as _discord
_EMOJI_TAG_RE = _reh.compile(r"^<a?:\w+:\d+>$")
def resolve_emoji_text(guild: _discord.Guild, value: _Optional[str], fallback: _Optional[str]=None) -> str:
    v = (value or fallback or "").strip()
    if not v: return ""
    if _EMOJI_TAG_RE.match(v): return v
    if v.isdigit():
        e = _discord.utils.get(guild.emojis, id=int(v))
        return str(e) if e else ""
    e = _discord.utils.find(lambda x: x.name.lower()==v.lower(), guild.emojis)
    return str(e) if e else v

# Channel / thread formatter for human admins
async def fmt_chan_or_thread(bot: _discord.Client, guild: _discord.Guild, target_id: Optional[int]) -> str:
    if not target_id: return "—"
    obj = guild.get_channel(target_id) or await bot.fetch_channel(target_id)
    if not obj: return f"(unknown) `{target_id}`"
    mention = getattr(obj, "mention", f"<#{target_id}>")
    name = getattr(obj, "name", "unknown")
    return f"{mention} — **{name}** `{target_id}`"

# Sheets client (modern google-auth)
def gs_client():
    import gspread as _gspread
    from google.oauth2.service_account import Credentials as _Creds
    raw = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("SERVICE_ACCOUNT_JSON"))
    if not raw:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON (or SERVICE_ACCOUNT_JSON).")
    info = json.loads(raw)
    creds = _Creds.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return _gspread.authorize(creds)

def open_sheet_by_env():
    sid = (os.getenv("GSHEET_ID") or os.getenv("GOOGLE_SHEET_ID") or os.getenv("CONFIG_SHEET_ID"))
    if not sid:
        raise RuntimeError("Set GSHEET_ID (or GOOGLE_SHEET_ID / CONFIG_SHEET_ID).")
    import gspread  # local import for clarity
    return gs_client().open_by_key(sid)
# ============================================================================

import discord
from discord.ext import commands
from aiohttp import web
import gspread

UTC = timezone.utc
log = c1c_get_logger("c1c.clanmatch")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DEFAULT_TAB   = os.getenv("C1C_MATCH_TAB", "bot_info")  # default worksheet/tab (case-insensitive)

# ------------------- tiny web (Render health) -------------------
async def _health(_): return web.Response(text="ok")
async def run_web():
    app = web.Application()
    app.add_routes([web.get("/", _health), web.get("/healthz", _health)])
    runner = web.AppRunner(app); await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("health on :%s", port)
    while True: await asyncio.sleep(3600)

# ------------------- Discord init -------------------
intents = c1c_make_intents()
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

# ------------------- Sheet utilities (robust reader) -------------------
def get_ws(sh, name: Optional[str]):
    """Return worksheet by name, case-insensitive. Uses DEFAULT_TAB if None/empty."""
    wanted = (name or DEFAULT_TAB).strip()
    try:
        return sh.worksheet(wanted)
    except gspread.WorksheetNotFound:
        lw = wanted.lower()
        for ws in sh.worksheets():
            if ws.title.lower() == lw:
                return ws
        raise gspread.WorksheetNotFound(wanted)

def _dedupe_headers(headers: List[str]) -> List[str]:
    """Trim, replace blanks with _colN, and dedupe by appending _2, _3…"""
    out, seen = [], {}
    for i, h in enumerate(headers, start=1):
        base = (h or "").strip()
        if base == "": base = f"_col{i}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        out.append(base if count == 0 else f"{base}_{count+1}")
    return out

def _looks_like_header(row: List[str]) -> bool:
    low = { (c or "").strip().lower() for c in row }
    keys = {"clantag","clan tag","clanname","clan name","tag","name","level","spots"}
    return any(k in low for k in keys)

def get_headers(ws) -> List[str]:
    vals = ws.get_all_values()
    if not vals: return []
    header_idx = 0
    scan_upto = min(20, len(vals))
    for i in range(scan_upto):
        if _looks_like_header(vals[i]):
            header_idx = i; break
    return _dedupe_headers(vals[header_idx])

def read_records(ws) -> List[Dict[str, str]]:
    """Robust rows: detect header, patch duplicates/blanks, map remaining rows."""
    vals = ws.get_all_values()
    if not vals: return []
    # find header row
    header_idx = 0
    scan_upto = min(20, len(vals))
    for i in range(scan_upto):
        if _looks_like_header(vals[i]):
            header_idx = i; break
    headers = _dedupe_headers(vals[header_idx])
    data_rows = vals[header_idx+1:]
    out = []
    for r in data_rows:
        if len(r) < len(headers): r = r + [""]*(len(headers)-len(r))
        elif len(r) > len(headers): r = r[:len(headers)]
        if not any(str(c).strip() for c in r):  # skip empty
            continue
        out.append({ headers[i]: r[i] for i in range(len(headers)) })
    return out

# ------------------- Flexible field matching -------------------
TAG_HINTS  = ("clantag", "clan tag", "tag", "abbr", "abbrev", "short", "ticker", "code", "id")
NAME_HINTS = ("clanname", "clan name", "name")  # avoid false positives with "tag"

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def tag_keys_in(headers: List[str]) -> List[str]:
    out=[]
    for h in headers:
        hl=h.strip().lower()
        if any(k in hl for k in TAG_HINTS):
            out.append(h)
    return out

def name_keys_in(headers: List[str]) -> List[str]:
    out=[]
    for h in headers:
        hl=h.strip().lower()
        if ("name" in hl) and ("tag" not in hl):
            out.append(h)
    return out

def find_row(rows: List[Dict[str,str]], headers: List[str], query: str) -> Optional[Dict[str,str]]:
    want = norm(query)
    tkeys = tag_keys_in(headers)
    nkeys = name_keys_in(headers)
    # 1) exact/normalized match on tag-like fields
    for r in rows:
        for k in tkeys:
            if norm(r.get(k,"")) == want:
                return r
    # 2) exact/normalized match on name-like fields
    for r in rows:
        for k in nkeys:
            if norm(r.get(k,"")) == want:
                return r
    # 3) fallback: substring match anywhere (normalized)
    for r in rows:
        for k in (tkeys + nkeys):
            if want and want in norm(r.get(k,"")):
                return r
    return None

# ------------------- Helpers for formatting -------------------
def _pick(d: dict, *names, default=None):
    for n in names:
        if n in d and str(d[n]).strip() != "":
            return d[n]
    return default

def _to_int_or_none(x):
    try:
        s = str(x).replace(",", "").strip()
        if s == "": return None
        return int(float(s))
    except:
        return None

def _plural(n, word): return f"{n} {word}" + ("" if n == 1 else "s")

def _boolish(val, default=False):
    s = str(val).strip().lower()
    if s in ("1","y","yes","true","open"): return True
    if s in ("0","n","no","false","closed"): return False
    return default

def format_entry_criteria_row(row: dict) -> str:
    """Robust 'Entry Criteria' builder supporting many header variants."""
    hydra_keys   = _to_int_or_none(_pick(row, "HydraKeys", "Hydra Keys", "Hydra Key", "Hydra keys"))
    hydra_target = _to_int_or_none(_pick(row, "HydraTargetM", "Hydra Target (M)", "Hydra Clash (M)", "Hydra Clash Target (M)", "Hydra Clash"))
    chim_keys    = _to_int_or_none(_pick(row, "ChimeraKeys", "Chimera Keys", "Chimera Key", "Chim keys"))
    chim_target  = _to_int_or_none(_pick(row, "ChimeraTargetM", "Chimera Target (M)", "Chimera Clash (M)", "Chimera Clash Target (M)", "Chimera Clash"))
    pr_min       = _to_int_or_none(_pick(row, "PR minimum", "PR Minimum", "PR Min", "PR_Min", "PRmin", "PR"))
    nonpr_min    = _to_int_or_none(_pick(row, "non PR minimum", "NonPR minimum", "Non PR Min", "NonPR Min", "NonPR", "nonPR", "NPR"))

    lines = []
    if hydra_keys is not None or hydra_target is not None:
        parts = []
        if hydra_keys is not None:   parts.append(f"{_plural(hydra_keys, 'key')}")
        if hydra_target is not None: parts.append(f"{hydra_target}M Hydra Clash")
        lines.append("Hydra: " + " — ".join(parts) if parts else "Hydra")
    if chim_keys is not None or chim_target is not None:
        parts = []
        if chim_keys is not None:    parts.append(f"{_plural(chim_keys, 'key')}")
        if chim_target is not None:  parts.append(f"{chim_target}M Chimera Clash")
        lines.append("Chimera: " + " — ".join(parts) if parts else "Chimera")
    if pr_min is not None or nonpr_min is not None:
        left = f"PR minimum: {pr_min}" if pr_min is not None else None
        right = f"non PR minimum: {nonpr_min}" if nonpr_min is not None else None
        both = " | ".join([x for x in (left, right) if x])
        if both:
            lines.append(f"CvC: {both}")

    return "**Entry Criteria:**\n" + ("\n".join(lines) if lines else "—")

def build_clan_embed(row: dict, guild: Optional[discord.Guild]=None) -> discord.Embed:
    tag   = str(_pick(row, "ClanTag", "Clan Tag", "Tag", default="")).strip()
    name  = str(_pick(row, "ClanName", "Clan Name", "Name", default=tag)).strip()
    level = _to_int_or_none(_pick(row, "Level", "Lvl", "Clan Level"))
    spots = _to_int_or_none(_pick(row, "Spots", "Open Spots", "Open", "OpenSlots", "Open spots"))
    title = f"{name}" + (f" | {tag}" if tag else "") + (f" | Level {level}" if level is not None else "") + (f" | Spots: {spots}" if spots is not None else "")

    desc_lines = [format_entry_criteria_row(row)]
    playstyle = _pick(row, "Playstyle", "Play Style")
    roster    = _pick(row, "Roster", "Roster Type", "Roster Policy")
    filters   = _pick(row, "Filters used", "Filters", "Filter")
    notes     = _pick(row, "Notes", "Note", "Extra", "Description")

    bullet = []
    if filters:   bullet.append(f"Filters used: {filters}")
    if playstyle: bullet.append(f"Playstyle: {playstyle}")
    if roster:    bullet.append(f"Roster: {roster}")
    if bullet:    desc_lines.append("\n".join(bullet))
    if notes:     desc_lines.append(notes)

    embed = discord.Embed(title=title, description="\n\n".join(desc_lines), color=discord.Color.blurple())

    thumb = str(_pick(row, "LogoUrl", "Logo", "ThumbUrl", "EmojiNameOrId", "Emoji", default="")).strip()
    if thumb:
        if thumb.startswith("http"):
            embed.set_thumbnail(url=thumb)
        elif guild:
            emo = None
            if thumb.isdigit():
                emo = discord.utils.get(guild.emojis, id=int(thumb))
            else:
                emo = discord.utils.find(lambda e: e.name.lower()==thumb.lower(), guild.emojis)
            if emo: embed.set_thumbnail(url=emo.url)

    embed.set_footer(text="React with 💡 for Clan Profile")
    return embed

# ------------------- Debug / utility commands -------------------
@bot.command(name="cmwhichsheet")
async def cmwhichsheet(ctx):
    try:
        sh = open_sheet_by_env()
        tabs = ", ".join(ws.title for ws in sh.worksheets()) or "—"
        await ctx.reply(f"✅ Connected to sheet `{sh.id}`.\nTabs: {tabs}\nDefault tab: `{DEFAULT_TAB}`")
    except Exception as e:
        await ctx.reply(f"❌ Sheet connect failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmheaders")
async def cmheaders(ctx, tab: Optional[str] = None):
    try:
        sh = open_sheet_by_env()
        ws = get_ws(sh, tab)
        headers = get_headers(ws)
        await ctx.reply("Headers in `" + ws.title + "`:\n" + ", ".join(headers))
    except Exception as e:
        await ctx.reply(f"❌ Headers failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmchecksheet")
async def cmchecksheet(ctx, tab: Optional[str] = None):
    try:
        sh = open_sheet_by_env()
        titles = [ws.title for ws in sh.worksheets()]
        try:
            ws = get_ws(sh, tab)
            rows = read_records(ws)
            return await ctx.reply(f"✅ Connected. Tab `{ws.title}` rows: **{len(rows)}**")
        except gspread.WorksheetNotFound:
            return await ctx.reply(f"⚠️ Connected, but tab `{tab or DEFAULT_TAB}` not found.\nAvailable: {', '.join(titles) or '—'}")
    except Exception as e:
        return await ctx.reply(f"❌ Matchmaker sheet check failed: `{type(e).__name__}: {e}`")

def _find_target_row(sh, tab, query) -> Optional[Dict[str,str]]:
    ws = get_ws(sh, tab)
    rows = read_records(ws)
    headers = list(rows[0].keys()) if rows else get_headers(ws)
    return find_row(rows, headers, query)

@bot.command(name="cmdump")
async def cmdump(ctx, clan: str, tab: Optional[str] = None):
    """Shows exactly what the bot reads from the sheet for one clan."""
    try:
        sh = open_sheet_by_env()
        target = _find_target_row(sh, tab, clan)
        if not target:
            ws = get_ws(sh, tab)
            hdr = get_headers(ws)
            return await ctx.reply(f"❓ No row for `{clan}` in tab `{ws.title}`.\n(Searching in: {', '.join(tag_keys_in(hdr)+name_keys_in(hdr)) or '—'})")
        pretty = json.dumps(target, indent=2, ensure_ascii=False)
        if len(pretty) > 1900: pretty = pretty[:1900] + "\n… (truncated)"
        await ctx.reply(f"```json\n{pretty}\n```")
    except Exception as e:
        await ctx.reply(f"❌ Dump failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmformat")
async def cmformat(ctx, clan: str, tab: Optional[str] = None):
    """Renders the Entry Criteria text for a clan using the safe formatter."""
    try:
        sh = open_sheet_by_env()
        target = _find_target_row(sh, tab, clan)
        if not target:
            ws = get_ws(sh, tab)
            hdr = get_headers(ws)
            return await ctx.reply(f"❓ No row for `{clan}` in tab `{ws.title}`.\n(Searching in: {', '.join(tag_keys_in(hdr)+name_keys_in(hdr)) or '—'})")
        text = format_entry_criteria_row(target)
        await ctx.reply(text)
    except Exception as e:
        await ctx.reply(f"❌ Format failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmsearch")
async def cmsearch(ctx, *, text: str):
    """Quick list (non-panel) search in DEFAULT_TAB."""
    try:
        sh = open_sheet_by_env()
        ws = get_ws(sh, None)
        rows = read_records(ws)
        headers = list(rows[0].keys()) if rows else get_headers(ws)
        tkeys, nkeys = tag_keys_in(headers), name_keys_in(headers)
        want = norm(text)
        hits = []
        for r in rows:
            cand = False
            for k in (tkeys + nkeys):
                val = str(r.get(k,"")).strip()
                if want in norm(val):
                    cand = True; break
            if cand:
                level = _to_int_or_none(_pick(r,"Level","Lvl","Clan Level"))
                spots = _to_int_or_none(_pick(r,"Spots","Open Spots","Open"))
                name  = _pick(r,"ClanName","Clan Name","Name", default="")
                tag   = _pick(r,"ClanTag","Clan Tag","Tag", default="")
                hits.append(f"{name or tag} ({tag or name}) · L{level or '?'} · spots {spots or '?'}")
        if not hits:
            return await ctx.reply("No matches.")
        await ctx.reply("\n".join(hits[:15]))
    except Exception as e:
        await ctx.reply(f"❌ Search failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmpost")
async def cmpost(ctx, clan: str, tab: Optional[str] = None):
    """Post one Matchmaker card for a given clan from the sheet."""
    try:
        sh = open_sheet_by_env()
        target = _find_target_row(sh, tab, clan)
        if not target:
            ws = get_ws(sh, tab)
            hdr = get_headers(ws)
            return await ctx.reply(f"❓ No row for `{clan}` in tab `{ws.title}`.\n(Searching in: {', '.join(tag_keys_in(hdr)+name_keys_in(hdr)) or '—'})")
        embed = build_clan_embed(target, ctx.guild)
        allow = discord.AllowedMentions(everyone=False, roles=False, users=False)
        msg = await ctx.send(embed=embed, allowed_mentions=allow)
        try: await msg.add_reaction("💡")
        except: pass
    except Exception as e:
        await ctx.reply(f"❌ Post failed: `{type(e).__name__}: {e}`")

# ==================== PANEL SEARCH / MATCH (restored UX) ====================
def _search_hits(sh, tab: Optional[str], text: str, limit: int = 50):
    """Return (ws, all_rows, matching_rows). If text is empty → show 'open' clans first."""
    ws = get_ws(sh, tab)
    rows = read_records(ws)
    if not rows:
        return ws, rows, []
    headers = list(rows[0].keys())
    tkeys, nkeys = tag_keys_in(headers), name_keys_in(headers)
    want = norm(text)

    hits: List[dict] = []
    if want:
        for r in rows:
            hay = False
            for k in (tkeys + nkeys):
                if want in norm(str(r.get(k, ""))):
                    hay = True; break
            if hay:
                hits.append(r)
                if len(hits) >= limit: break
    else:
        def is_open(r):
            spots = _to_int_or_none(_pick(r, "Spots", "Open Spots", "Open", "OpenSlots"))
            if spots is not None:
                return spots > 0
            return _boolish(_pick(r, "Open", "Status", "Recruitment", default="open"), default=True)
        for r in rows:
            if is_open(r):
                hits.append(r)
                if len(hits) >= limit: break
        if not hits:
            hits = rows[:min(limit, len(rows))]

    return ws, rows, hits

class ClanSearchView(discord.ui.View):
    """Interactive browser: Prev / Next / Post / Close."""
    def __init__(self, ctx: commands.Context, hits: List[dict]):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.hits = hits
        self.i = 0
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command author can use these controls.", ephemeral=True)
            return False
        return True

    def current(self) -> dict:
        return self.hits[self.i]

    async def refresh(self, interaction: Optional[discord.Interaction] = None):
        embed = build_clan_embed(self.current(), self.ctx.guild)
        pos = f"Result {self.i+1}/{len(self.hits)}"
        ft = (embed.footer.text if embed.footer else "") or ""
        embed.set_footer(text=(ft + (" • " if ft else "") + pos))
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.i = (self.i - 1) % len(self.hits)
        await self.refresh(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.i = (self.i + 1) % len(self.hits)
        await self.refresh(interaction)

    @discord.ui.button(label="📬 Post Here", style=discord.ButtonStyle.primary)
    async def post_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            emb = build_clan_embed(self.current(), self.ctx.guild)
            allow = discord.AllowedMentions(everyone=False, roles=False, users=False)
            await self.ctx.channel.send(embed=emb, allowed_mentions=allow)
            await interaction.response.send_message("Posted ✅", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Post failed: `{type(e).__name__}: {e}`", ephemeral=True)

    @discord.ui.button(label="✖ Close", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

@bot.command(name="clansearch")
async def clansearch_cmd(ctx: commands.Context, *, text: str = ""):
    """Open the interactive search panel. Empty text → show open clans."""
    try:
        sh = open_sheet_by_env()
        _ws, _rows, hits = _search_hits(sh, None, text, limit=50)
        if not hits:
            return await ctx.reply("No matches. Try a different tag/name.")
        view = ClanSearchView(ctx, hits)
        embed = build_clan_embed(hits[0], ctx.guild)
        msg = await ctx.send(content=(f"Results for **{text}**" if text else "Open clans"), embed=embed, view=view)
        view.message = msg
    except Exception as e:
        await ctx.reply(f"❌ Search failed: `{type(e).__name__}: {e}`")

@bot.command(name="clanmatch")
async def clanmatch_cmd(ctx: commands.Context, *, text: str = ""):
    """Legacy entry point. Same panel as !clansearch."""
    await clansearch_cmd(ctx, text=text)
# ================== end panel search / match block ==================

# ---------- Compat shims + help ----------
from discord.ext.commands import CommandNotFound

@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.reply(
        "**C1C Matchmaker — Commands**\n"
        "`!clan <tag|name>` → post one card (alias of `!cmpost`)\n"
        "`!clanmatch [text]` → interactive browser panel\n"
        "`!clansearch [text]` → interactive browser panel\n"
        "`!cmpost <tag|name> [tab]` · `!cmsearch <text>` (list)\n"
        "`!cmdump <tag|name> [tab]` · `!cmformat <tag|name> [tab]`\n"
        "`!cmwhichsheet` · `!cmchecksheet [tab]` · `!cmheaders [tab]`\n"
        f"(Default tab: `{DEFAULT_TAB}` — change via env `C1C_MATCH_TAB`)"
    )

@bot.command(name="clan")
async def clan_cmd(ctx, *, query: str = ""):
    if not query.strip():
        return await ctx.reply("Usage: `!clan <tag|name>` — posts one card. See `!help`.")
    await cmpost(ctx, query, None)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, CommandNotFound):
        return await ctx.reply("❓ Unknown command. Try `!help`.")
    try:
        await ctx.reply(f"⚠️ Command error: `{type(error).__name__}: {error}`")
    finally:
        log.exception("Command error", exc_info=error)
# ---------- end compat ----------

# ------------------- On Ready -------------------
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    log.info("Default data tab: %s", DEFAULT_TAB)

# ------------------- Startup -------------------
async def _main():
    await asyncio.gather(run_web(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    asyncio.run(_main())

