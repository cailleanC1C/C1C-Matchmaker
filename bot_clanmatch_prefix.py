# bot_clanmatch_prefix.py
# C1C-Matchmaker — panels, search, profiles, emoji padding, and reaction flip (💡)

import os, json, time, asyncio, re, traceback, urllib.parse, io, math
from collections import defaultdict

import discord
from discord.ext import commands
from discord import InteractionResponded
from discord.utils import get

import gspread
from google.oauth2.service_account import Credentials

from aiohttp import web, ClientSession
from PIL import Image  # Pillow

# Pillow 10+ changed resampling enums; keep compatibility with <10
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 10
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS             # Pillow < 10
    
from discord.ext import tasks
from datetime import datetime, timezone, time as dtime

# ------------------- boot/uptime -------------------
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

# Results per page for multi-card output
PAGE_SIZE = 10

if not CREDS_JSON:
    print("[boot] GSPREAD_CREDENTIALS missing", flush=True)
if not SHEET_ID:
    print("[boot] GOOGLE_SHEET_ID missing", flush=True)
print(f"[boot] WORKSHEET_NAME={WORKSHEET_NAME}", flush=True)
print(f"[boot] BASE_URL={BASE_URL}", flush=True)

# ---- Recruiters daily summary target + role mentions ----
RECRUITERS_THREAD_ID = int(os.environ.get("RECRUITERS_THREAD_ID", "0") or "0")
ROLE_ID_RECRUITMENT_COORDINATOR = int(os.environ.get("ROLE_ID_RECRUITMENT_COORDINATOR", "0") or "0")
ROLE_ID_RECRUITMENT_SCOUT       = int(os.environ.get("ROLE_ID_RECRUITMENT_SCOUT", "0") or "0")

# ---- Recruiter panel threading ----
PANEL_THREAD_MODE       = (os.environ.get("PANEL_THREAD_MODE", "same") or "same").lower()  # "fixed" or "same"
PANEL_FIXED_THREAD_ID   = int(os.environ.get("PANEL_FIXED_THREAD_ID", "0") or "0")
PANEL_PARENT_CHANNEL_ID = int(os.environ.get("PANEL_PARENT_CHANNEL_ID", "0") or "0")
PANEL_THREAD_ARCHIVE_MIN = int(os.environ.get("PANEL_THREAD_ARCHIVE_MIN", "10080") or "10080")  # minutes



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
def map_token(choice: str) -> str:
    c = norm(choice)
    return TOKEN_MAP.get(c, c)

def cell_has_diff(cell_text: str, token: str | None) -> bool:
    if not token:
        return True
    t = map_token(token)
    c = norm(cell_text)
    return (t in c or (t == "HRD" and "HARD" in c) or (t == "NML" and "NORMAL" in c) or (t == "BTL" and "BRUTAL" in c))

def cell_equals_10(cell_text: str, expected: str | None) -> bool:
    if expected is None:
        return True
    return (cell_text or "").strip() == expected  # exact 1/0

# Exact, token-based playstyle matching (no substring matches)
STYLE_CANON = {
    "STRESS FREE": "STRESSFREE",
    "STRESS-FREE": "STRESSFREE",
    "STRESSFREE": "STRESSFREE",
    "CASUAL": "CASUAL",
    "SEMI COMPETITIVE": "SEMICOMPETITIVE",
    "SEMI-COMPETITIVE": "SEMICOMPETITIVE",
    "SEMICOMPETITIVE": "SEMICOMPETITIVE",
    "COMPETITIVE": "COMPETITIVE",
}

def _canon_style(s: str) -> str | None:
    s = (s or "").strip().upper()
    s = re.sub(r"\s+", " ", s.replace("-", " "))
    # try exact canonical first
    if s in STYLE_CANON:
        return STYLE_CANON[s]
    # fall back to a few common forms
    if s == "SEMI COMPETITIVE":
        return "SEMICOMPETITIVE"
    if s == "STRESS FREE":
        return "STRESSFREE"
    return s if s in {"STRESSFREE", "CASUAL", "SEMICOMPETITIVE", "COMPETITIVE"} else None

def _split_styles(cell_text: str) -> set[str]:
    # split on common delimiters and canonicalize each token
    parts = re.split(r"[,\|/;]+", cell_text or "")
    out = set()
    for p in parts:
        c = _canon_style(p)
        if c:
            out.add(c)
    return out

def playstyle_ok(cell_text: str, value: str | None) -> bool:
    if not value:
        return True
    wanted = _canon_style(value)
    if not wanted:
        return True  # unknown filter value → don't block results
    available = _split_styles(cell_text)
    return wanted in available


def parse_spots_num(cell_text: str) -> int:
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
        cell_equals_10(row[COL_S_CVC], cvc) and
        cell_equals_10(row[COL_T_SIEGE], siege) and
        playstyle_ok(row[COL_U_STYLE], playstyle)
    )

def emoji_for_tag(guild: discord.Guild | None, tag: str | None):
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

# ------------------- Panel copy helpers -------------------
def panel_intro(spawn_cmd: str, owner_mention: str, private: bool = False) -> str:
    """
    spawn_cmd: "match" for !clanmatch panels, "search" for !clansearch panels
    """
    lines = [f"**{owner_mention} has summoned C1C-Matchmaker.**"]
    if private:
        lines.append("🔒 This panel is **private** — only you can see and use it.")
    else:
        cmd = "!clansearch" if spawn_cmd == "search" else "!clanmatch"
        lines.append(f"⚠️ Only they can use this panel. Not yours? Type **{cmd}** to get your own.")
    return "\n".join(lines)

# ------------------- Formatting -------------------
def build_entry_criteria_classic(row) -> str:
    """For !clanmatch output: inner labels not bold; spacing via NBSP pipes."""
    NBSP_PIPE = "\u00A0|\u00A0"
    parts = []
    v  = (row[IDX_V]  or "").strip()
    w  = (row[IDX_W]  or "").strip()
    x  = (row[IDX_X]  or "").strip()
    y  = (row[IDX_Y]  or "").strip()
    z  = (row[IDX_Z]  or "").strip()
    aa = (row[IDX_AA] or "").strip()
    ab = (row[IDX_AB] or "").strip()
    if v:  parts.append(f"Hydra keys: {v}")
    if w:  parts.append(f"Chimera keys: {w}")
    if x:  parts.append(x)
    if y:  parts.append(y)
    if z:  parts.append(z)
    if aa: parts.append(f"non PR CvC: {aa}")
    if ab: parts.append(f"PR CvC: {ab}")
    return "**Entry Criteria:** " + (NBSP_PIPE.join(parts) if parts else "—")

