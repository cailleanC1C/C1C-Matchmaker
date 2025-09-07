# bot_clanmatch_prefix.py
# C1C-Matchmaker — panels, search, profiles, emoji padding, and reaction flip (💡)
# Patched: resilient Sheets fallback (HTTP API) + !sheetdiag + SSRF-safe emoji pad

import os, json, time, asyncio, re, traceback, urllib.parse, io
from collections import defaultdict

import discord
from discord.ext import commands
from discord import InteractionResponded
from discord.utils import get

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, WorksheetNotFound

import requests
from google.auth.transport.requests import Request  # for token refresh

from aiohttp import web, ClientSession
from PIL import Image  # Pillow

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
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

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

# ------------------- Sheets (resilient + cache) -------------------
_gc = None
_ws = None
_cache_rows = None
_cache_time = 0.0
CACHE_TTL = 60  # seconds

def _build_creds():
    return Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)

def _get_bearer_token():
    creds = _build_creds()
    creds.refresh(Request())
    return creds.token

def _sheets_values_get(sheet_id: str, tab: str):
    """Read values via Sheets HTTP API v4 (fallback when gspread is cranky)."""
    token = _get_bearer_token()
    rng = f"{tab}!A1:ZZ9999"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{requests.utils.quote(rng, safe='!')}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Sheets values.get failed: {r.status_code} {r.text[:180]}")
    return r.json().get("values", []) or []

def _sheets_list_tabs(sheet_id: str):
    token = _get_bearer_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}?includeGridData=false"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Sheets meta get failed: {r.status_code} {r.text[:180]}")
    meta = r.json()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    title = meta.get("properties", {}).get("title", "(untitled)")
    return title, tabs

def get_ws(force: bool = False):
    """Connect to Google Sheets with gspread (primary path)."""
    global _gc, _ws
    if force:
        _ws = None
        _gc = None
    if _ws is not None:
        return _ws
    creds = _build_creds()
    _gc = gspread.authorize(creds)
    _ws = _gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
    print("[sheets] Connected to worksheet OK", flush=True)
    return _ws

def get_rows(force: bool = False):
    """Return all rows with 60s cache. Falls back to raw Sheets API on gspread errors."""
    global _cache_rows, _cache_time
    need_refresh = force or _cache_rows is None or (time.time() - _cache_time) > CACHE_TTL
    if not need_refresh:
        return _cache_rows
    try:
        ws = get_ws(False)
        _cache_rows = ws.get_all_values()
    except WorksheetNotFound:
        raise RuntimeError(f"Worksheet '{WORKSHEET_NAME}' not found. Rename the tab or set WORKSHEET_NAME.")
    except APIError as e:
        print(f"[sheets] gspread APIError -> fallback: {e}", flush=True)
        _cache_rows = _sheets_values_get(SHEET_ID, WORKSHEET_NAME)
    except Exception as e:
        print(f"[sheets] gspread unknown error -> fallback: {e}", flush=True)
        _cache_rows = _sheets_values_get(SHEET_ID, WORKSHEET_NAME)
    _cache_time = time.time()
    return _cache_rows

def clear_cache():
    global _cache_rows, _cache_time, _ws, _gc
    _cache_rows = None
    _cache_time = 0.0
    _ws = None  # reconnect next time
    _gc = None

# --- rest of your code continues here ---
# (keep everything else exactly as you had it:
# column maps, helpers, panel classes, commands, reaction handling, etc.)

# ------------------- Extra command: sheetdiag -------------------
@commands.has_permissions(administrator=True)
@bot.command(name="sheetdiag")
async def sheetdiag(ctx: commands.Context):
    """Prints spreadsheet title, tabs, and row count using fallback if needed."""
    try:
        rows = get_rows(force=True)
        title, tabs = _sheets_list_tabs(SHEET_ID)
        await ctx.reply("```\n"
                        f"Spreadsheet: {title}\n"
                        f"Tabs: {', '.join(tabs) or '(none)'}\n"
                        f"Target tab: {WORKSHEET_NAME}\n"
                        f"Row count: {len(rows)}\n"
                        "```", mention_author=False)
    except Exception as e:
        await ctx.reply(f"```diag error: {e}```", mention_author=False)

# ------------------- Emoji pad SSRF guard -------------------
ALLOWED_HOSTS = {"cdn.discordapp.com", "media.discordapp.net", "images-ext-1.discordapp.net"}

async def emoji_pad_handler(request: web.Request):
    src = request.query.get("u")
    size = int(request.query.get("s", str(EMOJI_PAD_SIZE)))
    box  = float(request.query.get("box", str(EMOJI_PAD_BOX)))
    if not src:
        return web.Response(status=400, text="missing u")
    try:
        host = urllib.parse.urlparse(src).hostname or ""
        if STRICT_EMOJI_PROXY and host not in ALLOWED_HOSTS:
            return web.Response(status=400, text="host not allowed")
    except Exception:
        return web.Response(status=400, text="bad url")
    # ... rest of your handler unchanged ...
