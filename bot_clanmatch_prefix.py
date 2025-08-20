# bot_clanmatch_prefix.py

import os, json, time, asyncio, re, traceback
import discord
from discord.ext import commands
from discord import InteractionResponded
from collections import defaultdict
import gspread
from google.oauth2.service_account import Credentials
from aiohttp import web

# ------------------- boot/uptime -------------------
START_TS = time.time()

# ------------------- ENV -------------------
CREDS_JSON = os.environ.get("GSPREAD_CREDENTIALS")
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "bot_info")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

if not CREDS_JSON:
    print("[boot] GSPREAD_CREDENTIALS missing", flush=True)
if not SHEET_ID:
    print("[boot] GOOGLE_SHEET_ID missing", flush=True)
print(f"[boot] WORKSHEET_NAME={WORKSHEET_NAME}", flush=True)

# ------------------- Sheets (lazy + cache) -------------------
_gc = None
_ws = None
_cache_rows = None
_cache_time = 0.0
CACHE_TTL = 60  # seconds

def _fmt_uptime():
    secs = int(time.time() - START_TS)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def get_ws(force=False):
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

def get_rows(force=False):
    """Return all rows with simple 60s cache."""
    global _cache_rows, _cache_time
    if force or _cache_rows is None or (time.time() - _cache_time) > CACHE_TTL:
        ws = get_ws(force=False)
        _cache_rows = ws.get_all_values()
        _cache_time = time.time()
    return _cache_rows

def clear_cache():
    global _cache_rows, _cache_time, _ws
    _cache_rows = None
    _cache_time = 0.0
    _ws = None  # reconnect next time

# ------------------- Column map (0-based) -------------------
COL_B_CLAN, COL_C_TAG, COL_E_SPOTS = 1, 2, 4
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

# ------------------- Formatting -------------------
def build_entry_criteria(row) -> str:
    """
    V/W labeled (not bold); X/Y/Z verbatim; AA/AB labeled (not bold).
    Wider spacing between items via NBSP around the pipe.
    """
    NBSP_PIPE = "\u00A0|\u00A0"  # non-breaking spaces around the pipe
    parts = []

    v  = (row[IDX_V]  or "").strip()   # Hydra keys
    w  = (row[IDX_W]  or "").strip()   # Chimera keys
    x  = (row[IDX_X]  or "").strip()   # Hydra Clash (verbatim)
    y  = (row[IDX_Y]  or "").strip()   # Chimera Clash (verbatim)
    z  = (row[IDX_Z]  or "").strip()   # CB Damage (verbatim)
    aa = (row[IDX_AA] or "").strip()   # non PR CvC
    ab = (row[IDX_AB] or "").strip()   # PR CvC

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

def make_embed_for_row(row, filters_text: str) -> discord.Embed:
    """Header shows Reserved (AC) when present; body adds AE and AD lines with blank-line spacing."""
    clan     = (row[COL_B_CLAN] or "").strip()
    tag      = (row[COL_C_TAG]  or "").strip()
    spots    = (row[COL_E_SPOTS] or "").strip()
    reserved = (row[IDX_AC_RESERVED] or "").strip()        # AC
    comments = (row[IDX_AD_COMMENTS] or "").strip()        # AD
    addl_req = (row[IDX_AE_REQUIREMENTS] or "").strip()    # AE

    title = f"{clan}  `{tag}`  — Spots: {spots}"
    if reserved:
        title += f" | Reserved: {reserved}"

    # Blank line between sections
    sections = [build_entry_criteria(row)]
    if addl_req:
        sections.append(f"**Additional Requirements:** {addl_req}")
    if comments:
        sections.append(f"**Clan Needs/Comments:** {comments}")

    e = discord.Embed(title=title, description="\n\n".join(sections))
    e.set_footer(text=f"Filters used: {filters_text}")
    return e

# ------------------- Discord bot -------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

LAST_CALL = defaultdict(float)
ACTIVE_PANELS = {}
COOLDOWN_SEC = 2.0

