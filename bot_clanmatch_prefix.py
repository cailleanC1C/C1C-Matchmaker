# bot_clanmatch_prefix.py
# C1C-Matchmaker — fixed-thread spawn (pointer + auto-delete), panel UI with dropdowns/chips,
# single-message results, Reset also deletes results, wipe-on-expire/close, Reload-on-expire,
# restored commands (!clansearch, !clanprofile/!clan, !ping, !health, !reload),
# optional Google Sheets search, and Render-compatible web binding.
#
# Requires: discord.py v2.x, aiohttp
# Optional: gspread (if using Google Sheets)

import os
import json
import asyncio
import logging
from typing import List, Dict, Optional, Sequence

import discord
from discord.ext import commands
from discord.ui import View, button, Button, Select, SelectOption, Modal, TextInput
from aiohttp import web

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("matchmaker")

# ---------------- ENV ----------------
TOKEN = os.environ.get("DISCORD_TOKEN")  # required

# Panel timeout (mins shown in UI)
PANEL_TIMEOUT_SECONDS = int(os.environ.get("PANEL_TIMEOUT_SECONDS", "600"))  # default 10 min

# THREADS: default "fixed" (spawn panel in a known thread)
# Values: off | fixed
PANEL_THREAD_MODE = os.environ.get("PANEL_THREAD_MODE", "fixed").lower()
PANEL_FIXED_THREAD_ID = int(os.environ.get("PANEL_FIXED_THREAD_ID", "0"))      # required when fixed
PANEL_BOUNCE_DELETE_SECONDS = int(os.environ.get("PANEL_BOUNCE_DELETE_SECONDS", "600"))  # pointer lifetime + command delete

# Google Sheets (optional; fallback to demo data if not set)
GSPREAD_CREDENTIALS = os.environ.get("GSPREAD_CREDENTIALS")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
CLANS_WORKSHEET_NAME = os.environ.get("CLANS_WORKSHEET_NAME", "Clans")
USE_GSHEETS = bool(GSPREAD_CREDENTIALS and GOOGLE_SHEET_ID)

# Render web server
WEB_PORT = int(os.environ.get("PORT", "10000"))

# ---------------- Discord Setup ----------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================================
#                       DATA ACCESS
# ============================================================

def _fallback_demo_data() -> List[Dict]:
    return [
        {"Name": "Martyrs", "Tag": "MTR", "ClanBoss": "UNM", "Hydra": "Hard", "Chimera": "Normal", "CvC": "High", "Siege": "Wins", "Roster": "All", "Playstyle": "Semi-Competitive", "Vibe": "Piratey, fun-first"},
        {"Name": "Vagrants", "Tag": "VAG", "ClanBoss": "NM", "Hydra": "Normal", "Chimera": "Normal", "CvC": "Medium", "Siege": "Solid", "Roster": "All", "Playstyle": "Chill", "Vibe": "Daily hitters"},
        {"Name": "Elders", "Tag": "ELD", "ClanBoss": "UNM", "Hydra": "Brutal", "Chimera": "Hard", "CvC": "High", "Siege": "Strong", "Roster": "Endgame", "Playstyle": "Competitive", "Vibe": "Kind tryhards"},
        {"Name": "Balors", "Tag": "BAL", "ClanBoss": "UNM", "Hydra": "Brutal", "Chimera": "Hard", "CvC": "High", "Siege": "Top", "Roster": "Endgame", "Playstyle": "Competitive", "Vibe": "Bring your best"},
        {"Name": "Island Sacred", "Tag": "ISL", "ClanBoss": "NM", "Hydra": "Normal", "Chimera": "Normal", "CvC": "Medium", "Siege": "Good", "Roster": "Mixed", "Playstyle": "Teaching", "Vibe": "Island vibes"},
    ]

def _normalize(s: Optional[str]) -> str:
    return (s or "").strip()

def _match_row_any(row: Dict[str, str], needle: str) -> bool:
    if not needle:
        return True
    n = needle.lower()
    for v in row.values():
        if n in str(v).lower():
            return True
    return False