def format_filters_footer(cb, hydra, chimera, cvc, siege, playstyle, roster_mode) -> str:
    parts = []
    if cb: parts.append(f"CB: {cb}")
    if hydra: parts.append(f"Hydra: {hydra}")
    if chimera: parts.append(f"Chimera: {chimera}")
    if cvc is not None:   parts.append(f"CvC: {'Yes' if cvc == '1' else 'No'}")
    if siege is not None: parts.append(f"Siege: {'Yes' if siege == '1' else 'No'}")
    if playstyle: parts.append(f"Playstyle: {playstyle}")
    roster_text = "All" if roster_mode is None else ("Open only" if roster_mode == "open" else "Full only")
    parts.append(f"Roster: {roster_text}")
    return " • ".join(parts)

def make_embed_for_row_classic(row, filters_text: str, guild: discord.Guild | None = None) -> discord.Embed:
    clan     = (row[COL_B_CLAN] or "").strip()
    tag      = (row[COL_C_TAG]  or "").strip()
    spots    = (row[COL_E_SPOTS] or "").strip()
    reserved = (row[IDX_AC_RESERVED] or "").strip()
    comments = (row[IDX_AD_COMMENTS] or "").strip()
    addl_req = (row[IDX_AE_REQUIREMENTS] or "").strip()

    title = f"{clan} `{tag}`  — Spots: {spots}"
    if reserved:
        title += f" | Reserved: {reserved}"

    sections = [build_entry_criteria_classic(row)]
    if addl_req:
        sections.append(f"**Additional Requirements:** {addl_req}")
    if comments:
        sections.append(f"**Clan Needs/Comments:** {comments}")

    e = discord.Embed(title=title, description="\n\n".join(sections))

    thumb = padded_emoji_url(guild, tag)
    if thumb:
        e.set_thumbnail(url=thumb)
    elif not STRICT_EMOJI_PROXY:
        em = emoji_for_tag(guild, tag)
        if em:
            e.set_thumbnail(url=str(em.url))

    e.set_footer(text=f"Filters used: {filters_text}")
    return e

def make_embed_for_row_search(row, filters_text: str, guild: discord.Guild | None = None) -> discord.Embed:
    b = (row[COL_B_CLAN] or "").strip()
    c = (row[COL_C_TAG]  or "").strip()
    d = (row[COL_D_LEVEL] or "").strip()
    e_spots = (row[COL_E_SPOTS] or "").strip()

    v  = (row[IDX_V]  or "").strip()
    w  = (row[IDX_W]  or "").strip()
    x  = (row[IDX_X]  or "").strip()
    y  = (row[IDX_Y]  or "").strip()
    z  = (row[IDX_Z]  or "").strip()
    aa = (row[IDX_AA] or "").strip()
    ab = (row[IDX_AB] or "").strip()

    title = f"{b} | {c} | **Level** {d} | **Spots:** {e_spots}"

    lines = ["**Entry Criteria:**"]
    if z:
        lines.append(f"Clan Boss: {z}")
    if v or x:
        hx = []
        if v: hx.append(f"{v} keys")
        if x: hx.append(x)
        lines.append("Hydra: " + " — ".join(hx))
    if w or y:
        cy = []
        if w: cy.append(f"{w} keys")
        if y: cy.append(y)
        lines.append("Chimera: " + " — ".join(cy))
    if aa or ab:
        cvc_bits = []
        if aa: cvc_bits.append(f"non PR minimum: {aa}")
        if ab: cvc_bits.append(f"PR minimum: {ab}")
        lines.append("CvC: " + " | ".join(cvc_bits))
    if len(lines) == 1:
        lines.append("—")

    e = discord.Embed(title=title, description="\n".join(lines))

    thumb = padded_emoji_url(guild, c)
    if thumb:
        e.set_thumbnail(url=thumb)
    elif not STRICT_EMOJI_PROXY:
        em = emoji_for_tag(guild, c)
        if em:
            e.set_thumbnail(url=str(em.url))

    e.set_footer(text=f"Filters used: {filters_text}")
    return e

# ------------------- Recruiters daily summary helpers -------------------

def _locate_summary_headers(rows):
    """
    Finds the row that contains the headers: 'open spots', 'inactives', 'reserved spots'.
    Returns (header_row_index, open_idx, inactive_idx, reserved_idx) or (None, None, None, None)
    """
    for i, r in enumerate(rows[:80]):  # search early part of the sheet
        lower = [(c or "").strip().lower() for c in r]
        if "open spots" in lower and "inactives" in lower and "reserved spots" in lower:
            return (
                i,
                lower.index("open spots"),
                lower.index("inactives"),
                lower.index("reserved spots"),
            )
    # fallback to the screenshot layout (F,G,H)
    return (None, 5, 6, 7)

def _first_nonempty_cell_lower(row):
    for c in row:
        if (c or "").strip():
            return (c or "").strip().lower()
    return ""

def _get_line_values(rows, start_row, label_norm, open_idx, inact_idx, reserve_idx):
    """
    Find a row whose first non-empty cell matches label_norm and return tuple of ints (open, inactive, reserved).
    Search from start_row onward; returns (0,0,0) if not found or not parseable.
    """
    def _to_int(x):
        try: return int(re.search(r"-?\d+", (x or "")).group())
        except Exception: return 0

    for r in rows[start_row: start_row + 60]:
        first = _first_nonempty_cell_lower(r)
        if first == label_norm:
            return (
                _to_int(r[open_idx] if len(r) > open_idx else ""),
                _to_int(r[inact_idx] if len(r) > inact_idx else ""),
                _to_int(r[reserve_idx] if len(r) > reserve_idx else ""),
            )
    return (0, 0, 0)

def read_recruiter_summary():
    """
    Reads the small summary table:
      overall / top10 / top5
      Elite End Game ... Beginners
    Returns a dict: key -> (open, inactives, reserved)
    """
    rows = get_rows(False)
    hdr_row, open_idx, inact_idx, reserve_idx = _locate_summary_headers(rows)
    start = (hdr_row + 1) if hdr_row is not None else 0

    labels = [
        ("overall", "overall"),
        ("top10", "top10"),
        ("top5", "top5"),
        ("elite end game", "Elite End Game"),
        ("early end game", "Early End Game"),
        ("late game", "Late Game"),
        ("mid game", "Mid Game"),
        ("early game", "Early Game"),
        ("beginners", "Beginners"),
    ]
    out = {}
    for key_norm, _pretty in labels:
        out[key_norm] = _get_line_values(rows, start, key_norm, open_idx, inact_idx, reserve_idx)
    return out

