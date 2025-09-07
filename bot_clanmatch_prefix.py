# bot_clanmatch_prefix.py
# C1C-Matchmaker — panels, search, profiles, emoji padding, and reaction flip (💡)

import os, json, time, asyncio, re, traceback, urllib.parse, io
from collections import defaultdict

import discord
from discord.ext import commands
from discord import InteractionResponded
from discord.utils import get

import gspread
from google.oauth2.service_account import Credentials

from aiohttp import web, ClientSession
from PIL import Image  # Pillow

# ------------------- boot/uptime helpers -------------------
START_TS = time.time()

def _fmt_uptime():
    secs = int(time.time() - START_TS)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ------------------- ENV -------------------
CREDS_JSON = os.environ.get("GSPREAD_CREDENTIALS")
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "bot_info")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Public base URL for proxying padded emoji images
BASE_URL = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")

# Padded-emoji tunables
EMOJI_PAD_SIZE = int(os.environ.get("EMOJI_PAD_SIZE", "256"))   # canvas px
EMOJI_PAD_BOX  = float(os.environ.get("EMOJI_PAD_BOX", "0.85")) # glyph fill (0..1)
STRICT_EMOJI_PROXY = os.environ.get("STRICT_EMOJI_PROXY", "1") == "1"  # if True: no raw fallback

if not CREDS_JSON:
    print("[boot] GSPREAD_CREDENTIALS missing", flush=True)
if not SHEET_ID:
    print("[boot] GOOGLE_SHEET_ID missing", flush=True)
print(f"[boot] WORKSHEET_NAME={WORKSHEET_NAME}", flush=True)
print(f"[boot] BASE_URL={BASE_URL}", flush=True)

# ------------------- Discord bot (define BEFORE decorators) -------------------
intents = discord.Intents.all()
intents.message_content = True  # must also be enabled in the Dev Portal for the bot
bot = commands.Bot(command_prefix="!", intents=intents)
LAST_CALL = defaultdict(float)
ACTIVE_PANELS: dict[tuple[int,str], int] = {}  # (user_id, variant) -> message_id
COOLDOWN_SEC = 2.0

@bot.command(name="ping")
async def ping_cmd(ctx: commands.Context):
    beat = round(bot.latency * 1000)
    secs = int(time.time() - START_TS)
    uptime = f"{secs//3600:02d}:{(secs%3600)//60:02d}:{secs%60:02d}"
    await ctx.reply(f"Pong! `{beat}ms` • up `{uptime}`", mention_author=False)

@bot.command(name="health")
async def health_cmd(ctx: commands.Context):
    try:
        ws = get_ws(False); _ = ws.row_values(1)
        sheets_status = f"OK (`{WORKSHEET_NAME}`)"
    except Exception as e:
        sheets_status = f"ERROR: {e}"
    secs = int(time.time() - START_TS)
    uptime = f"{secs//3600:02d}:{(secs%3600)//60:02d}:{secs%60:02d}"
    await ctx.reply(f"Matchmaker up `{uptime}` • Sheets: {sheets_status}", mention_author=False)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"[ready] Logged in as {bot.user} • slash synced: {len(synced)} • guilds: {len(bot.guilds)}", flush=True)
    except Exception as e:
        print(f"[ready] slash sync error: {e}", flush=True)
    print(f"[ready] intents.message_content={bot.intents.message_content}", flush=True)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    try:
        await ctx.reply(f"⚠️ {type(error).__name__}: {error}", mention_author=False)
    except Exception:
        pass
    traceback.print_exception(type(error), error, error.__traceback__)

# ------------------- Sheets (lazy + cache) -------------------
_gc = None
_ws = None
_cache_rows = None
_cache_time = 0.0
CACHE_TTL = 60  # seconds

def get_ws(force: bool = False):
    """Connect to Google Sheets only when needed."""
    global _gc, _ws
    if force:
        _ws = None
    if _ws is not None:
        return _ws
    creds = Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)
    _gc = gspread.authorize(creds)
    _ws = _gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
    print("[sheets] Connected to worksheet OK", flush=True)
    return _ws

def get_rows(force: bool = False):
    """Return all rows with simple 60s cache."""
    global _cache_rows, _cache_time
    if force or _cache_rows is None or (time.time() - _cache_time) > CACHE_TTL:
        ws = get_ws(False)
        _cache_rows = ws.get_all_values()
        _cache_time = time.time()
    return _cache_rows

def clear_cache():
    global _cache_rows, _cache_time, _ws
    _cache_rows = None
    _cache_time = 0.0
    _ws = None  # reconnect next time