def _safe_get(row: Dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and str(row[k]).strip():
            return str(row[k]).strip()
    return ""

def fetch_clans_raw() -> List[Dict]:
    if not USE_GSHEETS:
        return _fallback_demo_data()
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds_info = json.loads(GSPREAD_CREDENTIALS)
        creds = Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)
        ws = client.open_by_key(GOOGLE_SHEET_ID).worksheet(CLANS_WORKSHEET_NAME)
        return ws.get_all_records()
    except Exception as e:
        log.exception("Sheets read failed; using demo data: %s", e)
        return _fallback_demo_data()

def filter_rows(rows: List[Dict], f: Dict) -> List[Dict]:
    """
    Best-effort filter: tries explicit keys; otherwise falls back to substring search.
    f keys: clanboss, hydra, chimera, cvc, siege, roster, playstyle
    """
    out = []
    for r in rows:
        ok = True
        if f["clanboss"] and _safe_get(r, "ClanBoss", "CB", "Clan Boss").lower() != f["clanboss"].lower():
            ok = False
        if f["hydra"] and _safe_get(r, "Hydra").lower() != f["hydra"].lower():
            ok = False
        if f["chimera"] and _safe_get(r, "Chimera").lower() != f["chimera"].lower():
            ok = False
        if f["playstyle"] and f["playstyle"].lower() not in _safe_get(r, "Playstyle").lower():
            ok = False
        if f["cvc"] != "—" and f["cvc"].lower() not in (_safe_get(r, "CvC") or "").lower():
            ok = False
        if f["siege"] != "—" and f["siege"].lower() not in (_safe_get(r, "Siege") or "").lower():
            ok = False
        if f["roster"] != "All" and f["roster"].lower() not in (_safe_get(r, "Roster") or "").lower():
            ok = False
        if not ok:
            continue
        out.append(r)

    if not out and any(v and v not in {"—", "All"} for v in f.values()):
        needles = [v for v in f.values() if v and v not in {"—", "All"}]
        for r in rows:
            if all(_match_row_any(r, n) for n in needles):
                out.append(r)
    return out

# ============================================================
#                         THREAD HELPERS
# ============================================================

async def _get_fixed_thread(bot: commands.Bot, thread_id: int) -> Optional[discord.Thread]:
    if not thread_id:
        return None
    ch = bot.get_channel(thread_id)
    if isinstance(ch, discord.Thread):
        if ch.archived:
            try:
                await ch.edit(archived=False)
            except Exception:
                pass
        return ch
    try:
        fetched = await bot.fetch_channel(thread_id)
        if isinstance(fetched, discord.Thread):
            if fetched.archived:
                try:
                    await fetched.edit(archived=False)
                except Exception:
                    pass
            return fetched
    except Exception as e:
        log.warning("Fixed thread fetch failed (%s): %s", thread_id, e)
    return None

async def _post_pointer_and_cleanup(ctx: commands.Context, thread: discord.Thread):
    """Self-deleting pointer in invoking channel + delete the command after X seconds."""
    try:
        bounce = await ctx.reply(
            f"📎 Summoned matchmaking panel here → {thread.mention}\n"
            f"_This notice (and your command) will self-delete in {PANEL_BOUNCE_DELETE_SECONDS//60} min._",
            mention_author=True
        )
    except Exception:
        bounce = None

    async def _del(msg: Optional[discord.Message]):
        if not msg:
            return
        try:
            await asyncio.sleep(PANEL_BOUNCE_DELETE_SECONDS)
            await msg.delete()
        except Exception:
            pass

    asyncio.create_task(_del(bounce))
    asyncio.create_task(_del(ctx.message))

# ============================================================
#                        UI COMPONENTS
# ============================================================

def _mins_label(n: int) -> str:
    return f"{n} min" if n == 1 else f"{n} mins"

