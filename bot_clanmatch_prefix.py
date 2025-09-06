# C1C Matchmaker — bot_clanmatch_prefix.py
# Single-file edition with harmonized helpers + debug/format tools.
# Env vars supported (preferred first):
#   GSHEET_ID | GOOGLE_SHEET_ID | CONFIG_SHEET_ID
#   GOOGLE_SERVICE_ACCOUNT_JSON | SERVICE_ACCOUNT_JSON
#   DISCORD_TOKEN

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
async def fmt_chan_or_thread(bot: _discord.Client, guild: _discord.Guild, target_id: int | None) -> str:
    if not target_id: return "—"
    obj = guild.get_channel(target_id) or await bot.fetch_channel(target_id)
    if not obj: return f"(unknown) `{target_id}`"
    mention = getattr(obj, "mention", f"<#{target_id}>")
    name = getattr(obj, "name", "unknown")
    return f"{mention} — **{name}** `{target_id}`"

# Sheets client (unified, modern google-auth)
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

# ------------------- Helpers for flexible sheets -------------------
def _pick(d: dict, *names, default=None):
    """Return first present, non-empty value by any of the provided names."""
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

def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")

def _boolish(val, default=False):
    s = str(val).strip().lower()
    if s in ("1","y","yes","true","open"): return True
    if s in ("0","n","no","false","closed"): return False
    return default

def format_entry_criteria_row(row: dict) -> str:
    """
    Builds the 'Entry Criteria' text robustly from many possible header names.
    Adjust the synonyms below if your sheet uses different labels.
    """
    hydra_keys   = _to_int_or_none(_pick(row, "HydraKeys", "Hydra Keys", "Hydra Key", "Hydra keys"))
    hydra_target = _to_int_or_none(_pick(row, "HydraTargetM", "Hydra Target (M)", "Hydra Clash (M)", "Hydra Clash Target (M)", "Hydra Clash"))
    chim_keys    = _to_int_or_none(_pick(row, "ChimeraKeys", "Chimera Keys", "Chimera Key", "Chim keys"))
    chim_target  = _to_int_or_none(_pick(row, "ChimeraTargetM", "Chimera Target (M)", "Chimera Clash (M)", "Chimera Clash Target (M)", "Chimera Clash"))
    pr_min       = _to_int_or_none(_pick(row, "PR minimum", "PR Minimum", "PR Min", "PR_Min", "PRmin", "PR"))
    nonpr_min    = _to_int_or_none(_pick(row, "non PR minimum", "NonPR minimum", "Non PR Min", "NonPR Min", "NonPR", "nonPR", "NPR"))

    lines = []
    if hydra_keys or hydra_target:
        parts = []
        if hydra_keys is not None:   parts.append(f"{_plural(hydra_keys, 'key')}")
        if hydra_target is not None: parts.append(f"{hydra_target}M Hydra Clash")
        lines.append("Hydra: " + " — ".join(parts) if parts else "Hydra")
    if chim_keys or chim_target:
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
    # Title parts
    tag   = str(_pick(row, "ClanTag", "Tag", "Clan Tag", default="")).strip()
    name  = str(_pick(row, "ClanName", "Name", "Clan Name", default=tag)).strip()
    level = _to_int_or_none(_pick(row, "Level", "Lvl", "Clan Level"))
    spots = _to_int_or_none(_pick(row, "Spots", "Open Spots", "Open", "OpenSlots", "Open spots"))
    title = f"{name}" + (f" | {tag}" if tag else "") + (f" | Level {level}" if level is not None else "") + (f" | Spots: {spots}" if spots is not None else "")

    # Description
    desc_lines = [format_entry_criteria_row(row)]
    # Extra filters / notes if present
    playstyle = _pick(row, "Playstyle", "Play Style")
    roster    = _pick(row, "Roster", "Roster Type", "Roster Policy")
    filters   = _pick(row, "Filters used", "Filters", "Filter")
    notes     = _pick(row, "Notes", "Note", "Extra", "Description")

    bullet = []
    if filters:   bullet.append(f"Filters used: {filters}")
    if playstyle: bullet.append(f"Playstyle: {playstyle}")
    if roster:    bullet.append(f"Roster: {roster}")
    if bullet:
        desc_lines.append("\n".join(bullet))
    if notes:
        desc_lines.append(notes)

    embed = discord.Embed(title=title, description="\n\n".join(desc_lines), color=discord.Color.blurple())

    # Thumbnail (emoji name/id or direct URL)
    thumb = str(_pick(row, "LogoUrl", "Logo", "ThumbUrl", "EmojiNameOrId", "Emoji", default="")).strip()
    if thumb:
        if thumb.startswith("http"):
            embed.set_thumbnail(url=thumb)
        elif guild:
            # try emoji as thumbnail if custom
            emo = None
            if thumb.isdigit():
                emo = discord.utils.get(guild.emojis, id=int(thumb))
            else:
                emo = discord.utils.find(lambda e: e.name.lower()==thumb.lower(), guild.emojis)
            if emo: embed.set_thumbnail(url=emo.url)

    # Footer hint
    embed.set_footer(text="React with 💡 for Clan Profile")
    return embed