# ------------------- Column map (0-based) -------------------
COL_A_RANK, COL_B_CLAN, COL_C_TAG, COL_D_LEVEL, COL_E_SPOTS = 0, 1, 2, 3, 4
COL_F_PROGRESSION, COL_G_LEAD, COL_H_DEPUTIES = 5, 6, 7
COL_I_CVC_TIER, COL_J_CVC_WINS, COL_K_SIEGE_TIER, COL_L_SIEGE_WINS = 8, 9, 10, 11
COL_M_CB, COL_N_HYDRA, COL_O_CHIMERA = 12, 13, 14  # ranges text (not filters)

# Filters P–U
COL_P_CB, COL_Q_HYDRA, COL_R_CHIM, COL_S_CVC, COL_T_SIEGE, COL_U_STYLE = 15, 16, 17, 18, 19, 20

# Entry Criteria V–AB
IDX_V, IDX_W, IDX_X, IDX_Y, IDX_Z, IDX_AA, IDX_AB = 21, 22, 23, 24, 25, 26, 27

# AC / AD / AE add-ons
IDX_AC_RESERVED, IDX_AD_COMMENTS, IDX_AE_REQUIREMENTS = 28, 29, 30

# ------------------- Helpers -------------------
def norm(s: str) -> str:
    return (s or "").strip().upper()

def is_header_row(row) -> bool:
    """Detect and ignore header/label rows that look like CLAN/TAG/Spots."""
    b = norm(row[COL_B_CLAN]) if len(row) > COL_B_CLAN else ""
    c = norm(row[COL_C_TAG])  if len(row) > COL_C_TAG  else ""
    e = norm(row[COL_E_SPOTS]) if len(row) > COL_E_SPOTS else ""
    return b in {"CLAN", "CLAN NAME"} or c == "TAG" or e == "SPOTS"

TOKEN_MAP = {
    "EASY":"ESY","NORMAL":"NML","HARD":"HRD","BRUTAL":"BTL","NM":"NM","UNM":"UNM","ULTRA-NIGHTMARE":"UNM"
}
def map_token(cell: str) -> list[str]:
    s = (cell or "").upper()
    if not s:
        return []
    toks = re.split(r"[,\s/;|]+", s)
    out = []
    for t in toks:
        t = t.strip()
        if not t:
            continue
        out.append(TOKEN_MAP.get(t, t))
    return out

def cell_has_diff(cell: str, want: str | None) -> bool:
    if not want:
        return True
    return want in map_token(cell)

def cell_equals01(cell: str, want01: int | None) -> bool:
    if want01 is None:
        return True
    try:
        return int(str(cell).strip()[:1]) == int(want01)
    except Exception:
        return False

def parse_spots(cell_text: str) -> int:
    m = re.search(r"\d+", cell_text or "")
    return int(m.group()) if m else 0

def row_matches(row, cb, hydra, chimera, cvc, siege, playstyle) -> bool:
    if len(row) <= IDX_AB:
        return False
    if is_header_row(row):
        return False
    if not (row[COL_B_CLAN] or "").strip():
        return False
    return (
        cell_has_diff(row[COL_P_CB], cb) and
        cell_has_diff(row[COL_Q_HYDRA], hydra) and
        cell_has_diff(row[COL_R_CHIM], chimera) and
        cell_equals01(row[COL_S_CVC], cvc) and
        cell_equals01(row[COL_T_SIEGE], siege) and
        (not playstyle or playstyle.strip().lower() in (row[COL_U_STYLE] or "").lower())
    )

def emoji_for_tag(guild: discord.Guild | None, tag: str | None) -> discord.Emoji | None:
    """Return the Discord emoji object for tag (or None)."""
    if not guild or not tag:
        return None
    return get(guild.emojis, name=tag.strip())

# ----- padded emoji URL helper (proxy only) -----
def padded_emoji_url(guild: discord.Guild | None, tag: str | None, size: int | None = None, box: float | None = None) -> str | None:
    """
    Build a URL to our /emoji-pad proxy that fetches the discord emoji, trims transparent
    borders, pads into a square with consistent margins, and returns a PNG.
    """
    if not guild or not tag:
        return None
    emj = emoji_for_tag(guild, tag)
    if not emj:
        return None
    src  = str(emj.url)
    base = BASE_URL
    if not base:
        return None
    size = size or EMOJI_PAD_SIZE
    box  = box  or EMOJI_PAD_BOX
    q = urllib.parse.urlencode({"u": src, "s": str(size), "box": str(box), "v": str(emj.id)})
    return f"{base.rstrip('/')}/emoji-pad?{q}"