def build_panel_embed(user: discord.abc.User) -> discord.Embed:
    mins = max(1, PANEL_TIMEOUT_SECONDS // 60)
    e = discord.Embed(
        title="C1C-Matchmaker Panel",
        description=(
            f"**{user.mention}** has summoned C1C-Matchmaker.\n"
            "⚠️ **Only they can use this panel.** Not yours? Type **!clanmatch** to get your own.\n\n"
            "Use the selectors below to narrow your match. **Reset** clears the panel & last results. **Search Clans** posts results.\n"
            f"⏳ Panel auto-expires in {_mins_label(mins)} and will show a **Reload new search** button."
        ),
        color=discord.Color.blurple(),
    )
    e.set_footer(text="C1C · Match smart, play together")
    return e

def can_control(inter: discord.Interaction, owner_id: int) -> bool:
    if inter.user.id == owner_id:
        return True
    perms = inter.user.guild_permissions if inter.guild else None
    if perms and perms.administrator:
        return True
    if inter.guild and inter.guild.owner_id == inter.user.id:
        return True
    return False

# --- Dropdowns & chip buttons (match your screenshot flow) ---

CB_LEVELS = ["", "Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
HYDRA_LEVELS = ["", "Normal", "Hard", "Brutal", "Nightmare"]
CHIMERA_LEVELS = ["", "Normal", "Hard", "Brutal"]
PLAYSTYLES = ["", "Chill", "Semi-Competitive", "Competitive", "Teaching"]

class LabeledSelect(Select):
    def __init__(self, placeholder: str, options: List[str], state_attr: str):
        opts = [SelectOption(label=opt if opt else "—", value=opt) for opt in options]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=opts)
        self.state_attr = state_attr

    async def callback(self, inter: discord.Interaction):
        view: "MatchmakerView" = self.view  # type: ignore
        if not can_control(inter, view.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can use this panel.", ephemeral=True)
        setattr(view, self.state_attr, self.values[0])
        await inter.response.defer()

def _cycle(current: str, sequence: List[str]) -> str:
    if current not in sequence:
        return sequence[0]
    i = sequence.index(current)
    return sequence[(i + 1) % len(sequence)]

CVC_STATES = ["—", "Low", "Medium", "High", "Tank"]
SIEGE_STATES = ["—", "Wins", "Scores", "Defense", "Offense"]
ROSTER_STATES = ["All", "Midgame", "Endgame", "Mixed"]

def render_results_to_embeds(results: List[Dict], label: str) -> List[discord.Embed]:
    if not results:
        e = discord.Embed(
            title="No clans found",
            description="Try a broader filter, or different options.",
            color=discord.Color.red()
        )
        if label:
            e.add_field(name="Filters", value=label, inline=False)
        return [e]

    lines = []
    for i, r in enumerate(results, start=1):
        name = _normalize(r.get("Name")) or "Unknown Clan"
        tag = _normalize(r.get("Tag"))
        cb = _safe_get(r, "ClanBoss", "CB", "Clan Boss")
        hydra = _safe_get(r, "Hydra")
        chim = _safe_get(r, "Chimera")
        vibe = _safe_get(r, "Vibe") or _safe_get(r, "Playstyle")
        head = f"**{i}. {name}**" + (f" — `{tag}`" if tag else "")
        bits = []
        if cb:    bits.append(f"**CB:** {cb}")
        if hydra: bits.append(f"**Hydra:** {hydra}")
        if chim:  bits.append(f"**Chimera:** {chim}")
        if vibe:  bits.append(f"**Vibe:** {vibe}")
        body = "\n".join(bits) if bits else "*No extra details provided.*"
        lines.append((head, body))

    # Split fields across embeds (<=25 per embed)
    def chunk(seq, n=25):
        for i in range(0, len(seq), n):
            yield seq[i:i+n]

    embeds: List[discord.Embed] = []
    chunks = list(chunk(lines, 25))
    total = len(results)
    for idx, group in enumerate(chunks, start=1):
        title = "Clan Match Results" + (f" (Page {idx}/{len(chunks)})" if len(chunks) > 1 else "")
        e = discord.Embed(
            title=title,
            description=(f"Found **{total}** match(es)" + (f" • {label}" if label else "")),
            color=discord.Color.green(),
        )
        for head, body in group:
            e.add_field(name=head, value=body, inline=False)
        embeds.append(e)
    return embeds

# ============================================================
#                           VIEW
# ============================================================

class ReloadView(View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    @button(label="Reload new search", style=discord.ButtonStyle.primary)
    async def reload(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can reload this panel.", ephemeral=True)
        new_view = MatchmakerView(owner_id=self.owner_id, timeout=PANEL_TIMEOUT_SECONDS)
        embed = build_panel_embed(inter.user if inter.user.id == self.owner_id else inter.client.user)
        try:
            if self.message:
                await self.message.edit(embed=embed, view=new_view)
                new_view.panel_message = self.message
                await inter.response.send_message("New panel loaded.", ephemeral=True)
            else:
                msg = await inter.channel.send(embed=embed, view=new_view)
                new_view.panel_message = msg
                await inter.response.send_message("New panel loaded.", ephemeral=True)
        except Exception as e:
            log.exception("Reload failed: %s", e)
            await inter.response.send_message("Couldn't reload panel (missing perms?).", ephemeral=True)

class MatchmakerView(View):
    """
    The panel UI: dropdowns + chips + Reset + Search Clans.
    Behavior:
      - Search posts ALL results in one message (multi-embed ok).
      - Reset clears UI state AND deletes the previous results message.
      - on_timeout / Close wipes results, disables panel, and shows Reload button.
    """

    def __init__(self, owner_id: int, timeout: Optional[float] = PANEL_TIMEOUT_SECONDS):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.panel_message: Optional[discord.Message] = None
        self.results_message: Optional[discord.Message] = None

        # State
        self.clanboss: str = ""
        self.hydra: str = ""
        self.chimera: str = ""
        self.playstyle: str = ""
        self.cvc: str = "—"
        self.siege: str = "—"
        self.roster: str = "All"

        # Controls
        self.add_item(LabeledSelect("Clan Boss", CB_LEVELS, "clanboss"))
        self.add_item(LabeledSelect("Hydra", HYDRA_LEVELS, "hydra"))
        self.add_item(LabeledSelect("Chimera", CHIMERA_LEVELS, "chimera"))
        self.add_item(LabeledSelect("Playstyle (optional)", PLAYSTYLES, "playstyle"))

        # Chip row
        self.btn_cvc = Button(label=f"CvC: {self.cvc}", style=discord.ButtonStyle.secondary)
        self.btn_siege = Button(label=f"Siege: {self.siege}", style=discord.ButtonStyle.secondary)
        self.btn_roster = Button(label=f"Roster: {self.roster}", style=discord.ButtonStyle.secondary)
        self.btn_reset = Button(label="Reset", style=discord.ButtonStyle.danger)
        self.btn_search = Button(label="Search Clans", style=discord.ButtonStyle.primary)

        self.btn_cvc.callback = self._cb_cvc
        self.btn_siege.callback = self._cb_siege
        self.btn_roster.callback = self._cb_roster
        self.btn_reset.callback = self._cb_reset
        self.btn_search.callback = self._cb_search

        self.add_item(self.btn_cvc)
        self.add_item(self.btn_siege)
        self.add_item(self.btn_roster)
        self.add_item(self.btn_reset)
        self.add_item(self.btn_search)

    # ----- Chip handlers -----

    async def _cb_cvc(self, inter: discord.Interaction):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can use this panel.", ephemeral=True)
        self.cvc = _cycle(self.cvc, CVC_STATES)
        self.btn_cvc.label = f"CvC: {self.cvc}"
        await inter.response.edit_message(view=self)

    async def _cb_siege(self, inter: discord.Interaction):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can use this panel.", ephemeral=True)
        self.siege = _cycle(self.siege, SIEGE_STATES)
        self.btn_siege.label = f"Siege: {self.siege}"
        await inter.response.edit_message(view=self)

    async def _cb_roster(self, inter: discord.Interaction):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can use this panel.", ephemeral=True)
        self.roster = _cycle(self.roster, ROSTER_STATES)
        self.btn_roster.label = f"Roster: {self.roster}"
        await inter.response.edit_message(view=self)

    # ----- Reset / Search -----

    async def _cb_reset(self, inter: discord.Interaction):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can reset this panel.", ephemeral=True)

        await self._wipe_results()

        # Rebuild a fresh view to visually reset dropdowns/chips
        new_view = MatchmakerView(owner_id=self.owner_id, timeout=self.timeout)
        new_view.panel_message = self.panel_message
        try:
            await inter.response.edit_message(view=new_view)
        except discord.InteractionResponded:
            await inter.edit_original_response(view=new_view)
        try:
            await inter.followup.send("Panel reset and previous results removed.", ephemeral=True)
        except Exception:
            pass

    async def _cb_search(self, inter: discord.Interaction):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can search.", ephemeral=True)

        await self._wipe_results()
        await inter.response.defer(thinking=True)

        rows = fetch_clans_raw()
        filters = {
            "clanboss": self.clanboss,
            "hydra": self.hydra,
            "chimera": self.chimera,
            "playstyle": self.playstyle,
            "cvc": self.cvc,
            "siege": self.siege,
            "roster": self.roster,
        }
        results = filter_rows(rows, filters)
        label = self._filters_as_string()
        embeds = render_results_to_embeds(results, label)

        try:
            msg = await inter.channel.send(embeds=embeds)
            self.results_message = msg
            await inter.followup.send(f"Posted **{len(results)}** result(s) in a single message.", ephemeral=True)
        except Exception as e:
            log.exception("Failed to post results: %s", e)
            await inter.followup.send("Couldn't post results (missing perms?).", ephemeral=True)

    def _filters_as_string(self) -> str:
        parts = []
        if self.clanboss: parts.append(f"CB={self.clanboss}")
        if self.hydra: parts.append(f"Hydra={self.hydra}")
        if self.chimera: parts.append(f"Chimera={self.chimera}")
        if self.playstyle: parts.append(f"Style={self.playstyle}")
        if self.cvc != "—": parts.append(f"CvC={self.cvc}")
        if self.siege != "—": parts.append(f"Siege={self.siege}")
        if self.roster != "All": parts.append(f"Roster={self.roster}")
        return " • ".join(parts)

    # ----- Expiry Handling -----

    async def on_timeout(self):
        await self._wipe_results()
        try:
            if self.panel_message:
                for child in self.children:
                    child.disabled = True
                await self.panel_message.edit(view=self)

                reload_view = ReloadView(owner_id=self.owner_id)
                reload_view.message = self.panel_message
                expired = discord.Embed(
                    title="C1C-Matchmaker Panel",
                    description=(
                        "⏳ This panel expired.\n"
                        "🧹 Previous search results were **cleared**.\n\n"
                        "Click **Reload new search** to start fresh."
                    ),
                    color=discord.Color.greyple()
                )
                await self.panel_message.edit(embed=expired, view=reload_view)
        except Exception as e:
            log.debug("on_timeout edit failed: %s", e)

    async def _expire_to_reload(self, inter: discord.Interaction):
        await self._wipe_results()
        try:
            for child in self.children:
                child.disabled = True
            await inter.response.edit_message(view=self)

            reload_view = ReloadView(owner_id=self.owner_id)
            reload_view.message = self.panel_message or inter.message
            expired = discord.Embed(
                title="C1C-Matchmaker Panel",
                description=(
                    "Panel closed by a controller.\n"
                    "🧹 Previous search results were **cleared**.\n\n"
                    "Click **Reload new search** to start fresh."
                ),
                color=discord.Color.greyple()
            )
            await (self.panel_message or inter.message).edit(embed=expired, view=reload_view)
        except Exception as e:
            log.exception("Failed to close panel: %s", e)
            try:
                await inter.followup.send("Couldn't close panel (missing perms?).", ephemeral=True)
            except Exception:
                pass

    async def _wipe_results(self):
        if self.results_message:
            try:
                await self.results_message.delete()
            except Exception:
                pass
            self.results_message = None

    # public close button (optional; uncomment if you want it visible)
    @button(label="Close", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def btn_close(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message("Only the original summoner or an admin can close this panel.", ephemeral=True)
        await self._expire_to_reload(inter)

# ============================================================
#                        COMMANDS (RESTORED)
# ============================================================

@bot.command(name="clanmatch", help="Summon the C1C-Matchmaker panel.")
async def clanmatch(ctx: commands.Context):
    # Decide where to post (fixed thread by default)
    target = ctx.channel
    if PANEL_THREAD_MODE == "fixed":
        t = await _get_fixed_thread(bot, PANEL_FIXED_THREAD_ID)
        if t:
            target = t

    embed = build_panel_embed(ctx.author)
    view = MatchmakerView(owner_id=ctx.author.id, timeout=PANEL_TIMEOUT_SECONDS)
    msg = await target.send(embed=embed, view=view)
    view.panel_message = msg

    # If we posted in a thread, drop a pointer in the invoking channel and clean it + the command up later
    if isinstance(target, discord.Thread) and target.id != getattr(ctx.channel, "id", None):
        await _post_pointer_and_cleanup(ctx, target)

    try:
        await ctx.message.add_reaction("🔎")
    except Exception:
        pass

@bot.command(name="clansearch", help="Quick text search for clans. Usage: !clansearch <text>")
async def clansearch(ctx: commands.Context, *, query: str = ""):
    rows = fetch_clans_raw()
    if not query:
        # show a handful
        results = rows[:10]
        qlabel = ""
    else:
        results = [r for r in rows if _match_row_any(r, query)][:25]
        qlabel = f"query='{query}'"
    embeds = render_results_to_embeds(results, qlabel)
    await ctx.send(embeds=embeds)

@bot.command(name="clanprofile", aliases=["clan"], help="Show one clan’s profile by name or tag. Usage: !clan <tag|name>")
async def clanprofile(ctx: commands.Context, *, ident: str):
    rows = fetch_clans_raw()
    ident_l = ident.lower().strip()
    best = None

    # tag exact
    for r in rows:
        if _safe_get(r, "Tag").lower() == ident_l:
            best = r; break
    # name exact
    if not best:
        for r in rows:
            if _safe_get(r, "Name").lower() == ident_l:
                best = r; break
    # fuzzy contains
    if not best:
        for r in rows:
            if ident_l in (_safe_get(r, "Tag").lower() + " " + _safe_get(r, "Name").lower()):
                best = r; break

    if not best:
        return await ctx.send(f"Couldn't find a clan for `{ident}`.")

    name = _safe_get(best, "Name") or "Unknown Clan"
    tag = _safe_get(best, "Tag")
    e = discord.Embed(
        title=f"{name}" + (f" [{tag}]" if tag else ""),
        color=discord.Color.blue()
    )
    # dump known fields nicely
    for k in ("ClanBoss","Hydra","Chimera","CvC","Siege","Roster","Playstyle","Vibe"):
        v = _safe_get(best, k)
        if v:
            e.add_field(name=k, value=v, inline=True)
    await ctx.send(embed=e)

@bot.command(name="ping", help="Check bot latency.")
async def ping(ctx: commands.Context):
    await ctx.send(f"Pong! {round(bot.latency*1000)}ms")

@bot.command(name="health", help="Health probe.")
async def health(ctx: commands.Context):
    await ctx.send("ok")

@bot.command(name="reload", help="Reload the clan list from source (admin only).")
@commands.has_permissions(administrator=True)
async def reload_cmd(ctx: commands.Context):
    # No cache kept here, but this gives you a place to wire future caches.
    await ctx.send("Reloaded data.")

# ============================================================
#                      BOT LIFECYCLE / WEB
# ============================================================

@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

# ---------- Render Web Server (keeps Web Service happy) ----------
async def _health(_):
    return web.Response(text="ok")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/healthz", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info("HTTP server listening on 0.0.0.0:%s (Render health)", WEB_PORT)

# ---------- Main ----------
async def main():
    await start_webserver()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in env.")
    asyncio.run(main())