def build_recruiters_summary_embed(guild: discord.Guild | None) -> discord.Embed:
    data = read_recruiter_summary()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = []
    lines.append("**General overview**")
    for key_norm, pretty in [
        ("overall", "overall"),
        ("top10",   "Top10"),
        ("top5",    "Top 5"),
    ]:
        o, ina, res = data.get(key_norm, (0,0,0))
        lines.append(f"{pretty}:   open {o} | inactives {ina} | reserved {res}")

    lines.append("")
    lines.append("**per Bracket:**")
    for key_norm, pretty in [
        ("elite end game", "Elite End Game"),
        ("early end game", "Early End Game"),
        ("late game",      "Late Game"),
        ("mid game",       "Mid Game"),
        ("early game",     "Early Game"),
        ("beginners",      "Beginners"),
    ]:
        o, ina, res = data.get(key_norm, (0,0,0))
        lines.append(f"{pretty}:   open {o} | inactives {ina} | reserved {res}")

    e = discord.Embed(title=f"Summary open spots", description="\n".join(lines))

    # thumbnail with the C1C emoji (same padding proxy as other embeds)
    thumb = padded_emoji_url(guild, "C1C")
    if thumb:
        e.set_thumbnail(url=thumb)
    elif not STRICT_EMOJI_PROXY:
        em = emoji_for_tag(guild, "C1C")
        if em:
            e.set_thumbnail(url=str(em.url))

    return e

# ------------------- Daily poster (14:00 UTC) -------------------

POST_TIME_UTC = dtime(hour=14, minute=0, tzinfo=timezone.utc)

@tasks.loop(time=POST_TIME_UTC)
async def daily_recruiters_update():
    try:
        if not RECRUITERS_THREAD_ID:
            print("[daily] RECRUITERS_THREAD_ID not set; skipping.")
            return

        # fetch thread (works for threads and channels)
        thread = bot.get_channel(RECRUITERS_THREAD_ID) or await bot.fetch_channel(RECRUITERS_THREAD_ID)
        if thread is None:
            print(f"[daily] Could not fetch thread {RECRUITERS_THREAD_ID}")
            return

        embed = build_recruiters_summary_embed(getattr(thread, "guild", None))

        # build the mentions as message content (lines exactly as requested)
        parts = [f"Update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]
        if ROLE_ID_RECRUITMENT_COORDINATOR:
            parts.append(f"<@&{ROLE_ID_RECRUITMENT_COORDINATOR}>")
        if ROLE_ID_RECRUITMENT_SCOUT:
            parts.append(f"<@&{ROLE_ID_RECRUITMENT_SCOUT}>")
        content = "\n".join(parts)

        await thread.send(content=content, embed=embed)

    except Exception as e:
        print(f"[daily] post failed: {type(e).__name__}: {e}")


# ----------- Multi-card paging helpers (for !clanmatch only) -----------
def _page_embeds(rows, page_index, builder, filters_text, guild):
    """Build up to PAGE_SIZE embeds for given page. Adds page info to last card."""
    start = page_index * PAGE_SIZE
    end = min(len(rows), start + PAGE_SIZE)
    slice_ = rows[start:end]
    embeds = [builder(r, filters_text, guild) for r in slice_]
    if embeds:
        total_pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
        page_info = f"Page {page_index + 1}/{total_pages} • {len(rows)} total"
        last = embeds[-1]
        ft = last.footer.text or ""
        last.set_footer(text=f"{ft} • {page_info}" if ft else page_info)
    return embeds

class PagedResultsView(discord.ui.View):
    """Prev/Next/Close pager; owner-locked."""
    def __init__(self, *, author_id: int, rows, builder, filters_text: str, guild: discord.Guild | None, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.rows = rows
        self.builder = builder
        self.filters_text = filters_text
        self.guild = guild
        self.page = 0
        self.message: discord.Message | None = None
        self.results_message: discord.Message | None = None  # last results message we posted
        self._active_view: discord.ui.View | None = None     # last pager view (if any) attached to results

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user and itx.user.id == self.author_id:
            return True
        try:
            await itx.response.send_message("⚠️ Not your panel. Type **!clanmatch** to summon your own.", ephemeral=True)
        except InteractionResponded:
            try: await itx.followup.send("⚠️ Not your panel. Type **!clanmatch** to summon your own.", ephemeral=True)
            except Exception: pass
        return False

    def _sync_buttons(self):
        max_page = max(0, math.ceil(len(self.rows) / PAGE_SIZE) - 1)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "pm_prev":
                    child.disabled = (self.page <= 0)
                elif child.custom_id == "pm_next":
                    child.disabled = (self.page >= max_page)

    async def _edit(self, itx: discord.Interaction):
        self._sync_buttons()
        embeds = _page_embeds(self.rows, self.page, self.builder, self.filters_text, self.guild)
        try:
            await itx.response.edit_message(embeds=embeds, view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, embeds=embeds, view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="pm_prev")
    async def prev_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await self._edit(itx)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="pm_next")
    async def next_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        max_page = max(0, math.ceil(len(self.rows) / PAGE_SIZE) - 1)
        if self.page < max_page:
            self.page += 1
        await self._edit(itx)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="pm_close")
    async def close_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        # Try to delete the results message entirely.
        try:
            # Prefer the interaction message; fall back to stored refs.
            target = getattr(itx, "message", None) or self.message or self.results_message
            if target:
                await target.delete()
                return
        except Exception:
            # If deletion fails (e.g., perms), fall back to disabling the view.
            pass
    
        # Fallback: disable buttons and mark as closed (no delete)
        for child in self.children:
            child.disabled = True
        embeds = _page_embeds(self.rows, self.page, self.builder, self.filters_text, self.guild)
        if embeds:
            last = embeds[-1]
            ft = last.footer.text or ""
            last.set_footer(text=f"{ft} • Panel closed" if ft else "Panel closed")
        try:
            await itx.response.edit_message(embeds=embeds, view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, embeds=embeds, view=self)

    async def on_timeout(self):
        try:
            for child in self.children:
                child.disabled = True
            if self.message:
                embeds = _page_embeds(self.rows, self.page, self.builder, self.filters_text, self.guild)
                if embeds:
                    last = embeds[-1]
                    ft = last.footer.text or ""
                    last.set_footer(text=f"{ft} • Expired" if ft else "Expired")
                await self.message.edit(embeds=embeds, view=self)
        except Exception:
            pass

# ------------------- Reaction flip registry -------------------
REACT_INDEX: dict[int, dict] = {}  # message_id -> {row, kind, guild_id, channel_id, filters}