def resolve_thumb_url(guild: discord.Guild | None, tag: str | None) -> str | None:
    """
    If STRICT_EMOJI_PROXY=1 and a proxy BASE_URL exists → use padded.
    Otherwise → use the raw emoji URL so the icon always shows.
    """
    emj = emoji_for_tag(guild, tag)
    if not emj:
        return None
    raw = str(emj.url)
    if STRICT_EMOJI_PROXY:
        padded = padded_emoji_url(guild, tag)
        return padded or None
    return raw


# ------------------- Discord bot state -------------------
def _cooldown_ok(user_id: int) -> bool:
    now = time.time()
    if now - LAST_CALL[user_id] < COOLDOWN_SEC:
        return False
    LAST_CALL[user_id] = now
    return True

# ------------------- Formatting -------------------
def format_filters_footer(cb, hydra, chimera, cvc, siege, playstyle, roster_mode) -> str:
    def tri(x):
        return "—" if x is None else ("Yes" if int(x) == 1 else "No")
    roster = {None: "All", 1:"Open only", 0:"Full only"}.get(roster_mode, "All")
    parts = []
    if cb:      parts.append(f"CB {cb}")
    if hydra:   parts.append(f"Hydra {hydra}")
    if chimera: parts.append(f"Chimera {chimera}")
    if playstyle: parts.append(f"Style {playstyle}")
    parts.append(f"CvC {tri(cvc)}")
    parts.append(f"Siege {tri(siege)}")
    parts.append(f"Roster {roster}")
    return " • ".join(parts)

def panel_intro(spawn_cmd: str, owner_mention: str, private: bool = False) -> str:
    lines = [f"**{owner_mention} has summoned C1C-Matchmaker.**"]
    if private:
        lines.append("🔒 This panel is **private** — only you can see and use it.")
    else:
        cmd = "!clansearch" if spawn_cmd == "search" else "!clanmatch"
        lines.append(f"⚠️ Only they can use this panel. Not yours? Type **{cmd}** to get your own.")
    return "\n".join(lines)

def build_entry_criteria_classic(row) -> str:
    """
    Build the 'Entry Criteria' block using the sheet's headers (V..AB),
    so labels match the tab exactly. Appends Requirements/Notes if present.
    """
    rows = get_rows(False)
    headers = rows[0] if rows and len(rows) > 0 else []

    lines = ["**Entry Criteria:**"]
    for idx in range(IDX_V, IDX_AB + 1):
        val = (row[idx] if idx < len(row) else "") or ""
        val = val.strip()
        if not val:
            continue
        label = ((headers[idx] if idx < len(headers) else "") or "").strip()
        if label:
            lines.append(f"{label}: {val}")
        else:
            lines.append(val)

    req = (row[IDX_AE_REQUIREMENTS] if len(row) > IDX_AE_REQUIREMENTS else "") or ""
    comments = (row[IDX_AD_COMMENTS] if len(row) > IDX_AD_COMMENTS else "") or ""
    if req.strip():
        lines.append(f"Requirements: {req.strip()}")
    if comments.strip():
        lines.append(f"Notes: {comments.strip()}")

    return "\n".join(lines)


def make_embed_for_row_classic(row, filters_text: str, guild: discord.Guild) -> discord.Embed:
    clan = (row[COL_B_CLAN] or "").strip() or "—"
    tag  = (row[COL_C_TAG] or "").strip()
    rank = (row[COL_A_RANK] or "").strip() or "—"
    level = (row[COL_D_LEVEL] or "").strip()
    spots = (row[COL_E_SPOTS] or "").strip()

    title = f"{clan} [{tag}] — Rank {rank}"
    sections = []

    header = []
    if level: header.append(f"Level: {level}")
    if spots: header.append(f"Spots: {spots}")
    if header:
        sections.append(" • ".join(header))

    prog = (row[COL_F_PROGRESSION] or "").strip()
    leads = (row[COL_G_LEAD] or "").strip()
    deps  = (row[COL_H_DEPUTIES] or "").strip()
    if any([prog, leads, deps]):
        lines = []
        if prog:  lines.append(f"Progression: {prog}")
        if leads: lines.append(f"Lead: {leads}")
        if deps:  lines.append(f"Deputies: {deps}")
        sections.append("\n".join(lines))

    sections.append(build_entry_criteria_classic(row))
    comments = (row[IDX_AD_COMMENTS] or "").strip()
    if comments:
        sections.append(f"**Clan Needs/Comments:** {comments}")

    e = discord.Embed(title=title, description="\n\n".join(sections))

    # resilient thumbnail: padded → raw fallback
    thumb = resolve_thumb_url(guild, tag)
