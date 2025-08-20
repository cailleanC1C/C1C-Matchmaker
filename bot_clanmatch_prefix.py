# bot_clanmatch_prefix.py

import os, json, time, asyncio, re, traceback
import discord
from discord.ext import commands
from collections import defaultdict
import gspread
from google.oauth2.service_account import Credentials
from aiohttp import web
from discord import InteractionResponded

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

# ------------------- Sheets (lazy) -------------------
_gc = None
_ws = None

def get_ws():
    """Connect to Google Sheets only when needed. Raises with a clear log."""
    global _gc, _ws
    if _ws is not None:
        return _ws
    try:
        creds = Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)
        _gc = gspread.authorize(creds)
        _ws = _gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
        print("[sheets] Connected to worksheet OK", flush=True)
        return _ws
    except Exception as e:
        print("[sheets] ERROR opening worksheet:", e, flush=True)
        traceback.print_exc()
        raise

# ------------------- Column map (0-based) -------------------
COL_B_CLAN, COL_C_TAG, COL_E_SPOTS = 1, 2, 4
# Filters P–U
COL_P_CB, COL_Q_HYDRA, COL_R_CHIM, COL_S_CVC, COL_T_SIEGE, COL_U_STYLE = 15, 16, 17, 18, 19, 20
# Entry Criteria V–AB
IDX_V, IDX_W, IDX_X, IDX_Y, IDX_Z, IDX_AA, IDX_AB = 21, 22, 23, 24, 25, 26, 27

# ------------------- Matching helpers -------------------
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
    m = re.search(r"\d+", cell_text or "")
    return int(m.group()) if m else 0

def row_matches(row, cb, hydra, chimera, cvc, siege, playstyle) -> bool:
    if len(row) <= IDX_AB or not (row[COL_B_CLAN] or "").strip():
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
    clan  = (row[COL_B_CLAN] or "").strip()
    tag   = (row[COL_C_TAG]  or "").strip()
    spots = (row[COL_E_SPOTS] or "").strip()
    title = f"{clan}  `{tag}`  — Spots: {spots}"
    desc  = build_entry_criteria(row)
    e = discord.Embed(title=title, description=desc)
    e.set_footer(text=f"Filters used: {filters_text}")
    return e

# ------------------- Discord bot -------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

LAST_CALL = defaultdict(float)
ACTIVE_PANELS = {}
COOLDOWN_SEC = 2.0

CB_CHOICES        = ["Easy", "Normal","Hard", "Brutal", "NM", "UNM"]
HYDRA_CHOICES     = ["Normal", "Hard", "Brutal", "NM", "UNM"]
CHIMERA_CHOICES   = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
PLAYSTYLE_CHOICES = ["stress-free", "Casual", "Semi Competitive", "Competitive"]

class ClanMatchView(discord.ui.View):
    """4 selects + one row of buttons (CvC, Siege, Hide full, Reset, Search)."""
    def __init__(self, author_id: int):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cb = None; self.hydra = None; self.chimera = None; self.playstyle = None
        self.cvc = None; self.siege = None
        self.hide_full = False

    def _sync_visuals(self):
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                chosen = None
                ph = child.placeholder or ""
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
                elif child.custom_id == "hide_full_btn":
                    child.label = f"Hide full: {'On' if self.hide_full else 'Off'}"
                    child.style = discord.ButtonStyle.success if self.hide_full else discord.ButtonStyle.secondary

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
        self.cvc = self._cycle(self.cvc)
        self._sync_visuals()
        try:
            await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Siege: —", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_siege(self, itx: discord.Interaction, button: discord.ui.Button):
        self.siege = self._cycle(self.siege)
        self._sync_visuals()
        try:
            await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Hide full: Off", style=discord.ButtonStyle.secondary, row=4, custom_id="hide_full_btn")
    async def toggle_hide_full(self, itx: discord.Interaction, button: discord.ui.Button):
        self.hide_full = not self.hide_full
        self._sync_visuals()
        try:
            await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, row=4)
    async def reset_filters(self, itx: discord.Interaction, _btn: discord.ui.Button):
        self.cb = self.hydra = self.chimera = self.playstyle = None
        self.cvc = self.siege = None
        self.hide_full = False
        self._sync_visuals()
        try:
            await itx.response.edit_message(view=self)
        except InteractionResponded:
            await itx.followup.edit_message(message_id=itx.message.id, view=self)

    @discord.ui.button(label="Search Clans", style=discord.ButtonStyle.primary, row=4)
    async def search(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not any([self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.hide_full]):
            await itx.response.send_message("Pick at least **one** filter, then try again. 🙂")
            return

        await itx.response.defer(thinking=True)  # public results
        try:
            ws = get_ws()
            rows = ws.get_all_values()
        except Exception as e:
            await itx.followup.send(f"❌ Failed to read sheet: {e}")
            return

        matches = []
        for row in rows[1:]:
            try:
                if row_matches(row, self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle):
                    if self.hide_full and parse_spots_num(row[COL_E_SPOTS]) <= 0:
                        continue
                    matches.append(row)
            except Exception:
                continue

        if not matches:
            await itx.followup.send("No matching clans found. Try a different combo.")
            return

        filters_text = format_filters_footer(self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle, self.hide_full)
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

    old_id = ACTIVE_PANELS.pop(ctx.author.id, None)
    if old_id:
        try:
            old_msg = await ctx.channel.fetch_message(old_id)
            await old_msg.delete()
        except Exception:
            pass

    view = ClanMatchView(author_id=ctx.author.id)
    view._sync_visuals()
    embed = discord.Embed(title="Find a C1C Clan", description="Pick at least one filter and click **Search Clans**.")
    sent = await ctx.reply(embed=embed, view=view, mention_author=False)
    ACTIVE_PANELS[ctx.author.id] = sent.id

@clanmatch_cmd.error
async def clanmatch_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        return

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("✅ I’m alive and listening, captain!")

# ------------------- Tiny web server (Render port) -------------------
async def _health(_req): return web.Response(text="ok")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
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