# ------------------- Discord bot -------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---- Role gates via ENV role IDs -------------------------------------------------
# Set these env vars to comma/space-separated role IDs, e.g.:
# RECRUITER_ROLE_IDS="123456789012345678, 234567890123456789"
# LEAD_ROLE_IDS="345678901234567890"
# ADMIN_ROLE_IDS="456789012345678901"   # optional extra admin-ish role(s)

def _parse_id_set(env_name: str) -> set[int]:
    raw = os.environ.get(env_name, "") or ""
    ids: set[int] = set()
    for tok in re.split(r"[,\s]+", raw.strip()):
        if not tok:
            continue
        try:
            ids.add(int(tok))
        except ValueError:
            print(f"[roles] WARN: ignoring non-int token in {env_name}: {tok}", flush=True)
    print(f"[roles] {env_name} = {sorted(ids)}", flush=True)
    return ids

RECRUITER_ROLE_IDS = _parse_id_set("RECRUITER_ROLE_IDS")
LEAD_ROLE_IDS      = _parse_id_set("LEAD_ROLE_IDS")
ADMIN_ROLE_IDS     = _parse_id_set("ADMIN_ROLE_IDS")  # optional

def _has_role_id(member: discord.Member, ids: set[int]) -> bool:
    if not ids or not isinstance(member, discord.Member):
        return False
    return any(getattr(r, "id", None) in ids for r in getattr(member, "roles", []))

def _is_admin_perm(member: discord.Member) -> bool:
    return bool(getattr(member, "guild_permissions", None) and member.guild_permissions.administrator)

def _allowed_recruiter(member: discord.Member) -> bool:
    # EXACT spec: recruiters (scout/coordinator IDs) OR admins (perm or ADMIN_ROLE_IDS)
    return (
        _has_role_id(member, RECRUITER_ROLE_IDS)
        or _is_admin_perm(member)
        or _has_role_id(member, ADMIN_ROLE_IDS)
    )

def _allowed_admin_or_lead(member: discord.Member) -> bool:
    # Admin/lead spec for health/reload/ping
    return (
        _is_admin_perm(member)
        or _has_role_id(member, ADMIN_ROLE_IDS)
        or _has_role_id(member, LEAD_ROLE_IDS)
    )
# ----------------------------------------------------------------------------------

bot.remove_command("help")  # we'll provide our own embed help

LAST_CALL = defaultdict(float)
ACTIVE_PANELS: dict[tuple[int,str], int] = {}  # (user_id, variant) -> message_id
COOLDOWN_SEC = 2.0

CB_CHOICES        = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
HYDRA_CHOICES     = ["Normal", "Hard", "Brutal", "NM"]
CHIMERA_CHOICES   = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
PLAYSTYLE_CHOICES = ["stress-free", "Casual", "Semi Competitive", "Competitive"]

