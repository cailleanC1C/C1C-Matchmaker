import discord
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials

# --- Google Sheets Setup ---
SHEET_ID = "1eFrh1e9ljzVpc5Vf9tuMLViah1aOp6E_CTGZ9nCPXuQ"
WORKSHEET_NAME = "Clan Data"

# Define the scope for Google Sheets API
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = Credentials.from_service_account_file("c1c-matchfinder.json", scopes=SCOPES)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)

# --- Discord Setup ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def format_entry_criteria(row):
    """Format entry criteria text block from columns V–AB."""
    hydra_keys = row[21].strip()
    chimera_keys = row[22].strip()
    hydra_clash = row[23].strip()
    chimera_clash = row[24].strip()
    cb_damage = row[25].strip()
    cvc_nonpr = row[26].strip()
    cvc_pr = row[27].strip()

    parts = []
    if hydra_keys:
        parts.append(f"Hydra keys: {hydra_keys}")
    if chimera_keys:
        parts.append(f"Chimera keys: {chimera_keys}")
    if hydra_clash:
        parts.append(hydra_clash)
    if chimera_clash:
        parts.append(chimera_clash)
    if cb_damage:
        parts.append(cb_damage)
    if cvc_nonpr:
        parts.append(f"non PR CvC: {cvc_nonpr}")
    if cvc_pr:
        parts.append(f"PR CvC: {cvc_pr}")

    return "**Entry Criteria:** " + " | ".join(parts)

def matches_filters(row, filters):
    """Check if row matches filter selections in columns M–R."""
    for col_index, f in filters.items():
        if f and f not in row[col_index]:
            return False
    return True

@bot.command(name="clanmatch")
async def clanmatch(ctx, cb=None, hydra=None, chimera=None, cvc=None, siege=None, playstyle=None):
    # Filters mapping → columns M–R (12–17 index base-0)
    filters = {
        12: cb,
        13: hydra,
        14: chimera,
        15: cvc,
        16: siege,
        17: playstyle,
    }

    rows = sheet.get_all_values()[1:]  # skip header
    results = []

    for row in rows:
        if matches_filters(row, filters):
            clan_name = row[1]
            clan_tag = row[2]
            lvl = row[3]
            entry_criteria = format_entry_criteria(row)
            results.append(f"**{clan_name}** [{clan_tag}] (Lvl {lvl})\n{entry_criteria}")

    if results:
        filters_used = [f"{k}={v}" for k, v in {
            "CB": cb, "Hydra": hydra, "Chimera": chimera, "CvC": cvc, "Siege": siege, "Playstyle": playstyle
        }.items() if v]
        await ctx.send("\n\n".join(results) + f"\n\n*Filters used:* {', '.join(filters_used) if filters_used else 'None'}")
    else:
        await ctx.send("No matching clans found for the selected filters.")

# --- Run bot ---
bot.run("YOUR_DISCORD_BOT_TOKEN")
