# bot_clanmatch_prefix.py

import os, json, time, asyncio, re
import discord
from discord.ext import commands
from collections import defaultdict
import gspread
from google.oauth2.service_account import Credentials
from aiohttp import web
from discord import InteractionResponded

# ---------- Google Sheets (Render-safe) ----------
CREDS_JSON = os.environ.get("GSPREAD_CREDENTIALS")
if not CREDS_JSON:
    raise RuntimeError("Missing GSPREAD_CREDENTIALS env var.")
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
if not SHEET_ID:
    raise RuntimeError("Missing GOOGLE_SHEET_ID env var.")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "bot_info")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)
gc = gspread.authorize(creds)
ws = gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)

# ---------- Column map (0-based) ----------
COL_B_CLAN, COL_C_TAG, COL_E_SPOTS = 1, 2, 4
# Filters P–U
COL_P_CB, COL_Q_HYDRA, COL_R_CHIM, COL_S_CVC, COL_T_SIEGE, COL_U_STYLE = 15, 16, 17, 18, 19, 20
# Entry Criteria V–AB
IDX_V, IDX_W, IDX_X, IDX_Y, IDX_Z, IDX_AA, IDX_AB = 21, 22, 23, 24, 25, 26, 27

# ---------- Matching helpers ----------
def norm(s: str) -> str:
    return (s or "").strip().upper()

TOKEN_MAP = {"EASY":"ESY","NORMAL":"NML","HARD":"HRD","BRUTAL":"BTL","NM":"NM","UNM":"UNM","ULTRA-NIGHTMARE":"UNM"}
def map_token(choice: str) -> str:
    c = norm(choice)
    return TOKEN_MAP.get(c, c)

def cell_has_diff(cell_text: str, token: str | None) -> bool:
    if not token:
        return True
    t = map_token(token)
    c = norm(cell_text)
    return (t in c or (t=="HRD" and "HARD" in c) or (t=="NML" and "NORMAL" in c) or (t=="BTL" and "BRUTAL" in c))

def cell_equals_10(cell_text: str, expected: str | None) -> bool:
    if expected is None:
        return True
    return (cell_text or "").strip() == expected  # exact 1/0

def playstyle_ok(cell_text: str, value: str | None) -> bool:
    if not value:
        return True
    return norm(value) in norm(cell_text)

def parse_spots_num(cell_text: str) -> int:
    """Extract first integer from the 'Spots' cell (E). Non-numeric => 0."""
    m = re.search(r"\d+", cell_text or "")
    return int(m.group()) if m else 0

def row_matches(row, cb, hydra, chimera, cvc, siege, playstyle) -> bool:
    if len(row) <= IDX_AB or not row[COL_B_CLAN].strip():
        return False
    return (
        cell_has_diff(row[COL_P_CB], cb) and
        cell_has_diff(row[COL_Q_HYDRA], hydra) and
        cell_has_diff(row[COL_R_CHIM], chimera) and
        cell_equals_10(row[COL_S_CVC], cvc) and
        cell_equals_10(row[COL_T_SIEGE], siege) and
        playstyle_ok(row[COL_U_STYLE], playstyle)
    )

# ---------- Formatting ----------
def build_entry_criteria(row) -> str:
    parts = []
    v = row[IDX_V].strip(); w = row[IDX_W].strip()
    x = row[IDX_X].strip(); y = row[IDX_Y].strip(); z = row[IDX_Z].strip()
    aa = row[IDX_AA].strip(); ab = row[IDX_AB].strip()
    if v:  parts.append(f"Hydra keys: {v}")
    if w:  parts.append(f"Chimera keys: {w}")
    if x:  parts.append(x)
    if y:  parts.append(y)
    if z:  parts.append(z)
    if aa: parts.append(f"non PR CvC: {aa}")
    if ab: parts.append(f"PR CvC: {ab}")
    return "**Entry Criteria:** " + (" | ".join(parts) if parts else "—")

def format_filters_footer(cb, hydra, chimera, cvc, siege, playstyle, hide_full) -> str:
    parts = []
    if cb: parts.append(f"CB: {cb}")
    if hydra: parts.append(f"Hydra: {hydra}")
    if chimera: parts.append(f"Chimera: {chimera}")
    if cvc is not None:   parts.append(f"CvC: {'Yes' if cvc == '1' else 'No'}")
    if siege is not None: parts.append(f"Siege: {'Yes' if siege == '1' else 'No'}")
    if playstyle: parts.append(f"Playstyle: {playstyle}")
    parts.append(f"Hide full: {'On' if hide_full else 'Off'}")
    return " • ".join(parts)

def make_embed_for_row(row, filters_text: str) -> discord.Embed:
    clan  = row[COL_B_CLAN].strip()
    tag   = row[COL_C_TAG].strip()
    spots = row[COL_E_SPOTS].strip()
    title = f"{clan}  `{tag}`  — Spots: {spots}"   # big line
    desc  = build_entry_criteria(row)
    e = discord.Embed(title=title, description=desc)
    e.set_footer(text=f"Filters used: {filters_text}")
    return e

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# anti-dupe guard (one panel per user + short cooldown)
LAST_CALL = defaultdict(float)
ACTIVE_PANELS = {}
COOLDOWN_SEC = 2.0

CB_CHOICES        = ["Hard", "Brutal", "NM", "UNM"]
HYDRA_CHOICES     = ["Normal", "Hard", "Brutal", "NM", "UNM"]
CHIMERA_CHOICES   = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
PLAYSTYLE_CHOICES = ["stress-free", "Casual", "Semi Competitive", "Competitive"]

class ClanMatchView(discord.ui.View):
    """5 rows total: four selects + one row with five buttons (CvC, Siege, Hide full, Reset, Search)."""
    def __init__(self, author_id: int):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cb = None; self.hydra = None; self.chimera = None; self.playstyle = None
        self.cvc = None; self.siege = None  # "1"/"0"/None
        self.hide_full = False              # filter out 0-spot clans

    # --- visual sync so selects and toggles reflect current state ---
    def _sync_visuals(self):
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                chosen = None
                ph = child.placeholder or ""
                if "CB Difficulty" in ph: chosen = self.cb
                elif "Hydra Difficulty" in ph: chosen = self.hydra
                elif "Chimera Difficulty" in ph: chosen = se