class ClanMatchView(discord.ui.View):
    """4 selects + one row of buttons (CvC, Siege, Roster, Reset, Search)."""
    def __init__(self, author_id: int, embed_variant: str = "classic", spawn_cmd: str = "match"):
        super().__init__(timeout=1800)  # 30 min
        self.author_id = author_id
        self.embed_variant = embed_variant        # "classic" or "search"
        self.spawn_cmd = spawn_cmd                # "match" or "search"
        self.owner_mention: str | None = None

        self.cb = None; self.hydra = None; self.chimera = None; self.playstyle = None
        self.cvc = None; self.siege = None
        self.roster_mode: str | None = "open"   # None = All, 'open' = Spots>0, 'full' = Spots<=0 
        self.message: discord.Message | None = None  # set after sending
        self.results_message: discord.Message | None = None  # last results message we posted
        self._active_view: discord.ui.View | None = None     # pager attached to that message


    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if self.message:
                cmd = "!clansearch" if self.spawn_cmd == "search" else "!clanmatch"
                expired = discord.Embed(
                    title="Find a C1C Clan",
                    description=f"⏳ This panel expired. Type **{cmd}** to open a fresh one."
                )
                await self.message.edit(embed=expired, view=self)
        except Exception as e:
            print("[view timeout] failed to edit:", e)

    def _sync_visuals(self):
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                chosen = None
                ph = (child.placeholder or "")
                if "CB Difficulty" in ph: chosen = self.cb
                elif "Hydra Difficulty" in ph: chosen = self.hydra
                elif "Chimera Difficulty" in ph: chosen = self.chimera
                elif "Playstyle" in ph: chosen = self.playstyle
                for opt in child.options:
                    opt.default = (chosen is not None and opt.value == chosen)
            elif isinstance(child, discord.ui.Button):
                if child.label.startswith("CvC:"):
                    child.label = self._toggle_label("CvC", self.cvc)
                    child.style = discord.ButtonStyle.success if self.cvc == "1" else (
                        discord.ButtonStyle.danger if self.cvc == "0" else discord.ButtonStyle.secondary
                    )
                elif child.label.startswith("Siege:"):
                    child.label = self._toggle_label("Siege", self.siege)
                    child.style = discord.ButtonStyle.success if self.siege == "1" else (
                        discord.ButtonStyle.danger if self.siege == "0" else discord.ButtonStyle.secondary
                    )
                elif child.custom_id == "roster_btn":
                    if self.roster_mode == "open":
                        child.label = "Open Spots Only"
                        child.style = discord.ButtonStyle.success
                    elif self.roster_mode == "full":
                        child.label = "Full Only"
                        child.style = discord.ButtonStyle.primary
                    else:  # Any roster
                        child.label = "Any Roster"
                        child.style = discord.ButtonStyle.secondary
                        
    async def _maybe_refresh(self, itx: discord.Interaction):
        """If we already have a results message for !clanmatch, refresh it after criteria changes."""
        if self.embed_variant != "classic":
            return
        if not self.results_message:
            return

        # Re-run the same filter logic to rebuild the results
        try:
            rows = get_rows(False)
        except Exception:
            return

        matches = []
        for row in rows[1:]:
            try:
                if is_header_row(row):
                    continue
                if row_matches(row, self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle):
                    spots_num = parse_spots_num(row[COL_E_SPOTS])
                    if self.roster_mode == "open" and spots_num <= 0:
                        continue
                    if self.roster_mode == "full" and spots_num > 0:
                        continue
                    matches.append(row)
            except Exception:
                continue

        # No matches → clear the embeds on the existing results message
        if not matches:
            try:
                await self.results_message.edit(
                    content="No matching clans with current filters. Adjust and I’ll update here.",
                    embeds=[],
                    view=None
                )
            except Exception:
                pass
            return

        filters_text = format_filters_footer(
            self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.roster_mode
        )
        builder = make_embed_for_row_classic  # classic variant only

        total = len(matches)
        if total <= PAGE_SIZE:
            # No pagination: update embeds in place and drop any old pager
            embeds = _page_embeds(matches, 0, builder, filters_text, itx.guild)
            self._active_view = None
            try:
                await self.results_message.edit(embeds=embeds, view=None)
            except Exception:
                sent = await itx.followup.send(embeds=embeds)
                self.results_message = sent
            return

        # Paginated mode
        view = PagedResultsView(
            author_id=itx.user.id,
            rows=matches,
            builder=builder,
            filters_text=filters_text,
            guild=itx.guild,
            timeout=300
        )
        embeds = _page_embeds(matches, 0, builder, filters_text, itx.guild)
        try:
            await self.results_message.edit(embeds=embeds, view=view)
            self._active_view = view
            view.message = self.results_message
        except Exception:
            sent = await itx.followup.send(embeds=embeds, view=view)
            self.results_message = sent
            self._active_view = view
            view.message = sent


    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.author_id:
            cmd = "!clansearch" if self.spawn_cmd == "search" else "!clanmatch"
            owner = self.owner_mention or "the summoner"
            note = f"⚠️ You can’t use {owner}’s panel. Type **{cmd}** to get your own."
            try:
                await itx.response.send_message(note, ephemeral=True)
            except InteractionResponded:
                await itx.followup.send(note, ephemeral=True)
            return False
        return True

    # Row 0–3: selects
    @discord.ui.select(placeholder="CB Difficulty (optional)", min_values=0, max_values=1, row=0,
                       options=[discord.SelectOption(label=o, value=o) for o in CB_CHOICES])
    async def cb_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.cb = select.values[0] if select.values else None
        self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)
        
    @discord.ui.select(placeholder="Hydra Difficulty (optional)", min_values=0, max_values=1, row=1,
                       options=[discord.SelectOption(label=o, value=o) for o in HYDRA_CHOICES])
    async def hydra_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.hydra = select.values[0] if select.values else None
        self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)

    @discord.ui.select(placeholder="Chimera Difficulty (optional)", min_values=0, max_values=1, row=2,
                       options=[discord.SelectOption(label=o, value=o) for o in CHIMERA_CHOICES])
    async def chimera_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.chimera = select.values[0] if select.values else None
        self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)

    @discord.ui.select(placeholder="Playstyle (optional)", min_values=0, max_values=1, row=3,
                       options=[discord.SelectOption(label=o, value=o) for o in PLAYSTYLE_CHOICES])
    async def playstyle_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.playstyle = select.values[0] if select.values else None
        self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)

    # Row 4: buttons
    def _cycle(self, current):
        return "1" if current is None else ("0" if current == "1" else None)
    def _toggle_label(self, name, value):
        state = "—" if value is None else ("Yes" if value == "1" else "No")
        return f"{name}: {state}"

    @discord.ui.button(label="CvC: —", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_cvc(self, itx: discord.Interaction, button: discord.ui.Button):
        self.cvc = self._cycle(self.cvc); self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)

    @discord.ui.button(label="Siege: —", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_siege(self, itx: discord.Interaction, button: discord.ui.Button):
        self.siege = self._cycle(self.siege); self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)

    @discord.ui.button(label="Open Spots Only", style=discord.ButtonStyle.success, row=4, custom_id="roster_btn")
    async def toggle_roster(self, itx: discord.Interaction, button: discord.ui.Button):
        # Cycle: 'open' → None (any) → 'full' → 'open'
        if self.roster_mode == "open":
            self.roster_mode = None
        elif self.roster_mode is None:
            self.roster_mode = "full"
        else:
            self.roster_mode = "open"
        self._sync_visuals()
        try:
            await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)


    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, row=4)
    async def reset_filters(self, itx: discord.Interaction, _btn: discord.ui.Button):
        self.cb = self.hydra = self.chimera = self.playstyle = None
        self.cvc = self.siege = None
        self.roster_mode = "open"  # reset to default (Open Spots Only)
        self._sync_visuals()
        try:
            await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)
        await self._maybe_refresh(itx)


    @discord.ui.button(label="Search Clans", style=discord.ButtonStyle.primary, row=4, custom_id="cm_search")
    async def search(self, itx: discord.Interaction, _btn: discord.ui.Button):
        # NOTE: this is the SAME method we used before, keep it inside the class.
        if not any([self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.roster_mode is not None]):
            await itx.response.send_message("Pick at least **one** filter, then try again. 🙂", ephemeral=True)
            return

        await itx.response.defer(thinking=True)

        responded = False
        try:
            rows = get_rows(False)

            matches = []
            for row in rows[1:]:
                try:
                    if is_header_row(row):
                        continue
                    if row_matches(row, self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle):
                        spots_num = parse_spots_num(row[COL_E_SPOTS])
                        if self.roster_mode == "open" and spots_num <= 0:
                            continue
                        if self.roster_mode == "full" and spots_num > 0:
                            continue
                        matches.append(row)
                except Exception:
                    continue

            if not matches:
                await itx.followup.send("No matching clans found. Try a different combo.", ephemeral=False)
                responded = True
                return

            filters_text = format_filters_footer(
                self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.roster_mode
            )
            builder = make_embed_for_row_search if self.embed_variant == "search" else make_embed_for_row_classic

            if self.embed_variant == "search":
                for r in matches:
                    embed = builder(r, filters_text, itx.guild)
                    ft = embed.footer.text or ""
                    hint = "React with 💡 for Clan Profile"
                    embed.set_footer(text=(f"{ft} • {hint}" if ft else hint))
                    msg = await itx.followup.send(embed=embed)
                    responded = True
                    try:
                        await msg.add_reaction("💡")
                    except Exception:
                        pass
                    REACT_INDEX[msg.id] = {
                        "row": r,
                        "kind": "profile_from_search",
                        "guild_id": itx.guild_id,
                        "channel_id": msg.channel.id,
                        "filters": filters_text,
                    }
                return

            total = len(matches)
            if total <= PAGE_SIZE:
                embeds = _page_embeds(matches, 0, builder, filters_text, itx.guild)
                self._active_view = None
                if self.results_message:
                    try:
                        await self.results_message.edit(embeds=embeds, view=None)
                        await itx.followup.send("Results updated.", ephemeral=True)
                        responded = True
                    except Exception:
                        sent = await itx.followup.send(embeds=embeds)
                        responded = True
                        self.results_message = sent
                else:
                    sent = await itx.followup.send(embeds=embeds)
                    responded = True
                    self.results_message = sent
                return

            view = PagedResultsView(
                author_id=itx.user.id,
                rows=matches,
                builder=builder,
                filters_text=filters_text,
                guild=itx.guild,
                timeout=300
            )
            embeds = _page_embeds(matches, 0, builder, filters_text, itx.guild)

            if self.results_message:
                try:
                    await self.results_message.edit(embeds=embeds, view=view)
                    self._active_view = view
                    view.message = self.results_message
                    await itx.followup.send("Results updated.", ephemeral=True)
                    responded = True
                except Exception:
                    sent = await itx.followup.send(embeds=embeds, view=view)
                    responded = True
                    self.results_message = sent
                    self._active_view = view
                    view.message = sent
            else:
                sent = await itx.followup.send(embeds=embeds, view=view)
                responded = True
                self.results_message = sent
                self._active_view = view
                view.message = sent

        except Exception as e:
            if not responded:
                try:
                    await itx.followup.send(f"❌ Error: {type(e).__name__}: {e}", ephemeral=True)
                except Exception:
                    pass

