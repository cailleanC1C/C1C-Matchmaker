# bot_clanmatch_prefix.py
# C1C Matchmaker — recruiter & user panels, strict GSheets, hybrid thread routing.
# Commands:
#   !clanmatch  -> recruiter panel (hybrid: per-recruiter thread if role; else fixed thread)
#   !clansearch -> user panel (in invoking channel)
#   !clan       -> clan profile (tag or name)
#   !ping, !health, !reload -> utility commands
#
# Behaviors:
#   - Recruiter Search posts ALL results in ONE message (chunks if >10 embeds)
#   - Reset also deletes the last results message(s)
#   - Expire/Close disables panel, wipes results, shows "Reload new search"
#   - Pointer: when !clanmatch panel appears in a thread, the invoking channel gets a link that self-deletes
#
# Requires: discord.py v2.x, aiohttp; gspread/google-auth for Google Sheets

import os
import json
import asyncio
import logging
from typing import List, Dict, Optional

import discord
from discord.ext import commands
from discord.ui import View, button, Button, Select
from discord import SelectOption
from aiohttp import web

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("c1c")

# ---------------- ENV ----------------
TOKEN = os.environ.get("DISCORD_TOKEN")

# Thread routing (for !clanmatch)
# Modes: off | fixed | per_recruiter | hybrid
PANEL_THREAD_MODE = os.environ.get("PANEL_THREAD_MODE", "hybrid").lower()
PANEL_FIXED_THREAD_ID = int(os.environ.get("PANEL_FIXED_THREAD_ID", "0"))  # fixed thread id
PANEL_PARENT_CHANNEL_ID = int(os.environ.get("PANEL_PARENT_CHANNEL_ID", "0"))  # parent channel for recruiter threads
RECRUITER_ROLE_IDS = tuple(int(x) for x in os.environ.get("RECRUITER_ROLE_IDS", "").split(",") if x.strip().isdigit())
PANEL_THREAD_ARCHIVE_MIN = int(os.environ.get("PANEL_THREAD_ARCHIVE_MIN", "1440"))  # 60|1440|4320|10080
PANEL_BOUNCE_DELETE_SECONDS = int(os.environ.get("PANEL_BOUNCE_DELETE_SECONDS", "600"))

# Panel timeout
PANEL_TIMEOUT_SECONDS = int(os.environ.get("PANEL_TIMEOUT_SECONDS", "600"))  # 10min default

# Google Sheets (strict; no fallback)
GSPREAD_CREDENTIALS = os.environ.get("GSPREAD_CREDENTIALS")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# Accept both new and old envs; default to your old tab
CLANS_WORKSHEET_NAME = (
    os.environ.get("CLANS_WORKSHEET_NAME")
    or os.environ.get("WORKSHEET_NAME")
    or "bot_info"
)
USE_GSHEETS = bool(GSPREAD_CREDENTIALS and GOOGLE_SHEET_ID)

# Render web server
WEB_PORT = int(os.environ.get("PORT", "10000"))

# ---------------- Discord Setup ----------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Keep strong refs to live views so GC never kills timeout callbacks
LIVE_VIEWS: dict[int, View] = {}

# ============================================================
#                       DATA ACCESS (STRICT)
# ============================================================

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

def _get(row: Dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and str(row[k]).strip():
            return str(row[k]).strip()
    return ""

def fetch_clans_raw() -> List[Dict]:
    """Google Sheets only, with diagnostic errors."""
    if not USE_GSHEETS:
        raise RuntimeError("DATA_SOURCE_MISCONFIGURED")
    try:
        import gspread
        from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError
        from google.oauth2.service_account import Credentials

        creds_info = json.loads(GSPREAD_CREDENTIALS)
        creds = Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)

        try:
            sh = client.open_by_key(GOOGLE_SHEET_ID)
        except SpreadsheetNotFound as e:
            raise RuntimeError("SHEET_NOT_FOUND") from e

        try:
            ws = sh.worksheet(CLANS_WORKSHEET_NAME)
        except WorksheetNotFound as e:
            raise RuntimeError(f"WORKSHEET_NOT_FOUND:{CLANS_WORKSHEET_NAME}") from e

        return ws.get_all_records()

    except json.JSONDecodeError as e:
        raise RuntimeError("CREDS_BAD_JSON") from e
    except APIError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403):
            raise RuntimeError("SHEET_NOT_SHARED") from e
        raise RuntimeError("GSHEETS_API_ERROR") from e
    except Exception as e:
        raise RuntimeError("DATA_SOURCE_UNAVAILABLE") from e

