# bot_clanmatch_prefix.py
# C1C Matchmaker — recruiter & user panels, strict GSheets, hybrid thread routing.
# Commands:
#   !clanmatch  -> recruiter panel (hybrid: per-recruiter thread if role; else fixed thread)
#   !clansearch -> user panel (in invoking channel)
#   !clan       -> clan profile (tag or name)
#
# Behaviors:
#   - Search posts ALL results in ONE message (chunks if >10 embeds)
#   - Reset also deletes the last results message
#   - Expire/Close disables panel, wipes results, shows "Reload new search"
#   - Pointer: when panel appears in a thread, the invoking channel gets a link that self-deletes
#
# Requires: discord.py v2.x, aiohttp; optional gspread (for Google Sheets)

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
CLANS_WORKSHEET_NAME = os.environ.get("CLANS_WORKSHEET_NAME", "Clans")
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
    """Google Sheets only. Raise on misconfig/unavailable."""
    if not USE_GSHEETS:
        log.error("Sheets misconfigured: set GSPREAD_CREDENTIALS & GOOGLE_SHEET_ID (& CLANS_WORKSHEET_NAME).")
        raise RuntimeError("DATA_SOURCE_MISCONFIGURED")
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
        log.exception("Sheets error:")
        raise RuntimeError("DATA_SOURCE_UNAVAILABLE") from e

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
#