# ------------------- Help (custom) -------------------
@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    e = discord.Embed(
        title="C1C-Matchmaker — Help",
        description=(
            "Helps recruiters place new players into the right clan, and lets members "
            "browse open spots and entry criteria."
        ),
        color=discord.Color.blurple()
    )

    # Thumbnail: C1C emoji (uses the same padded-emoji proxy as result embeds)
    thumb = padded_emoji_url(ctx.guild, "C1C")
    if thumb:
        e.set_thumbnail(url=thumb)
    elif not STRICT_EMOJI_PROXY:
        em = emoji_for_tag(ctx.guild, "C1C")
        if em:
            e.set_thumbnail(url=str(em.url))

    e.add_field(
        name="For Recruiters",
        value=(
            "• `!clanmatch` — Open the recruiter panel. Pick filters (CB, Hydra, Chimera, "
            "CvC Yes/No, Siege Yes/No, Playstyle, Roster). Only the person who opens a panel can use it.\n"
            "• `!clan <tag or name>` — Show a clan **Profile** (level, rank, leadership, "
            "boss/hydra/chimera ranges, CvC & Siege stats, progression/style)."
        ),
        inline=False
    )

    e.add_field(
        name="For Members",
        value=(
            "• `!clansearch` — Open the member panel to browse clans by filters and see **Entry Criteria** "
            "& open spots.\n"
            "  Tip: On results from `!clansearch`, react with 💡 to flip between **Entry Criteria** and the **Clan Profile**."
        ),
        inline=False
    )

    e.add_field(
        name="Admin / Maintenance",
        value=(
            "• `!reload` — Clear the sheet cache (forces fresh data on next search)\n"
            "• `!health` — Bot & Sheets status (latency, worksheet health, uptime)\n"
            "• `!ping` — Quick “bot alive” check"
        ),
        inline=False
    )

    e.set_footer(text="Note: Panels are owner-locked — only the opener can use their panel.")
    await ctx.reply(embed=e, mention_author=False)



# ------------------- Commands: panels -------------------
async def _safe_delete(message: discord.Message):
    try:
        await message.delete()
    except Exception:
        pass