def friendly_data_error(code: str) -> str:
    if code == "DATA_SOURCE_MISCONFIGURED":
        return ("Data source not configured. Admins: set `GSPREAD_CREDENTIALS`, `GOOGLE_SHEET_ID`, "
                "and `CLANS_WORKSHEET_NAME` (or `WORKSHEET_NAME`).")
    if code.startswith("WORKSHEET_NOT_FOUND"):
        return f"Worksheet `{CLANS_WORKSHEET_NAME}` not found in the spreadsheet."
    if code == "SHEET_NOT_FOUND":
        return "Spreadsheet ID is invalid or inaccessible."
    if code == "SHEET_NOT_SHARED":
        return "Spreadsheet isn’t shared with my service account (`client_email` from GSPREAD_CREDENTIALS)."
    if code == "CREDS_BAD_JSON":
        return "Credentials JSON in `GSPREAD_CREDENTIALS` is invalid."
    return "Clan data source unavailable. Try later."

def filter_rows(rows: List[Dict], f: Dict) -> List[Dict]:
    out = []
    for r in rows:
        ok = True
        if f.get("clanboss") and _get(r, "ClanBoss", "CB", "Clan Boss").lower() != f["clanboss"].lower(): ok = False
        if f.get("hydra") and _get(r, "Hydra").lower() != f["hydra"].lower(): ok = False
        if f.get("chimera") and _get(r, "Chimera").lower() != f["chimera"].lower(): ok = False
        if f.get("playstyle") and f["playstyle"].lower() not in _get(r, "Playstyle").lower(): ok = False
        if f.get("cvc") and f["cvc"] != "—" and f["cvc"].lower() not in (_get(r, "CvC") or "").lower(): ok = False
        if f.get("siege") and f["siege"] != "—" and f["siege"].lower() not in (_get(r, "Siege") or "").lower(): ok = False
        if f.get("roster") and f["roster"] != "All" and f["roster"].lower() not in (_get(r, "Roster") or "").lower(): ok = False
        if ok: out.append(r)

    # fallback-wide if strict yielded none but filters exist
    if not out and any(v and v not in {"—","All"} for v in f.values()):
        needles = [v for v in f.values() if v and v not in {"—","All"}]
        for r in rows:
            if all(_match_row_any(r, n) for n in needles):
                out.append(r)
    return out

# ============================================================
#                         THREAD HELPERS
# ============================================================

async def _unarchive_if_needed(thread: discord.Thread) -> discord.Thread:
    try:
        if thread.archived:
            await thread.edit(archived=False, auto_archive_duration=PANEL_THREAD_ARCHIVE_MIN)
    except Exception:
        pass
    return thread

async def _find_archived_by_name(channel: discord.TextChannel, name: str) -> Optional[discord.Thread]:
    try:
        async for t in channel.archived_threads(limit=200, private=False):
            if t.name == name:
                return t
    except Exception:
        pass
    return None

def _is_recruiter(member: discord.Member) -> bool:
    if not RECRUITER_ROLE_IDS:
        return False
    ids = {r.id for r in member.roles or []}
    return any(rid in ids for rid in RECRUITER_ROLE_IDS)

