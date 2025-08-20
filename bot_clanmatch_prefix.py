import os, json
import discord
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials

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
COL_B_CLAN, COL_C_TAG, COL_D_SPOTS = 1, 2, 3
# Filters P–U
COL_P_CB, COL_Q_HYDRA, COL_R_CHIM, COL_S_CVC, COL_T_SIEGE, COL_U_STYLE = 15, 16, 17, 18, 19, 20
# Entry Criteria V–AB
IDX_V, IDX_W, IDX_X, IDX_Y, IDX_Z, IDX_AA, IDX_AB = 21, 22, 23, 24, 25, 26, 27

def norm(s: str) -> str:
    return (s or "").strip().upper()

TOKEN_MAP = {
    "EASY": "ESY", "NORMAL": "NML", "HARD": "HRD", "BRUTAL": "BTL",
    "NM": "NM", "UNM": "UNM", "ULTRA-NIGHTMARE": "UNM",
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

def playstyle_ok(cell_text: str, value: str | None) -> bool:
    if not value:
        return True
    return norm(value) in norm(cell_text)

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

def build_entry_criteria(row) -> str:
    # V/W labeled; X/Y/Z raw; AA/AB labeled; echo exact cell text
    parts = []
    v = row[IDX_V].strip()
    w = row[IDX_W].strip()
    x = row[IDX_X].strip()
    y = row[IDX_Y].strip()
    z = row[IDX_Z].strip()
    aa = row[IDX_AA].strip()
    ab = row[IDX_AB].strip()
    if v:  parts.append(f"Hydra keys: {v}")
    if w:  parts.append(f"Chimera keys: {w}")
    if x:  parts.append(x)
    if y:  parts.append(y)
    if z:  parts.append(z)
    if aa: parts.append(f"non PR CvC: {aa}")
    if ab: parts.append(f"PR CvC: {ab}")
    return "**Entry Criteria:** " + (" | ".join(parts) if parts else "—")

def format_row_block(row) -> str:
    clan  = row[COL_B_CLAN].strip()
    tag   = row[COL_C_TAG].strip()
    spots = row[COL_D_SPOTS].strip()
    return f"**{clan}**  `{tag}`  — Spots: {spots}\n{build_entry_criteria(row)}"

def format_filters_footer(cb, hydra, chimera, cvc, siege, playstyle) -> str:
    parts = []
    if cb: parts.append(f"CB: {cb}")
    if hydra: parts.append(f"Hydra: {hydra}")
    if chimera: parts.append(f"Chimera: {chimera}")
    if cvc is not None: parts.append(f"CvC: {cvc}")
    if siege is not None: parts.append(f"Siege: {siege}")
    if playstyle: parts.append(f"Playstyle: {playstyle}")
    return " • ".join(parts) if parts else "—"

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

CB_CHOICES        = ["Hard", "Brutal", "NM", "UNM"]
HYDRA_CHOICES     = ["Normal", "Hard", "Brutal", "NM", "UNM"]
CHIMERA_CHOICES   = ["Easy", "Normal", "Hard", "Brutal", "NM", "UNM"]
YESNO_OPTIONS     = [("Yes (1)", "1"), ("No (0)", "0")]
PLAYSTYLE_CHOICES = ["stress-free", "Casual", "Semi Competitive", "Competitive"]

class ClanMatchView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cb = None
        self.hydra = None
        self.chimera = None
        self.cvc = None     # "1"/"0"
        self.siege = None   # "1"/"0"
        self.playstyle = None

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.author_id:
            await itx.response.send_message("This panel isn’t yours—run `!clanmatch` to get your own. 🙂", ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="CB Difficulty (optional)", min_values=0, max_values=1,
                       options=[discord.SelectOption(label=o, value=o) for o in CB_CHOICES])
    async def cb_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.cb = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Hydra Difficulty (optional)", min_values=0, max_values=1,
                       options=[discord.SelectOption(label=o, value=o) for o in HYDRA_CHOICES])
    async def hydra_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.hydra = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Chimera Difficulty (optional)", min_values=0, max_values=1,
                       options=[discord.SelectOption(label=o, value=o) for o in CHIMERA_CHOICES])
    async def chimera_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.chimera = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="CvC Interest (Yes=1 / No=0) — optional", min_values=0, max_values=1,
                       options=[discord.SelectOption(label=l, value=v) for l, v in YESNO_OPTIONS])
    async def cvc_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.cvc = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Siege Interest (Yes=1 / No=0) — optional", min_values=0, max_values=1,
                       options=[discord.SelectOption(label=l, value=v) for l, v in YESNO_OPTIONS])
    async def siege_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.siege = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.select(placeholder="Playstyle (optional)", min_values=0, max_values=1,
                       options=[discord.SelectOption(label=o, value=o) for o in PLAYSTYLE_CHOICES])
    async def playstyle_select(self, itx: discord.Interaction, select: discord.ui.Select):
        self.playstyle = select.values[0] if select.values else None
        await itx.response.defer()

    @discord.ui.button(label="Search Clans", style=discord.ButtonStyle.primary)
    async def search(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not any([self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle]):
            await itx.response.send_message("Pick at least **one** filter, then try again. 🙂", ephemeral=True)
            return

        await itx.response.defer(thinking=True, ephemeral=True)
        try:
            rows = ws.get_all_values()
        except Exception as e:
            await itx.followup.send(f"❌ Failed to read sheet: {e}", ephemeral=True)
            return

        data_rows = rows[1:]
        matches = []
        for row in data_rows:
            try:
                if row_matches(row, self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle):
                    matches.append(row)
            except Exception:
                continue

        if not matches:
            await itx.followup.send("No matching clans found. Try a different combo.", ephemeral=True)
            return

        filters_text = format_filters_footer(self.cb, self.hydra, self.chimera, self.cvc, self.siege, self.playstyle)
        chunks = [matches[i:i+10] for i in range(0, len(matches), 10)]
        for idx, chunk in enumerate(chunks, start=1):
            desc = "\n\n".join(format_row_block(r) for r in chunk)
            embed = discord.Embed(title=f"Clan Matches (page {idx}/{len(chunks)})", description=desc)
            embed.set_footer(text=f"Filters used: {filters_text}")
            await itx.followup.send(embed=embed, ephemeral=True)

# ---------- Prefix command to open the panel ----------
@bot.command(name="clanmatch")
async def clanmatch_cmd(ctx: commands.Context):
    view = ClanMatchView(author_id=ctx.author.id)
    embed = discord.Embed(
        title="Find a C1C Clan",
        description=(
            "Choose any filters you like (leave others blank) and click **Search Clans**.\n"
            "Filters: **P–U** (CB/Hydra/Chimera + CvC(1/0) + Siege(1/0) + Playstyle)\n"
            "Output: **B/C/D** + **V–AB** as Entry Criteria."
        )
    )
    await ctx.reply(embed=embed, view=view, mention_author=False)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} — prefix commands ready.")

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN env var.")
    bot.run(token)