CB_CHOICES        = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
HYDRA_CHOICES     = ["Normal", "Hard", "Brutal", "NM"]
CHIMERA_CHOICES   = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
PLAYSTYLE_CHOICES = ["stress-free", "Casual", "Semi Competitive", "Competitive"]

class ClanMatchView(discord.ui.View):
    """4 selects + one row of buttons (CvC, Siege, Roster, Reset, Search)."""
    def __init__(self, author_id: int):
        super().__init__(timeout=1800)  # 30 min
        self.author_id = author_id
        self.cb = None; self.hydra = None; self.chimera = None; self.playstyle = None
        self.cvc = None; self.siege = None
        self.roster_mode: str | None = None   # None = All, 'open' = Spots > 0, 'full' = Spots <= 0
        self.message: discord.Message | None = None  # set after sending

    # on-timeout: disable + mark expired
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if self.message:
                expired = discord.Embed(
                    title="Find a C1C Clan",
                    description="⏳ Panel expired. Run `!clanmatch` to open a fresh one."
                )
                await self.message.edit(embed=expired, view=self)
        except Exception as e:
            print("[view timeout] failed to edit:", e)

    # visual sync so selects and toggles reflect current state
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
                    if self.roster_mode is None:
                        child.label = "Roster: All"
                        child.style = discord.ButtonStyle.secondary
                    elif self.roster_mode == "open":
                        child.label = "Roster: Open only"
                        child.style = discord.ButtonStyle.success
                    else:  # 'full'
                        child.label = "Roster: Full only"
                        child.style = discord.ButtonStyle.primary

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.author_id:
            await itx.response.send_message("This panel isn’t yours—run `!clanmatch` to get your own. 🙂", ephemeral=True)
            return False
        return True

    # Row 0–3: selects
    @discord.ui.select(placeholder="CB Difficulty (optional)", min_values=0, max_values=1, row=0,
                       options=[discord.SelectOption(label=o, value=o) for o in CB_CHOICES])
    async def cb_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.cb = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Hydra Difficulty (optional)", min_values=0, max_values=1, row=1,
                       options=[discord.SelectOption(label=o, value=o) for o in HYDRA_CHOICES])
    async def hydra_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.hydra = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Chimera Difficulty (optional)", min_values=0, max_values=1, row=2,
                       options=[discord.SelectOption(label=o, value=o) for o in CHIMERA_CHOICES])
    async def chimera_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.chimera = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Playstyle (optional)", min_values=0, max_values=1, row=3,
                       options=[discord.SelectOption(label=o, value=o) for o in PLAYSTYLE_CHOICES])
    async def playstyle_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.playstyle = select.values[0] if select.values else None
        await itx.response.defer()

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

    @discord.ui.button(label="Siege: —", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_siege(self, itx: discord.Interaction, button: discord.ui.Button):
        self.siege = self._cycle(self.siege); self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
                await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Roster: All", style=discord.ButtonStyle.secondary, row=4, custom_id="roster_btn")
    async def toggle_roster(self, itx: discord.Interaction, button: discord.ui.Button):
        # Cycle: None -> 'open' -> 'full' -> None
        if self.roster_mode is None:
            self.roster_mode = "open"
        elif self.roster_mode == "open":
            self.roster_mode = "full"
        else:
            self.roster_mode = None
        self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, row=4)
    async def reset_filters(self, itx: discord.Interaction, _btn: discord.ui.Button):
        self.cb = self.hydra = self.chimera = self.playstyle = None
        self.cvc = self.siege = None
        self.roster_mode = None
        self._sync_visuals()
        try:    await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Search Clans", style=discord.ButtonStyle.primary, row=4)
    async def search(self, itx: discord.Interaction, _btn: discord.ui.Button):
        # Require at least one filter (including roster mode)
        if not any([
            self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle,
            self.roster_mode is not None
        ]):
            await itx.response.send_message("Pick at least **one** filter, then try again. 🙂")
            return

        await itx.response.defer(thinking=True)  # public results
        try:
            rows = get_rows(force=False)
        except Exception as e:
            await itx.followup.send(f"❌ Failed to read sheet: {e}")
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

        if not matches:
            await itx.followup.send("No matching clans found. Try a different combo.")
            return

        filters_text = format_filters_footer(
            self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.roster_mode
        )
        for i in range(0, len(matches), 10):
            chunk = matches[i:i+10]
            embeds = [make_embed_for_row(r, filters_text) for r in chunk]
            await itx.followup.send(embeds=embeds)