async def _get_fixed_thread() -> Optional[discord.Thread]:
    if not PANEL_FIXED_THREAD_ID:
        return None
    ch = bot.get_channel(PANEL_FIXED_THREAD_ID)
    if isinstance(ch, discord.Thread):
        return await _unarchive_if_needed(ch)
    try:
        fetched = await bot.fetch_channel(PANEL_FIXED_THREAD_ID)
        if isinstance(fetched, discord.Thread):
            return await _unarchive_if_needed(fetched)
    except Exception:
        pass
    return None

async def _get_or_create_recruiter_thread(ctx: commands.Context) -> Optional[discord.Thread]:
    if not PANEL_PARENT_CHANNEL_ID:
        return None
    parent = bot.get_channel(PANEL_PARENT_CHANNEL_ID)
    if not isinstance(parent, discord.TextChannel):
        try:
            parent = await bot.fetch_channel(PANEL_PARENT_CHANNEL_ID)
        except Exception:
            return None
    if not isinstance(parent, discord.TextChannel):
        return None

    name = f"Matchmaker — {ctx.author.display_name} · {ctx.author.id}"
    # active list
    for t in parent.threads:
        if t.name == name:
            return await _unarchive_if_needed(t)
    # archived list
    t = await _find_archived_by_name(parent, name)
    if t:
        return await _unarchive_if_needed(t)
    # create new
    try:
        return await parent.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=PANEL_THREAD_ARCHIVE_MIN
        )
    except Exception:
        return None

async def _resolve_panel_target(ctx: commands.Context) -> discord.abc.Messageable:
    """Return where to send the !clanmatch panel."""
    mode = PANEL_THREAD_MODE
    if mode == "off":
        return ctx.channel
    if mode == "fixed":
        return (await _get_fixed_thread()) or ctx.channel
    if mode == "per_recruiter":
        if isinstance(ctx.author, discord.Member) and _is_recruiter(ctx.author):
            return (await _get_or_create_recruiter_thread(ctx)) or ctx.channel
        return ctx.channel
    # hybrid: recruiters -> per-recruiter thread; others -> fixed thread.
    if isinstance(ctx.author, discord.Member) and _is_recruiter(ctx.author):
        return (await _get_or_create_recruiter_thread(ctx)) or (await _get_fixed_thread()) or ctx.channel
    return (await _get_fixed_thread()) or ctx.channel

async def _delete_after(delay: int, message: Optional[discord.Message]):
    """Robust delayed delete with a quick retry."""
    if not message:
        return
    try:
        await asyncio.sleep(delay); await message.delete()
    except Exception:
        try:
            await asyncio.sleep(2); await message.delete()
        except Exception:
            pass

async def _pointer_and_cleanup(ctx: commands.Context, thread: discord.Thread):
    """Leave a self-deleting pointer in invoking channel and delete the command."""
    try:
        bounce = await ctx.reply(
            f"📎 Summoned matchmaking panel here → {thread.mention}\n"
            f"_This notice (and your command) will self-delete in {PANEL_BOUNCE_DELETE_SECONDS//60} min._",
            mention_author=True
        )
    except Exception:
        bounce = None
    asyncio.create_task(_delete_after(PANEL_BOUNCE_DELETE_SECONDS, bounce))
    asyncio.create_task(_delete_after(PANEL_BOUNCE_DELETE_SECONDS, ctx.message))

# ============================================================
#                        UI HELPERS
# ============================================================

def _mins_label(n: int) -> str:
    return f"{n} min" if n == 1 else f"{n} mins"

def _can_control(inter: discord.Interaction, owner_id: int) -> bool:
    if inter.user.id == owner_id:
        return True
    gp = inter.user.guild_permissions if inter.guild else None
    if gp and gp.administrator:
        return True
    if inter.guild and inter.guild.owner_id == inter.user.id:
        return True
    return False

