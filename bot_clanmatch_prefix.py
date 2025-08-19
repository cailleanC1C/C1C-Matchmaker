# bot_clanmatch_prefix.py

import os
import json
import discord
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials

# ----------------------------
# Google Sheets setup
# ----------------------------
CREDS_JSON = os.environ.get("GSPREAD_CREDENTIALS")
if not CREDS_JSON:
    raise RuntimeError("Missing GSPREAD_CREDENTIALS env var")

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
if not SHEET_ID:
    raise RuntimeError("Missing GOOGLE_SHEET_ID env var")

# Your worksheet/tab name
WORKSHEET_NAME = "bot_info"

# Build credentials & client
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)
gc = gspread.authorize(creds)

try:
    ws = gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
    print(f"[Sheets] Connected to '{WORKSHEET_NAME}' in {SHEET_ID[:6]}… OK")
except Exception as e:
    raise RuntimeError(f"[Sheets] Failed to open sheet: {e}")

# ----------------------------
# Discord Bot setup
# ----------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ----------------------------
# Events
# ----------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

# ----------------------------
# Commands
# ----------------------------
@bot.command(name="clanmatch")
async def clanmatch(ctx, *, query: str = None):
    """
    Look up clans or members from the Google Sheet (tab: bot_info).
    Example: !clanmatch Invictus
    """
    try:
        data = ws.get_all_records()
    except Exception as e:
        await ctx.send(f"❌ Failed to read sheet: {e}")
        return

    if not query:
        await ctx.send("⚔️ Please provide a search term, e.g. `!clanmatch Invictus`")
        return

    query_lower = query.lower()
    matches = [row for row in data if query_lower in str(row).lower()]

    if not matches:
        await ctx.send(f"😢 No matches found for **{query}**")
        return

    # Limit to 5 matches to avoid spamming
    matches = matches[:5]

    embed = discord.Embed(
        title=f"🔎 Clanmatch Results for '{query}'",
        color=discord.Color.blue()
    )

    for row in matches:
        # Adjust field names to your Google Sheet headers
        clan = row.get("Clan Name", "Unknown Clan")
        level = row.get("Level", "n/a")
        reqs = row.get("Requirements", "n/a")

        embed.add_field(
            name=f"{clan} (Lvl {level})",
            value=f"Requirements: {reqs}",
            inline=False
        )

    await ctx.send(embed=embed)

# ----------------------------
# Run Bot
# ----------------------------
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN env var")
    bot.run(TOKEN)