async def _resolve_recruiter_panel_channel(ctx: commands.Context) -> discord.abc.Messageable | None:
    """
    Returns the channel/thread where !clanmatch panels should live.
    If PANEL_THREAD_MODE=fixed and PANEL_FIXED_THREAD_ID is valid, use that thread.
    In fixed mode, never fall back to the invoking channel.
    """
    # SAME mode → always the invoking channel
    if PANEL_THREAD_MODE != "fixed" or not PANEL_FIXED_THREAD_ID:
        return ctx.channel

    try:
        dest = bot.get_channel(PANEL_FIXED_THREAD_ID) or await bot.fetch_channel(PANEL_FIXED_THREAD_ID)
        # Must be a thread in FIXED mode
        if isinstance(dest, discord.Thread):
            if dest.archived:
                try:
                    await dest.edit(
                        archived=False,
                        auto_archive_duration=min(max(PANEL_THREAD_ARCHIVE_MIN, 60), 10080),
                    )
                except Exception:
                    pass  # best effort if we can't unarchive/extend
            return dest
        # Not a thread → treat as error in FIXED mode (no fallback)
        print(f"[panel-thread] FIXED mode id {PANEL_FIXED_THREAD_ID} is not a thread; refusing to fallback.", flush=True)
        return None
    except Exception as e:
        print(f"[panel-thread] FIXED mode fetch failed: {type(e).__name__}: {e}", flush=True)
        return None

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.command(name="clanmatch")
async def clanmatch_cmd(ctx: commands.Context, *, extra: str | None = None):
    if extra and extra.strip():
        msg = (
            "❌ `!clanmatch` doesn’t take a clan tag or name.\n"
            "• Use **`!clan <tag or name>`** to see a specific clan profile (e.g., `!clan C1CE`).\n"
            "• Or type **`!clanmatch`** by itself to open the filter panel."
        )
        await ctx.reply(msg, mention_author=False)
        await _safe_delete(ctx.message)
        return

    if not isinstance(ctx.author, discord.Member) or not _allowed_recruiter(ctx.author):
        await ctx.reply("⚠️ Only **Recruitment Scouts/Coordinators** (or Admins) can use `!clanmatch`.",
                        mention_author=False)
        await _safe_delete(ctx.message)
        return

    view = ClanMatchView(author_id=ctx.author.id, embed_variant="classic", spawn_cmd="match")
    view.owner_mention = ctx.author.mention
    view._sync_visuals()

    embed = discord.Embed(
        title="Find a C1C Clan for your recruit",
        description=panel_intro("match", ctx.author.mention, private=False) + "\n\n"
                    "Pick any filters (you can leave some blank) and click **Search Clans**.\n"
                    "**Tip:** choose the most important criteria for your recruit — *but don’t go overboard*. "
                    "Too many filters might narrow things down to zero."
    )
    embed.set_footer(text="Only the summoner can use this panel.")

    target_chan = await _resolve_recruiter_panel_channel(ctx)
    if target_chan is None:
        await ctx.reply("❌ I couldn’t access the configured recruiter thread. "
                        "Check `PANEL_FIXED_THREAD_ID` and my permissions.", mention_author=False)
        await _safe_delete(ctx.message)
        return

    print(f"[clanmatch] sending to {getattr(target_chan, 'id', None)} (invoked {ctx.channel.id})", flush=True)

    key = (ctx.author.id, "classic")
    old_id = ACTIVE_PANELS.get(key)
    allowed = discord.AllowedMentions(users=[ctx.author])

    if old_id:
        try:
            msg = await target_chan.fetch_message(old_id)
            view.message = msg
            await msg.edit(embed=embed, view=view)
    
            # Optional pointer (no auto-delete)
            if target_chan != ctx.channel:
                try:
                    await ctx.reply(
                        f"{ctx.author.mention} your recruiter panel is in {target_chan.mention}.",
                        mention_author=False,
                        allowed_mentions=allowed
                    )
                except Exception:
                    pass
    
            await _safe_delete(ctx.message)
            return  # <-- important: don’t create a new panel
        except Exception:
            pass

    # New panel: ping the opener in the thread and drop a pointer in the invoking channel
    sent = await target_chan.send(
        content=(ctx.author.mention if target_chan != ctx.channel else None),
        embed=embed,
        view=view,
        allowed_mentions=allowed,
    )
    view.message = sent
    ACTIVE_PANELS[key] = sent.id

    if target_chan != ctx.channel:
        try:
            await ctx.reply(
                f"{ctx.author.mention} I opened your recruiter panel in {target_chan.mention}. "
                mention_author=False,
                allowed_mentions=allowed,
                delete_after=60,  # remove/tune if you prefer
            )
        except Exception:
            pass

    await _safe_delete(ctx.message)

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.command(name="clansearch")
async def clansearch_cmd(ctx: commands.Context, *, extra: str | None = None):
    # Guard: this command takes no arguments
    if extra and extra.strip():
        msg = (
            "❌ `!clansearch` doesn’t take a clan tag or name.\n"
            "• Use **`!clan <tag or name>`** to see a specific clan profile (e.g., `!clan C1CE`).\n"
            "• Or type **`!clansearch`** by itself to open the filter panel."
        )
        await ctx.reply(msg, mention_author=False)
        await _safe_delete(ctx.message)
        return

    view = ClanMatchView(author_id=ctx.author.id, embed_variant="search", spawn_cmd="search")
    view.owner_mention = ctx.author.mention
    view._sync_visuals()

    embed = discord.Embed(
        title="Search for a C1C Clan",
        description=panel_intro("search", ctx.author.mention, private=False) + "\n\n"
                    "Pick any filters *(you can leave some blank)* and click **Search Clans** "
                    "to see Entry Criteria and open Spots."
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

    sent = await ctx.reply(embed=embed, view=view, mention_author=False)
    view.message = sent
    ACTIVE_PANELS[key] = sent.id
    await _safe_delete(ctx.message)


# ------------------- Clan profile command -------------------
def find_clan_row(query: str):
    if not query:
        return None
    q = query.strip().upper()
    rows = get_rows(False)
    exact_tag = None
    exact_name = None
    partials = []
    for row in rows[1:]:
        if is_header_row(row):
            continue
        name = (row[COL_B_CLAN] or "").strip()
        tag  = (row[COL_C_TAG]  or "").strip()
        if not name and not tag:
            continue
        nU, tU = (name.upper(), tag.upper())
        if q == tU:
            exact_tag = row; break
        if q == nU and exact_name is None:
            exact_name = row
        if q in tU or q in nU:
            partials.append(row)
    return exact_tag or exact_name or (partials[0] if partials else None)

def make_embed_for_profile(row, guild: discord.Guild | None = None) -> discord.Embed:
    # Top line with rank fallback
    rank_raw = (row[COL_A_RANK] or "").strip()
    rank = rank_raw if rank_raw and rank_raw not in {"-", "—"} else ">1k"

    name  = (row[COL_B_CLAN]        or "").strip()
    tag   = (row[COL_C_TAG]         or "").strip()
    lvl   = (row[COL_D_LEVEL]       or "").strip()

    # Leadership
    lead  = (row[COL_G_LEAD]        or "").strip()
    deps  = (row[COL_H_DEPUTIES]    or "").strip()

    # Ranges
    cb    = (row[COL_M_CB]          or "").strip()
    hydra = (row[COL_N_HYDRA]       or "").strip()
    chim  = (row[COL_O_CHIMERA]     or "").strip()

    # CvC / Siege
    cvc_t = (row[COL_I_CVC_TIER]    or "").strip()
    cvc_w = (row[COL_J_CVC_WINS]    or "").strip()
    sg_t  = (row[COL_K_SIEGE_TIER]  or "").strip()
    sg_w  = (row[COL_L_SIEGE_WINS]  or "").strip()

    # Footer
    prog  = (row[COL_F_PROGRESSION] or "").strip()
    style = (row[COL_U_STYLE]       or "").strip()

    title = f"{name} | {tag} | **Level** {lvl} | **Global Rank** {rank}"

    lines = [
        f"**Clan Lead:** {lead or '—'}",
        f"**Clan Deputies:** {deps or '—'}",
        "",
        f"**Clan Boss:** {cb or '—'}",
        f"**Hydra:** {hydra or '—'}",
        f"**Chimera:** {chim or '—'}",
        "",
        f"**CvC**: Tier {cvc_t or '—'} | Wins {cvc_w or '—'}",
        f"**Siege:** Tier {sg_t or '—'} | Wins {sg_w or '—'}",
        "",
    ]
    tail = " | ".join([p for p in [prog, style] if p])
    if tail:
        lines.append(tail)

    e = discord.Embed(title=title, description="\n".join(lines))

    thumb = padded_emoji_url(guild, tag)
    if thumb:
        e.set_thumbnail(url=thumb)
    elif not STRICT_EMOJI_PROXY:
        em = emoji_for_tag(guild, tag)
        if em:
            e.set_thumbnail(url=str(em.url))

    # Add hint so 💡 can flip to Entry Criteria
    e.set_footer(text="React with 💡 for Entry Criteria")
    return e

@bot.command(name="clan")
async def clanprofile_cmd(ctx: commands.Context, *, query: str | None = None):
    if not query:
        await ctx.reply("Usage: `!clan <tag or name>` — e.g., `!clan C1CE` or `!clan Elders`", mention_author=False)
        return
    try:
        row = find_clan_row(query)
        if not row:
            await ctx.reply(f"Couldn’t find a clan matching **{query}**.", mention_author=False)
            return
        embed = make_embed_for_profile(row, ctx.guild)
        msg = await ctx.reply(embed=embed, mention_author=False)
        try: await msg.add_reaction("💡")
        except Exception: pass
        REACT_INDEX[msg.id] = {
            "row": row,
            "kind": "entry_from_profile",
            "guild_id": ctx.guild.id if ctx.guild else None,
            "channel_id": msg.channel.id,
            "filters": "",
        }
        await _safe_delete(ctx.message)
    except Exception as e:
        await ctx.reply(f"❌ Error: {type(e).__name__}: {e}", mention_author=False)

# ------------------- Reaction flip: 💡 -------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        # ignore DMs / self / non-bulb
        if not payload.guild_id or payload.user_id == (bot.user.id if bot.user else None):
            return
        if str(payload.emoji) != "💡":
            return

        info = REACT_INDEX.get(payload.message_id)
        if not info:
            return

        guild = bot.get_guild(info["guild_id"]) if info.get("guild_id") else bot.get_guild(payload.guild_id)
        channel = bot.get_channel(info["channel_id"]) or await bot.fetch_channel(info["channel_id"])
        try:
            msg = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        row = info["row"]

        if info["kind"] == "entry_from_profile":
            # Profile → show Entry Criteria in-place
            embed = make_embed_for_row_search(row, info.get("filters", ""), guild)
            ft = embed.footer.text or ""
            hint = "React with 💡 for Clan Profile"
            embed.set_footer(text=(f"{ft} • {hint}" if ft else hint))

            await msg.edit(embed=embed)
            info["kind"] = "profile_from_search"          # flip the kind for next toggle
            REACT_INDEX[payload.message_id] = info

        else:  # "profile_from_search" → show Profile in-place
            embed = make_embed_for_profile(row, guild)
            await msg.edit(embed=embed)
            info["kind"] = "entry_from_profile"           # flip back
            REACT_INDEX[payload.message_id] = info

        # Let users press 💡 again without removing it manually
        try:
            user = payload.member or (guild and guild.get_member(payload.user_id))
            if user:
                await msg.remove_reaction(payload.emoji, user)
        except Exception:
            pass

    except Exception as e:
        print("[react] error:", e)

# ------------------- Health / reload -------------------
@bot.command(name="ping")
async def ping(ctx):
    # Admin or Recruitment Lead only
    if not isinstance(ctx.author, discord.Member) or not _allowed_admin_or_lead(ctx.author):
        await ctx.reply("⚠️ Only **Recruitment Lead** or Admins can use `!ping`.", mention_author=False)
        return
    await ctx.send("✅ I’m alive and listening, captain!")


@bot.command(name="health", aliases=["status"])
async def health_prefix(ctx: commands.Context):
    # Admin or Recruitment Lead only
    if not isinstance(ctx.author, discord.Member) or not _allowed_admin_or_lead(ctx.author):
        await ctx.reply("⚠️ Only **Recruitment Lead** or Admins can use `!health`.", mention_author=False)
        return
    try:
        try:
            ws = get_ws(False)
            _ = ws.row_values(1)
            sheets_status = f"OK (`{WORKSHEET_NAME}`)"
        except Exception as e:
            sheets_status = f"ERROR: {type(e).__name__}"
        latency_ms = round(bot.latency * 1000) if bot.latency is not None else -1
        await ctx.reply(f"🟢 Bot OK | Latency: {latency_ms} ms | Sheets: {sheets_status} | Uptime: {_fmt_uptime()}",
                        mention_author=False)
        await _safe_delete(ctx.message)
    except Exception as e:
        await ctx.reply(f"⚠️ Health error: `{type(e).__name__}: {e}`", mention_author=False)

@bot.command(name="reload")
async def reload_cache_cmd(ctx):
    # Admin or Recruitment Lead only
    if not isinstance(ctx.author, discord.Member) or not _allowed_admin_or_lead(ctx.author):
        await ctx.reply("⚠️ Only **Recruitment Lead** or Admins can use `!reload`.", mention_author=False)
        return
    clear_cache()
    await ctx.send("♻️ Sheet cache cleared. Next search will fetch fresh data.")
    await _safe_delete(ctx.message)

# ------------------- Events -------------------
@bot.event
async def on_ready():
    print(f"[ready] Logged in as {bot.user} ({bot.user.id})", flush=True)
    try:
        synced = await bot.tree.sync()
        print(f"[slash] synced {len(synced)} commands", flush=True)
    except Exception as e:
        print(f"[slash] sync failed: {e}", flush=True)

    # NEW: kick off the daily poster (safe to call repeatedly)
    if not daily_recruiters_update.is_running():
        daily_recruiters_update.start()

# ------------------- Tiny web server + image-pad proxy -------------------
async def _health_http(_req): return web.Response(text="ok")

async def emoji_pad_handler(request: web.Request):
    """
    /emoji-pad?u=<emoji_cdn_url>&s=<int canvas>&box=<0..1 glyph fraction>&v=<cache-buster>
    Downloads the emoji, trims transparent borders, scales to (s*box), centers on s×s canvas.
    """
    src = request.query.get("u")
    size = int(request.query.get("s", str(EMOJI_PAD_SIZE)))
    box  = float(request.query.get("box", str(EMOJI_PAD_BOX)))
    if not src:
        return web.Response(status=400, text="missing u")
    try:
        async with request.app["session"].get(src) as resp:
            if resp.status != 200:
                return web.Response(status=resp.status, text=f"fetch failed: {resp.status}")
            raw = await resp.read()

        img = Image.open(io.BytesIO(raw)).convert("RGBA")

        # Trim transparent borders so glyph is truly centered
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        if bbox:
            img = img.crop(bbox)

        # Scale glyph to fit target “box” inside the square canvas
        w, h = img.size
        max_side = max(w, h)
        target   = max(1, int(size * box))
        scale    = target / float(max_side)
        new_w    = max(1, int(w * scale))
        new_h    = max(1, int(h * scale))
        img = img.resize((new_w, new_h), RESAMPLE_LANCZOS)

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
    except Exception as e:
        return web.Response(status=500, text=f"err {type(e).__name__}: {e}")

async def start_webserver():
    app = web.Application()
    app["session"] = ClientSession()
    async def _close_session(app):
        await app["session"].close()
    app.on_cleanup.append(_close_session)
    app.router.add_get("/", _health_http)
    app.router.add_get("/health", _health_http)
    app.router.add_get("/emoji-pad", emoji_pad_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[keepalive] HTTP server listening on :{port}", flush=True)


# ------------------- Boot both -------------------
async def main():
    try:
        asyncio.create_task(start_webserver())
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token or len(token) < 50:
            raise RuntimeError("Missing/short DISCORD_TOKEN.")
        print("[boot] starting discord bot…", flush=True)
        await bot.start(token)
    except Exception as e:
        print("[boot] FATAL:", e, flush=True)
        traceback.print_exc()
        raise

if __name__ == "__main__":
    asyncio.run(main())


