if thumb:
    e.set_thumbnail(url=thumb)

    e.set_footer(text=f"Filters used: {filters_text}")
    return e

def make_embed_for_row_search(row, _filters_text: str, guild: discord.Guild) -> discord.Embed:
    clan = (row[COL_B_CLAN] or "").strip() or "—"
    tag  = (row[COL_C_TAG] or "").strip()
    rank = (row[COL_A_RANK] or "").strip() or "—"
    level = (row[COL_D_LEVEL] or "").strip()
    spots = (row[COL_E_SPOTS] or "").strip()

    title = f"{clan} [{tag}]"
    lines = []
    meta = []
    if rank: meta.append(f"Rank {rank}")
    if level: meta.append(f"Lvl {level}")
    if spots: meta.append(f"Spots {spots}")
    if meta:
        lines.append(" • ".join(meta))
    if len(lines) == 1:
        lines.append("—")

    e = discord.Embed(title=title, description="\n".join(lines))

    # resilient thumbnail: padded → raw fallback
    thumb = padded_emoji_url(guild, tag)
    thumb = resolve_thumb_url(guild, tag)
if thumb:
    e.set_thumbnail(url=thumb)


    # hint so 💡 can flip to Entry Criteria
    e.set_footer(text="React with 💡 for Entry Criteria")
    return e

def make_embed_for_profile(row, _filters_text: str, guild: discord.Guild) -> discord.Embed:
    clan = (row[COL_B_CLAN] or "").strip() or "—"
    tag  = (row[COL_C_TAG] or "").strip()
    title = f"{clan} [{tag}] — Profile"

    parts = []
    lead = (row[COL_G_LEAD] or "").strip()
    deps = (row[COL_H_DEPUTIES] or "").strip()
    if lead or deps:
        parts.append(f"Leadership: {lead or '—'}" + (f" • Deputies: {deps}" if deps else ""))

    ranges = []
    if (row[COL_M_CB] or "").strip():     ranges.append(f"CB: {row[COL_M_CB]}")
    if (row[COL_N_HYDRA] or "").strip():  ranges.append(f"Hydra: {row[COL_N_HYDRA]}")
    if (row[COL_O_CHIMERA] or "").strip():ranges.append(f"Chimera: {row[COL_O_CHIMERA]}")
    if ranges: parts.append("Ranges: " + " • ".join(ranges))

    cvc  = (row[COL_I_CVC_TIER] or "").strip()
    cvcw = (row[COL_J_CVC_WINS] or "").strip()
    sieg = (row[COL_K_SIEGE_TIER] or "").strip()
    siegw= (row[COL_L_SIEGE_WINS] or "").strip()
    meta = [m for m in [f"CvC tier {cvc}" if cvc else "", f"CvC wins {cvcw}" if cvcw else "", f"Siege tier {sieg}" if sieg else "", f"Siege wins {siegw}" if siegw else ""] if m]
    if meta: parts.append("Stats: " + " • ".join(meta))

    style = (row[COL_U_STYLE] or "").strip()
    if style: parts.append(f"Playstyle: {style}")

    e = discord.Embed(title=title, description="\n".join(parts))

    thumb = padded_emoji_url(guild, tag)
    thumb = resolve_thumb_url(guild, tag)
if thumb:
    e.set_thumbnail(url=thumb)

    e.set_footer(text="React with 💡 to flip back to Entry Criteria")
    return e

# --- Aggregated classic results into single message embeds ---
def make_embeds_for_rows_classic_aggregate(rows, filters_text: str, guild: discord.Guild, title: str = "C1C Matchmaker — Results", per_page: int = 12):
    embeds = []
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    for pi in range(pages):
        chunk = rows[pi*per_page:(pi+1)*per_page]
        e = discord.Embed(title=title)
        for r in chunk:
            clan = (r[COL_B_CLAN] or "").strip() or "—"
            tag  = (r[COL_C_TAG] or "").strip()
            emj  = emoji_for_tag(guild, tag)
            name = f"{str(emj) + ' ' if emj else ''}{clan} [{tag}]"
            val  = build_entry_criteria_classic(r)
            e.add_field(name=name, value=val, inline=False)
        ft = f"Filters used: {filters_text} • Page {pi+1}/{pages} • {total} match(es)"
        e.set_footer(text=ft)
        embeds.append(e)
    return embeds