# ------------------- Commands (debug + test poster) -------------------
@bot.command(name="cmwhichsheet")
async def cmwhichsheet(ctx):
    try:
        sh = open_sheet_by_env()
        tabs = ", ".join(ws.title for ws in sh.worksheets()) or "—"
        await ctx.reply(f"✅ Connected to sheet `{sh.id}`.\nTabs: {tabs}\nTip: `!cmdump <ClanTagOrName> [TabName]`")
    except Exception as e:
        await ctx.reply(f"❌ Sheet connect failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmchecksheet")
async def cmchecksheet(ctx, tab: str | None = None):
    try:
        sh = open_sheet_by_env()
        titles = [ws.title for ws in sh.worksheets()]
        if tab:
            try:
                ws = sh.worksheet(tab)
                rows = len(ws.get_all_values()) - 1
                return await ctx.reply(f"✅ Connected. Tab `{tab}` rows: **{rows}**")
            except gspread.WorksheetNotFound:
                return await ctx.reply(f"⚠️ Connected, but tab `{tab}` not found.\nAvailable: {', '.join(titles) or '—'}")
        return await ctx.reply(f"✅ Sheet OK (Matchmaker). Tabs: {', '.join(titles) or '—'}\nTip: `!cmchecksheet <TabName>` to check one.")
    except Exception as e:
        return await ctx.reply(f"❌ Matchmaker sheet check failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmdump")
async def cmdump(ctx, clan: str, tab: str = "CLANS"):
    """Shows exactly what the bot reads from the sheet for one clan."""
    try:
        sh = open_sheet_by_env()
        ws = sh.worksheet(tab)
        rows = ws.get_all_records()
        target = None
        want = clan.strip().lower()
        for r in rows:
            tag  = str(r.get("ClanTag", "")).strip().lower()
            name = str(r.get("ClanName", "")).strip().lower()
            if want in (tag, name):
                target = r; break
        if not target:
            return await ctx.reply(f"❓ No row for `{clan}` in tab `{tab}`.")
        pretty = json.dumps(target, indent=2, ensure_ascii=False)
        if len(pretty) > 1900:
            pretty = pretty[:1900] + "\n… (truncated)"
        await ctx.reply(f"```json\n{pretty}\n```")
    except gspread.WorksheetNotFound:
        await ctx.reply(f"⚠️ Tab `{tab}` not found.")
    except Exception as e:
        await ctx.reply(f"❌ Dump failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmformat")
async def cmformat(ctx, clan: str, tab: str = "CLANS"):
    """Renders the Entry Criteria text for a clan using the safe formatter."""
    try:
        sh = open_sheet_by_env()
        ws = sh.worksheet(tab)
        rows = ws.get_all_records()
        target = None
        want = clan.strip().lower()
        for r in rows:
            tag  = str(r.get("ClanTag", "")).strip().lower()
            name = str(r.get("ClanName", "")).strip().lower()
            if want in (tag, name):
                target = r; break
        if not target:
            return await ctx.reply(f"❓ No row for `{clan}` in tab `{tab}`.")
        text = format_entry_criteria_row(target)
        await ctx.reply(text)
    except Exception as e:
        await ctx.reply(f"❌ Format failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmsearch")
async def cmsearch(ctx, *, text: str):
    """Quick search by name or tag in CLANS tab."""
    try:
        sh = open_sheet_by_env()
        ws = sh.worksheet("CLANS")
        rows = ws.get_all_records()
        want = text.strip().lower()
        hits = []
        for r in rows:
            tag  = str(r.get("ClanTag","")).strip()
            name = str(r.get("ClanName","")).strip()
            if want in tag.lower() or want in name.lower():
                level = _to_int_or_none(_pick(r,"Level","Lvl"))
                spots = _to_int_or_none(_pick(r,"Spots","Open Spots","Open"))
                hits.append(f"{name} ({tag}) · L{level or '?'} · spots {spots or '?'}")
        if not hits:
            return await ctx.reply("No matches.")
        await ctx.reply("\n".join(hits[:15]))
    except Exception as e:
        await ctx.reply(f"❌ Search failed: `{type(e).__name__}: {e}`")

@bot.command(name="cmpost")
async def cmpost(ctx, clan: str, tab: str = "CLANS"):
    """Post one Matchmaker card for a given clan from the sheet."""
    try:
        sh = open_sheet_by_env()
        ws = sh.worksheet(tab)
        rows = ws.get_all_records()
        target = None
        want = clan.strip().lower()
        for r in rows:
            tag  = str(r.get("ClanTag", "")).strip().lower()
            name = str(r.get("ClanName", "")).strip().lower()
            if want in (tag, name):
                target = r; break
        if not target:
            return await ctx.reply(f"❓ No row for `{clan}` in tab `{tab}`.")
        embed = build_clan_embed(target, ctx.guild)
        allow = discord.AllowedMentions(everyone=False, roles=False, users=False)
        msg = await ctx.send(embed=embed, allowed_mentions=allow)
        try: await msg.add_reaction("💡")
        except: pass
    except Exception as e:
        await ctx.reply(f"❌ Post failed: `{type(e).__name__}: {e}`")

# ------------------- On Ready -------------------
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

# ------------------- Startup -------------------
async def _main():
    await asyncio.gather(run_web(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    asyncio.run(_main())