# ------------------- Commands -------------------
@commands.cooldown(1, 2, commands.BucketType.user)
@bot.command(name="clanmatch")
async def clanmatch_cmd(ctx: commands.Context):
    now = time.time()
    if now - LAST_CALL.get(ctx.author.id, 0) < COOLDOWN_SEC:
        return
    LAST_CALL[ctx.author.id] = now

    view = ClanMatchView(author_id=ctx.author.id)
    view._sync_visuals()

    embed = discord.Embed(
        title="Find a C1C Clan for your recruit",
        description=(
            "Pick any filters (you can leave some blank) and click **Search Clans**.\n"
            "**Tip:** choose the most important criteria for your recruit — *but don’t go overboard*. "
            "Too many filters might narrow things down to zero."
        )
    )

    # Try to edit your previous panel in place; if not found, send a new one
    old_id = ACTIVE_PANELS.get(ctx.author.id)
    if old_id:
        try:
            msg = await ctx.channel.fetch_message(old_id)
            view.message = msg
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass

    sent = await ctx.reply(embed=embed, view=view, mention_author=False)
    view.message = sent
    ACTIVE_PANELS[ctx.author.id] = sent.id

@clanmatch_cmd.error
async def clanmatch_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        return

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("✅ I’m alive and listening, captain!")

# Health (prefix)
@bot.command(name="health", aliases=["status"])
async def health_prefix(ctx: commands.Context):
    """Lightweight health check with hard fail-safes."""
    try:
        try:
            ws = get_ws(force=False)
            _ = ws.row_values(1)  # tiny read
            sheets_status = f"OK (`{WORKSHEET_NAME}`)"
        except Exception as e:
            sheets_status = f"ERROR: {type(e).__name__}"

        latency_ms = round(bot.latency * 1000) if bot.latency is not None else -1
        msg = f"🟢 Bot OK | Latency: {latency_ms} ms | Sheets: {sheets_status} | Uptime: {_fmt_uptime()}"
        await ctx.reply(msg, mention_author=False)
    except Exception as e:
        await ctx.reply(f"⚠️ Health error: `{type(e).__name__}: {e}`", mention_author=False)

# Reload cache
@bot.command(name="reload")
async def reload_cache(ctx):
    clear_cache()
    await ctx.send("♻️ Sheet cache cleared. Next search will fetch fresh data.")

# Health (slash)
@bot.tree.command(name="health", description="Bot & Sheets status")
async def health_slash(itx: discord.Interaction):
    await itx.response.defer(thinking=False, ephemeral=False)
    try:
        ws = get_ws(force=False)
        _ = ws.row_values(1)
        sheets_status = f"OK (`{WORKSHEET_NAME}`)"
    except Exception as e:
        sheets_status = f"ERROR: {e.__class__.__name__}"
    latency_ms = int(bot.latency * 1000) if bot.latency else -1
    await itx.followup.send(f"🟢 Bot OK | Latency: {latency_ms} ms | Sheets: {sheets_status} | Uptime: {_fmt_uptime()}")

# ------------------- Events -------------------
@bot.event
async def on_ready():
    print(f"[ready] Logged in as {bot.user} ({bot.user.id})", flush=True)
    # sync slash commands (so /health shows up)
    try:
        synced = await bot.tree.sync()
        print(f"[slash] synced {len(synced)} commands", flush=True)
    except Exception as e:
        print(f"[slash] sync failed: {e}", flush=True)

# ------------------- Tiny web server (Render port) -------------------
async def _health_http(_req): return web.Response(text="ok")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", _health_http)
    app.router.add_get("/health", _health_http)
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