def _results_embeds(results: List[Dict], label: str) -> List[discord.Embed]:
    if not results:
        e = discord.Embed(
            title="No clans found",
            description="Try different filters.",
            color=discord.Color.red()
        )
        if label:
            e.add_field(name="Filters", value=label, inline=False)
        return [e]

    def chunk(seq, n=25):
        for i in range(0, len(seq), n):
            yield seq[i:i+n]

    fields = []
    for i, r in enumerate(results, 1):
        name = _normalize(r.get("Name")) or "Unknown Clan"
        tag = _normalize(r.get("Tag"))
        cb = _get(r, "ClanBoss", "CB", "Clan Boss")
        hydra = _get(r, "Hydra")
        chim = _get(r, "Chimera")
        vibe = _get(r, "Vibe") or _get(r, "Playstyle")
        head = f"**{i}. {name}**" + (f" — `{tag}`" if tag else "")
        bits = []
        if cb: bits.append(f"**CB:** {cb}")
        if hydra: bits.append(f"**Hydra:** {hydra}")
        if chim: bits.append(f"**Chimera:** {chim}")
        if vibe: bits.append(f"**Vibe:** {vibe}")
        body = "\n".join(bits) if bits else "*No extra details provided.*"
        fields.append((head, body))

    pages = list(chunk(fields, 25))
    total = len(results)
    embeds = []
    for idx, group in enumerate(pages, 1):
        title = "Clan Match Results" + (f" (Page {idx}/{len(pages)})" if len(pages) > 1 else "")
        e = discord.Embed(
            title=title,
            description=f"Found **{total}** match(es)" + (f" • {label}" if label else ""),
            color=discord.Color.green()
        )
        for head, body in group:
            e.add_field(name=head, value=body, inline=False)
        embeds.append(e)
    return embeds