# ------------------- Panel View -------------------
class ClanMatchView(discord.ui.View):
    def __init__(self, author_id: int, embed_variant: str, spawn_cmd: str):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.embed_variant = embed_variant  # "classic" for !clanmatch, "search" for !clansearch
        self.spawn_cmd = spawn_cmd
        self.owner_mention = "the summoner"

        # filters state
        self.cb = None
        self.hydra = None
        self.chimera = None
        self.cvc = None
        self.siege = None
        self.playstyle = None
        self.roster_mode = None  # None=All, 1=Open only, 0=Full only

        self.message: discord.Message | None = None

        # UI components (kept minimal but functional)
        self.add_item(self.CBSelect(self))
        self.add_item(self.HydraSelect(self))
        self.add_item(self.ChimeraSelect(self))
        self.add_item(self.PlaystyleInput(self))
        self.add_item(self.CvCButtons(self))
        self.add_item(self.SiegeButtons(self))
        self.add_item(self.RosterButtons(self))
        self.add_item(self.SearchBtn(self))
        self.add_item(self.ResetBtn(self))

    # ----- UI inner classes -----
    class CBSelect(discord.ui.Select):
        def __init__(self, view:"ClanMatchView"):
            self._parent = view
            options = [discord.SelectOption(label=o or "(any)", value=o) for o in ["", "Easy","Normal","Hard","Brutal","NM","UNM"]]
            super().__init__(placeholder="Clan Boss", min_values=0, max_values=1, options=options, row=0)
        async def callback(self, itx: discord.Interaction):
            self._parent.cb = self.values[0] if self.values else None
            await itx.response.defer()
    class HydraSelect(discord.ui.Select):
        def __init__(self, view:"ClanMatchView"):
            self._parent = view
            options = [discord.SelectOption(label=o or "(any)", value=o) for o in ["", "Easy","Normal","Hard","Brutal","NM","UNM"]]
            super().__init__(placeholder="Hydra", min_values=0, max_values=1, options=options, row=0)
        async def callback(self, itx: discord.Interaction):
            self._parent.hydra = self.values[0] if self.values else None
            await itx.response.defer()
    class ChimeraSelect(discord.ui.Select):
        def __init__(self, view:"ClanMatchView"):
            self._parent = view
            options = [discord.SelectOption(label=o or "(any)", value=o) for o in ["", "Easy","Normal","Hard","Brutal","NM","UNM"]]
            super().__init__(placeholder="Chimera", min_values=0, max_values=1, options=options, row=0)
        async def callback(self, itx: discord.Interaction):
            self._parent.chimera = self.values[0] if self.values else None
            await itx.response.defer()
    class PlaystyleInput(discord.ui.Select):
        def __init__(self, view:"ClanMatchView"):
            self._parent = view
            options = [discord.SelectOption(label=o, value=o) for o in ["Any","Relaxed","Semi-competitive","Competitive"]]
            super().__init__(placeholder="Playstyle", min_values=0, max_values=1, options=options, row=1)
        async def callback(self, itx: discord.Interaction):
            val = self.values[0] if self.values else None
            if val == "Any": val = None
            self._parent.playstyle = val
            await itx.response.defer()
    class CvCButtons(discord.ui.Item):
        def __init__(self, view:"ClanMatchView"):
            super().__init__(row=2); self._parent = view
        async def callback(self, _): pass
        async def refresh_message(self): pass
        def view(self): return self._parent
        @discord.ui.button(label="CvC —", style=discord.ButtonStyle.secondary, row=2)
        async def cvc_any(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.cvc = None; await itx.response.defer()
        @discord.ui.button(label="CvC Yes", style=discord.ButtonStyle.secondary, row=2)
        async def cvc_yes(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.cvc = 1; await itx.response.defer()
        @discord.ui.button(label="CvC No", style=discord.ButtonStyle.secondary, row=2)
        async def cvc_no(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.cvc = 0; await itx.response.defer()
    class SiegeButtons(discord.ui.Item):
        def __init__(self, view:"ClanMatchView"):
            super().__init__(row=2); self._parent = view
        async def callback(self, _): pass
        async def refresh_message(self): pass
        def view(self): return self._parent
        @discord.ui.button(label="Siege —", style=discord.ButtonStyle.secondary, row=2)
        async def siege_any(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.siege = None; await itx.response.defer()
        @discord.ui.button(label="Siege Yes", style=discord.ButtonStyle.secondary, row=2)
        async def siege_yes(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.siege = 1; await itx.response.defer()
        @discord.ui.button(label="Siege No", style=discord.ButtonStyle.secondary, row=2)
        async def siege_no(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.siege = 0; await itx.response.defer()
    class RosterButtons(discord.ui.Item):
        def __init__(self, view:"ClanMatchView"):
            super().__init__(row=3); self._parent = view
        async def callback(self, _): pass
        async def refresh_message(self): pass
        def view(self): return self._parent
        @discord.ui.button(label="Roster All", style=discord.ButtonStyle.secondary, row=3)
        async def roster_all(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.roster_mode = None; await itx.response.defer()
        @discord.ui.button(label="Roster Open", style=discord.ButtonStyle.secondary, row=3)
        async def roster_open(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.roster_mode = 1; await itx.response.defer()
        @discord.ui.button(label="Roster Full", style=discord.ButtonStyle.secondary, row=3)
        async def roster_full(self, itx: discord.Interaction, _btn: discord.ui.Button):
            self._parent.roster_mode = 0; await itx.response.defer()
    class SearchBtn(discord.ui.Button):
        def __init__(self, view:"ClanMatchView"):
            super().__init__(label="Search Clans", style=discord.ButtonStyle.primary, row=4); self._parent = view
        async def callback(self, itx: discord.Interaction):
            await self._parent.search(itx, self)
    class ResetBtn(discord.ui.Button):
        def __init__(self, view:"ClanMatchView"):
            super().__init__(label="Reset", style=discord.ButtonStyle.secondary, row=4); self._parent = view
        async def callback(self, itx: discord.Interaction):
            v = self._parent
            v.cb = v.hydra = v.chimera = v.playstyle = None
            v.cvc = v.siege = None
            v.roster_mode = None
            await itx.response.send_message("Filters reset. Pick new ones and hit **Search Clans**.", ephemeral=True)

    def _sync_visuals(self):  # minimal; components reflect state on next interaction
        pass

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user and itx.user.id == self.author_id:
            return True
        cmd = "!clansearch" if self.spawn_cmd == "search" else "!clanmatch"
        await itx.response.send_message(
            f"⚠️ This panel isn’t yours. Type **{cmd}** to summon your own.", ephemeral=True
        )
        return False

    @discord.ui.button(label="(internal)", style=discord.ButtonStyle.secondary, disabled=True, row=5)
    async def _ghost(self, itx: discord.Interaction, _btn: discord.ui.Button):
        try: await itx.response.defer()
        except InteractionResponded: pass

    async def search(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not any([self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.roster_mode is not None]):
            await itx.response.send_message("Pick at least **one** filter, then try again. 🙂", ephemeral=True)
            return

        await itx.response.defer(thinking=True)  # public results
        try:
            rows = get_rows(False)
        except Exception as e:
            await itx.followup.send(f"❌ Failed to read sheet: {e}")
            return

        def tok(x):
            if not x: return None
            u = x.upper()
            return {"EASY":"ESY","NORMAL":"NML","HARD":"HRD","BRUTAL":"BTL","NM":"NM","UNM":"UNM"}.get(u, u)

        cb     = tok(self.cb)
        hydra  = tok(self.hydra)
        chim   = tok(self.chimera)
        cvc    = self.cvc
        siege  = self.siege
        style  = self.playstyle
        roster = self.roster_mode

        matches = []
        for row in rows[1:]:
            try:
                if is_header_row(row): continue
                if not row_matches(row, cb, hydra, chim, cvc, siege, style): continue
                if roster is not None:
                    open_spots = parse_spots(row[COL_E_SPOTS] or "")
                    if roster == 1 and open_spots <= 0: continue
                    if roster == 0 and open_spots > 0:  continue
                matches.append(row)
            except Exception:
                continue

        if not matches:
            await itx.followup.send("No matching clans found. Try loosening Playstyle or CvC/Siege filters.")
            return

        filters_text = format_filters_footer(cb, hydra, chim, cvc, siege, style, roster)
        builder = make_embed_for_row_search if self.embed_variant == "search" else make_embed_for_row_classic

        if self.embed_variant == "search":
            # one message per row so 💡 can map 1:1 to profile flips
            for r in matches:
                embed = builder(r, filters_text, itx.guild)
                ft = embed.footer.text or ""
                hint = "React with 💡 for Clan Profile"
                embed.set_footer(text=(f"{ft} • {hint}" if ft else hint))
                msg = await itx.followup.send(embed=embed)
                try: await msg.add_reaction("💡")
                except Exception: pass
                REACT_INDEX[msg.id] = {"row": r, "kind": "profile_from_search",
                                       "guild_id": itx.guild_id, "channel_id": msg.channel.id}
        else:
            # classic variant → aggregate into a single message (paginated embeds)
            embeds = make_embeds_for_rows_classic_aggregate(matches, filters_text, itx.guild)
            msg = await itx.followup.send(embed=embeds[0])
            if len(embeds) > 1:
                try:
                    await msg.add_reaction("◀️"); await msg.add_reaction("▶️")
                except Exception:
                    pass
                def check(reaction, user):
                    return reaction.message.id == msg.id and user.id == self.author_id and str(reaction.emoji) in ("◀️","▶️")
                idx = 0
                while True:
                    try:
                        reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)
                    except asyncio.TimeoutError:
                        break
                    try:
                        await msg.remove_reaction(reaction.emoji, user)
                    except Exception:
                        pass
                    idx = (idx - 1) % len(embeds) if str(reaction.emoji) == "◀️" else (idx + 1) % len(embeds)
                    try:
                        await msg.edit(embed=embeds[idx])
                    except Exception:
                        break

# ------------------- Reaction flip index -------------------
REACT_INDEX: dict[int, dict] = {}  # message_id -> {row, kind, guild_id, channel_id}

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != "💡":
        return
    info = REACT_INDEX.get(payload.message_id)
    if not info:
        return
    try:
        guild = bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id) if guild else None
        if not channel:
            return
        msg = await channel.fetch_message(payload.message_id)
        row = info["row"]
        # flip between search card and profile
        if msg.embeds and "Profile" in (msg.embeds[0].title or ""):
            new = make_embed_for_row_classic(row, "", guild)
        else:
            new = make_embed_for_profile(row, "", guild)
        await msg.edit(embed=new)
        try:
            if payload.member:
                await msg.remove_reaction("💡", payload.member)
        except Exception:
            pass
    except Exception:
        traceback.print_exc()

# ------------------- Commands -------------------
async def _safe_delete(message: discord.Message):
    try:
        await message.delete()
    except Exception:
        pass

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.command(name="clanmatch")
async def clanmatch_cmd(ctx: commands.Context, *, extra: str | None = None):
    if extra and extra.strip():
        msg = (
            "❌ `!clanmatch` doesn’t take a clan tag or name.\n"
            "• Use **`!clan <tag or name>`** to see a specific clan profile (e.g., `!clan C1CE`).\n"
            "• Or type **`!clanmatch`** by itself to open the filter panel."
        )
        await ctx.reply(msg, mention_author=False); return
    if not _cooldown_ok(ctx.author.id): return
    view = ClanMatchView(author_id=ctx.author.id, embed_variant="classic", spawn_cmd="match")
    embed = discord.Embed(
        title="Find a C1C Clan for your recruit",
        description=panel_intro("match", ctx.author.mention, private=False) + "\n\n"
                    "Pick any filters (you can leave some blank) and click **Search Clans**.\n"
                    "**Tip:** choose the most important criteria for your recruit — *but don’t go overboard*. "
                    "Too many filters might narrow things down to zero."
    )
    embed.set_footer(text="Only the summoner can use this panel.")
    key = (ctx.author.id, "classic")
    old_id = ACTIVE_PANELS.get(key)
    if old_id:
        try:
            msg = await ctx.channel.fetch_message(old_id)
            view.message = msg
            await msg.edit(embed=embed, view=view)
            await _safe_delete(ctx.message)
            return
        except Exception:
            pass
    msg = await ctx.reply(embed=embed, view=view, mention_author=False)
    ACTIVE_PANELS[key] = msg.id

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.command(name="clansearch")
async def clansearch_cmd(ctx: commands.Context, *, extra: str | None = None):
    if extra and extra.strip():
        msg = (
            "❌ `!clansearch` doesn’t take a clan tag or name.\n"
            "• Use **`!clan <tag or name>`** to see a specific clan profile.\n"
            "• Or type **`!clansearch`** by itself to open the quick search panel."
        )
        await ctx.reply(msg, mention_author=False); return
    if not _cooldown_ok(ctx.author.id): return
    view = ClanMatchView(author_id=ctx.author.id, embed_variant="search", spawn_cmd="search")
    embed = discord.Embed(
        title="Quick Clan Search",
        description=panel_intro("search", ctx.author.mention, private=False) + "\n\n"
                    "Set a couple filters and hit **Search Clans**."
    )
    embed.set_footer(text="Only the summoner can use this panel.")
    key = (ctx.author.id, "search")
    old_id = ACTIVE_PANELS.get(key)
    if old_id:
        try:
            msg = await ctx.channel.fetch_message(old_id)
            view.message = msg
            await msg.edit(embed=embed, view=view)
            await _safe_delete(ctx.message)
            return
        except Exception:
            pass
    msg = await ctx.reply(embed=embed, view=view, mention_author=False)
    ACTIVE_PANELS[key] = msg.id

# NEW/RESTORED: !clan <tag or name>
@bot.command(name="clan")
async def clan_cmd(ctx: commands.Context, *, query: str | None = None):
    if not query or not query.strip():
        await ctx.reply("Usage: `!clan <tag or name>`  e.g., `!clan C1CE` or `!clan Martyrs`", mention_author=False)
        return
    try:
        rows = get_rows(False)
    except Exception as e:
        await ctx.reply(f"❌ Failed to read sheet: {e}", mention_author=False); return

    q = query.strip().lower()
    best = None
    # priority 1: exact tag match
    for r in rows[1:]:
        if is_header_row(r): continue
        tag = (r[COL_C_TAG] or "").strip().lower()
        if tag and tag == q:
            best = r; break
    # priority 2: exact name match
    if not best:
        for r in rows[1:]:
            if is_header_row(r): continue
            name = (r[COL_B_CLAN] or "").strip().lower()
            if name and name == q:
                best = r; break
    # priority 3: contains in name
    if not best:
        for r in rows[1:]:
            if is_header_row(r): continue
            name = (r[COL_B_CLAN] or "").strip().lower()
            if name and q in name:
                best = r; break
    if not best:
        await ctx.reply("No clan found by that tag or name.", mention_author=False); return

    embed = make_embed_for_row_classic(best, "Direct lookup", ctx.guild)
    msg = await ctx.reply(embed=embed, mention_author=False)
    try: await msg.add_reaction("💡")
    except Exception: pass
    REACT_INDEX[msg.id] = {"row": best, "kind": "profile_from_search",
                           "guild_id": ctx.guild.id if ctx.guild else None,
                           "channel_id": msg.channel.id}

@bot.command(name="reload")
async def reload_cache_cmd(ctx):
    clear_cache()
    await ctx.send("♻️ Sheet cache cleared. Next search will fetch fresh data.")
    await _safe_delete(ctx.message)

# ------------------- HTTP mini-server (emoji pad) -------------------
async def _health_http(_):
    return web.Response(text="ok")

async def emoji_pad_handler(request: web.Request):
    url = request.rel_url.query.get("u")
    size = int(request.rel_url.query.get("s", "256"))
    box  = float(request.rel_url.query.get("box", "0.85"))
    if not url:
        return web.Response(text="missing u", status=400)
    session: ClientSession = request.app["session"]
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return web.Response(text=f"upstream {resp.status}", status=502)
            raw = await resp.read()
    except Exception:
        return web.Response(text="upstream error", status=502)

    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    max_side = max(w, h)
    target = int(size * float(box))
    scale    = target / float(max_side)
    new_w    = max(1, int(w * scale))
    new_h    = max(1, int(h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - new_w) // 2
    y = (size - new_h) // 2
    canvas.paste(img, (x, y), img)

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return web.Response(
        body=out.getvalue(),
        headers={"Cache-Control": "public, max-age=86400"},
        content_type="image/png",
    )

async def start_webserver():
    app = web.Application()
    app["session"] = ClientSession()
    app.router.add_get("/", _health_http)
    app.router.add_get("/health", _health_http)
    app.router.add_get("/emoji-pad", emoji_pad_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[keepalive] HTTP server listening on :{port}", flush=True)

# ------------------- SLASH: /health -------------------
@bot.tree.command(name="health", description="Bot & Sheets status")
async def health_slash(itx: discord.Interaction):
    await itx.response.defer(thinking=False, ephemeral=False)
    try:
        ws = get_ws(False); _ = ws.row_values(1)
        sheets_status = f"OK (`{WORKSHEET_NAME}`)"
    except Exception as e:
        sheets_status = f"ERROR: {e}"
    uptime = _fmt_uptime()
    await itx.followup.send(f"Matchmaker up `{uptime}` • Sheets: {sheets_status}")

# ------------------- Boot -------------------
async def main():
    asyncio.create_task(start_webserver())
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token or len(token) < 50:
        raise RuntimeError("Missing/short DISCORD_TOKEN.")
    print("[boot] starting discord bot…", flush=True)
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