def _panel_embed(user: discord.abc.User, title: str) -> discord.Embed:
    mins = max(1, PANEL_TIMEOUT_SECONDS // 60)
    e = discord.Embed(
        title=title,
        description=(
            f"**{user.mention}** opened this panel.\n"
            "⚠️ **Only they (or an admin) can use it.** Not yours? Run the command yourself.\n\n"
            f"⏳ Auto-expires in {_mins_label(mins)} and will show **Reload new search**."
        ),
        color=discord.Color.blurple(),
    )
    e.set_footer(text="C1C · Match smart, play together")
    return e

# ============================================================
#                        PANEL VIEWS
# ============================================================

CB_LEVELS    = ["", "Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
HYDRA_LEVELS = ["", "Normal", "Hard", "Brutal", "Nightmare"]
CHIMERA_LVLS = ["", "Normal", "Hard", "Brutal"]
PLAYSTYLES   = ["", "Chill", "Semi-Competitive", "Competitive", "Teaching"]

class LabeledSelect(Select):
    def __init__(self, placeholder: str, options: List[str], state_attr: str):
        opts = [SelectOption(label=(opt if opt else "—"), value=opt) for opt in options]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=opts)
        self.state_attr = state_attr

    async def callback(self, inter: discord.Interaction):
        view: "BasePanelView" = self.view  # type: ignore
        if not _can_control(inter, view.owner_id):
            return await inter.response.send_message("Not allowed.", ephemeral=True)
        setattr(view, self.state_attr, self.values[0])
        await inter.response.defer()

def _cycle(cur: str, seq: List[str]) -> str:
    if cur not in seq: return seq[0]
    return seq[(seq.index(cur)+1) % len(seq)]

CVC_STATES    = ["—", "Low", "Medium", "High", "Tank"]
SIEGE_STATES  = ["—", "Wins", "Scores", "Defense", "Offense"]
ROSTER_STATES = ["All", "Midgame", "Endgame", "Mixed"]

class ReloadView(View):
    def __init__(self, owner_id: int, factory: type["BasePanelView"]):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.factory = factory
        self.message: Optional[discord.Message] = None

    @button(label="Reload new search", style=discord.ButtonStyle.primary)
    async def reload_btn(self, itx: discord.Interaction, _btn: Button):
        if itx.user.id != self.owner_id:
            return await itx.response.send_message("Not allowed.", ephemeral=True)
        new_view = self.factory(owner_id=self.owner_id, timeout=PANEL_TIMEOUT_SECONDS)
        try:
            await self.message.edit(embed=_panel_embed(itx.user, self.factory.TITLE), view=new_view)
            new_view.panel_message = self.message
            LIVE_VIEWS[self.message.id] = new_view  # keep alive
            await itx.response.send_message("New panel loaded.", ephemeral=True)
        except Exception:
            try: await itx.response.send_message("Couldn't reload here.", ephemeral=True)
            except Exception: pass

class BasePanelView(View):
    TITLE = "Panel"
    def __init__(self, owner_id: int, timeout: Optional[float]):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.panel_message: Optional[discord.Message] = None
        # track posted results so Reset/Expire can delete them
        self._results: List[discord.Message] = []

    async def _wipe_results(self):
        if self._results:
            for m in self._results:
                try: await m.delete()
                except Exception: pass
            self._results.clear()

    async def on_timeout(self):
        await self._wipe_results()
        if self.panel_message:
            # disable controls
            for c in self.children: c.disabled = True
            try: await self.panel_message.edit(view=self)
            except Exception: pass
            # swap to reload
            rv = ReloadView(self.owner_id, type(self)); rv.message = self.panel_message
            try:
                await self.panel_message.edit(
                    embed=discord.Embed(
                        title=self.TITLE,
                        description="⏳ Panel expired. 🧹 Cleared previous results.\nClick **Reload new search**.",
                        color=discord.Color.greyple()
                    ),
                    view=rv
                )
            finally:
                LIVE_VIEWS.pop(self.panel_message.id, None)

    @button(label="Close", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def btn_close(self, inter: discord.Interaction, _btn: Button):
        if not _can_control(inter, self.owner_id):
            return await inter.response.send_message("Not allowed.", ephemeral=True)
        await self._wipe_results()
        for c in self.children: c.disabled = True
        try: await inter.response.edit_message(view=self)
        except Exception: pass
        rv = ReloadView(self.owner_id, type(self)); rv.message = self.panel_message or inter.message
        try:
            await (self.panel_message or inter.message).edit(
                embed=discord.Embed(
                    title=self.TITLE,
                    description="Panel closed. 🧹 Cleared previous results.\nClick **Reload new search**.",
                    color=discord.Color.greyple()
                ),
                view=rv
            )
        finally:
            if self.panel_message:
                LIVE_VIEWS.pop(self.panel_message.id, None)

# Recruiter panel (with chips)
class RecruiterPanelView(BasePanelView):
    TITLE = "C1C-Matchmaker • Recruiter Panel"
    def __init__(self, owner_id: int, timeout: Optional[float]):
        super().__init__(owner_id, timeout)
        # state
        self.clanboss=""; self.hydra=""; self.chimera=""; self.playstyle=""
        self.cvc="—"; self.siege="—"; self.roster="All"
        # controls
        self.add_item(LabeledSelect("CB Difficulty (optional)", CB_LEVELS, "clanboss"))
        self.add_item(LabeledSelect("Hydra Difficulty (optional)", HYDRA_LEVELS, "hydra"))
        self.add_item(LabeledSelect("Chimera Difficulty (optional)", CHIMERA_LVLS, "chimera"))
        self.add_item(LabeledSelect("Playstyle (optional)", PLAYSTYLES, "playstyle"))
        self.btn_cvc   = Button(label=f"CvC: {self.cvc}",   style=discord.ButtonStyle.secondary)
        self.btn_siege = Button(label=f"Siege: {self.siege}", style=discord.ButtonStyle.secondary)
        self.btn_roster= Button(label=f"Roster: {self.roster}", style=discord.ButtonStyle.secondary)
        self.btn_reset = Button(label="Reset", style=discord.ButtonStyle.danger)
        self.btn_search= Button(label="Search Clans", style=discord.ButtonStyle.primary)
        for b in (self.btn_cvc,self.btn_siege,self.btn_roster,self.btn_reset,self.btn_search): self.add_item(b)

        async def _cvc(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            self.cvc=_cycle(self.cvc,CVC_STATES); self.btn_cvc.label=f"CvC: {self.cvc}"; await i.response.edit_message(view=self)
        async def _siege(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            self.siege=_cycle(self.siege,SIEGE_STATES); self.btn_siege.label=f"Siege: {self.siege}"; await i.response.edit_message(view=self)
        async def _roster(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            self.roster=_cycle(self.roster,ROSTER_STATES); self.btn_roster.label=f"Roster: {self.roster}"; await i.response.edit_message(view=self)

        async def _reset(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            await self._wipe_results()
            new = RecruiterPanelView(self.owner_id, self.timeout); new.panel_message=self.panel_message
            try: await i.response.edit_message(view=new)
            except discord.InteractionResponded: await i.edit_original_response(view=new)
            LIVE_VIEWS[self.panel_message.id] = new  # keep alive
            try: await i.followup.send("Panel reset & previous results removed.", ephemeral=True)
            except Exception: pass

        async def _search(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            await self._wipe_results()
            await i.response.defer(thinking=True)
            try: rows = fetch_clans_raw()
            except RuntimeError as err:
                return await i.followup.send(friendly_data_error(str(err)), ephemeral=True)

            filters={"clanboss":self.clanboss,"hydra":self.hydra,"chimera":self.chimera,
                     "playstyle":self.playstyle,"cvc":self.cvc,"siege":self.siege,"roster":self.roster}
            res = filter_rows(rows, filters)
            label = " • ".join([p for p in [
                f"CB={self.clanboss}" if self.clanboss else "",
                f"Hydra={self.hydra}" if self.hydra else "",
                f"Chimera={self.chimera}" if self.chimera else "",
                f"Style={self.playstyle}" if self.playstyle else "",
                f"CvC={self.cvc}" if self.cvc!="—" else "",
                f"Siege={self.siege}" if self.siege!="—" else "",
                f"Roster={self.roster}" if self.roster!="All" else ""
            ] if p])
            embeds = _results_embeds(res, label)

            # Batch embeds into as few messages as possible (Discord caps 10 embeds/message)
            CHUNK = 10
            for i0 in range(0, len(embeds), CHUNK):
                msg = await i.channel.send(embeds=embeds[i0:i0+CHUNK])
                self._results.append(msg)
            await i.followup.send(f"Posted **{len(res)}** result(s) in one message.", ephemeral=True)

        self.btn_cvc.callback=_cvc; self.btn_siege.callback=_siege; self.btn_roster.callback=_roster
        self.btn_reset.callback=_reset; self.btn_search.callback=_search

# User panel (simpler UI; also batches to one message for consistency)
class UserPanelView(BasePanelView):
    TITLE = "C1C-Matchmaker • User Panel"
    def __init__(self, owner_id: int, timeout: Optional[float]):
        super().__init__(owner_id, timeout)
        self.clanboss=""; self.hydra=""; self.chimera=""; self.playstyle=""
        self.add_item(LabeledSelect("CB Difficulty (optional)", CB_LEVELS, "clanboss"))
        self.add_item(LabeledSelect("Hydra Difficulty (optional)", HYDRA_LEVELS, "hydra"))
        self.add_item(LabeledSelect("Chimera Difficulty (optional)", CHIMERA_LVLS, "chimera"))
        self.add_item(LabeledSelect("Playstyle (optional)", PLAYSTYLES, "playstyle"))
        self.btn_reset = Button(label="Reset", style=discord.ButtonStyle.danger)
        self.btn_search= Button(label="Search Clans", style=discord.ButtonStyle.primary)
        self.add_item(self.btn_reset); self.add_item(self.btn_search)

        async def _reset(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            await self._wipe_results()
            new = UserPanelView(self.owner_id, self.timeout); new.panel_message=self.panel_message
            try: await i.response.edit_message(view=new)
            except discord.InteractionResponded: await i.edit_original_response(view=new)
            LIVE_VIEWS[self.panel_message.id] = new
            try: await i.followup.send("Panel reset & previous results removed.", ephemeral=True)
            except Exception: pass

        async def _search(i):
            if not _can_control(i, self.owner_id): return await i.response.send_message("Not allowed.",ephemeral=True)
            await self._wipe_results()
            await i.response.defer(thinking=True)
            try: rows = fetch_clans_raw()
            except RuntimeError as err:
                return await i.followup.send(friendly_data_error(str(err)), ephemeral=True)

            filters={"clanboss":self.clanboss,"hydra":self.hydra,"chimera":self.chimera,"playstyle":self.playstyle}
            res = filter_rows(rows, filters)
            label = " • ".join([p for p in [
                f"CB={self.clanboss}" if self.clanboss else "",
                f"Hydra={self.hydra}" if self.hydra else "",
                f"Chimera={self.chimera}" if self.chimera else "",
                f"Style={self.playstyle}" if self.playstyle else ""
            ] if p])
            embeds = _results_embeds(res, label)
            CHUNK = 10
            for i0 in range(0, len(embeds), CHUNK):
                msg = await i.channel.send(embeds=embeds[i0:i0+CHUNK])
                self._results.append(msg)
            await i.followup.send(f"Posted **{len(res)}** result(s) in one message.", ephemeral=True)

        self.btn_reset.callback=_reset; self.btn_search.callback=_search

# ============================================================
#                        COMMANDS
# ============================================================

@bot.command(name="clanmatch", help="Recruiter panel.")
async def clanmatch_cmd(ctx: commands.Context):
    target = await _resolve_panel_target(ctx)
    embed = _panel_embed(ctx.author, RecruiterPanelView.TITLE)
    view = RecruiterPanelView(owner_id=ctx.author.id, timeout=PANEL_TIMEOUT_SECONDS)
    msg = await target.send(embed=embed, view=view)
    view.panel_message = msg
    LIVE_VIEWS[msg.id] = view  # keep alive until timeout/close

    # pointer in invoking channel (only if we posted in a different thread)
    if isinstance(target, discord.Thread) and target.id != getattr(ctx.channel, "id", None):
        await _pointer_and_cleanup(ctx, target)
    else:
        # best effort: clean up the command anyway
        asyncio.create_task(_delete_after(PANEL_BOUNCE_DELETE_SECONDS, ctx.message))

@bot.command(name="clansearch", help="User panel.")
async def clansearch_cmd(ctx: commands.Context):
    embed = _panel_embed(ctx.author, UserPanelView.TITLE)
    view = UserPanelView(owner_id=ctx.author.id, timeout=PANEL_TIMEOUT_SECONDS)
    msg = await ctx.send(embed=embed, view=view)
    view.panel_message = msg
    LIVE_VIEWS[msg.id] = view

@bot.command(name="clan", help="Show clan profile by tag or name. Usage: !clan <tag|name>")
async def clan_cmd(ctx: commands.Context, *, ident: str):
    ident_l = ident.lower().strip()
    try:
        rows = fetch_clans_raw()
    except RuntimeError as err:
        return await ctx.send(friendly_data_error(str(err)))

    best = None
    for r in rows:
        if _get(r, "Tag").lower() == ident_l: best = r; break
    if not best:
        for r in rows:
            if _get(r, "Name").lower() == ident_l: best = r; break
    if not best:
        for r in rows:
            if ident_l in (_get(r,"Tag").lower()+" "+_get(r,"Name").lower()): best = r; break

    if not best:
        return await ctx.send(f"Couldn't find a clan for `{ident}`.")

    name = _get(best,"Name") or "Unknown Clan"
    tag  = _get(best,"Tag")
    e = discord.Embed(
        title=f"{name}" + (f" [{tag}]" if tag else ""),
        color=discord.Color.blue()
    )
    for k in ("ClanBoss","Hydra","Chimera","CvC","Siege","Roster","Playstyle","Vibe"):
        v = _get(best,k)
        if v: e.add_field(name=k, value=v, inline=True)
    await ctx.send(embed=e)

# ---------- Utility commands ----------
@bot.command(name="ping", help="Check bot latency.")
async def ping_cmd(ctx: commands.Context):
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")

@bot.command(name="health", help="Health probe.")
async def health_cmd(ctx: commands.Context):
    await ctx.send("ok")

@bot.command(name="reload", help="Reload clan list (admin only).")
@commands.has_permissions(administrator=True)
async def reload_cmd(ctx: commands.Context):
    # no caching yet; this confirms wiring
    await ctx.send("Reloaded.")

@bot.command(name="gsdiag", help="Admin-only: diagnose Google Sheets connectivity.")
@commands.has_permissions(administrator=True)
async def gsdiag_cmd(ctx: commands.Context):
    try:
        import gspread
        from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError
        from google.oauth2.service_account import Credentials
        import json

        # show what the bot *thinks* the worksheet name is
        worksheet_name = (
            os.environ.get("CLANS_WORKSHEET_NAME")
            or os.environ.get("WORKSHEET_NAME")
            or "bot_info"
        )

        creds_info = json.loads(os.environ["GSPREAD_CREDENTIALS"])
        client_email = creds_info.get("client_email", "<no-client-email>")
        creds = Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        gc = gspread.authorize(creds)

        sh = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
        try:
            ws = sh.worksheet(worksheet_name)
        except WorksheetNotFound:
            return await ctx.send(f"❌ Worksheet not found: `{worksheet_name}`. Tabs: {[w.title for w in sh.worksheets()]}")
        rows = ws.get_all_records()
        return await ctx.send(
            f"✅ Sheets OK. Worksheet=`{worksheet_name}`, rows={len(rows)}. "
            f"Service account={client_email}"
        )

    except json.JSONDecodeError as e:
        return await ctx.send("❌ CREDS_BAD_JSON: your GSPREAD_CREDENTIALS is not valid JSON.")
    except SpreadsheetNotFound as e:
        return await ctx.send("❌ SHEET_NOT_FOUND: bad GOOGLE_SHEET_ID or no access.")
    except APIError as e:
        status = getattr(getattr(e, 'response', None), 'status_code', None)
        if status in (401, 403):
            return await ctx.send("❌ SHEET_NOT_SHARED: share the sheet with the service account’s client_email.")
        return await ctx.send(f"❌ GSHEETS_API_ERROR: {e}")
    except Exception as e:
        # show exact exception class & msg so we’re not blind
        return await ctx.send(f"❌ DATA_SOURCE_UNAVAILABLE: {e.__class__.__name__}: {e}")


# ---------- Friendly error messages (UI-side) ----------
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    original = getattr(error, "original", error)

    if isinstance(error, commands.CommandNotFound):
        return await ctx.send("Unknown command. Try `!help`.")

    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name == "clan":
            return await ctx.send("Usage: `!clan <tag|name>`")
        return await ctx.send(f"Missing parameter. Usage: `!{ctx.command.qualified_name} {ctx.command.signature}`")

    if isinstance(error, commands.CheckFailure):
        return await ctx.send("You don't have permission to do that here.")

    if isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(f"Slow down. Try again in {error.retry_after:.1f}s.")

    if isinstance(original, discord.Forbidden):
        return await ctx.send(
            "I can’t do that here. Please give me **Send Messages**, **Embed Links**, "
            "**Send Messages in Threads**, **Create Public Threads**, and **Manage Messages**."
        )

    if isinstance(original, discord.HTTPException):
        return await ctx.send("Discord rejected that request. Try again or tweak your filters.")

    try:
        cmd_name = getattr(ctx.command, "qualified_name", "unknown")
        log.exception("Unhandled error in command %s", cmd_name, exc_info=original)
    except Exception:
        pass
    await ctx.send("Something went wrong. I’ve logged the details.")

# ============================================================
#                      BOT LIFECYCLE / WEB
# ============================================================

@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

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
    log.info("HTTP server listening on 0.0.0.0:%s", WEB_PORT)

async def main():
    await start_webserver()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN.")
    asyncio.run(main())
