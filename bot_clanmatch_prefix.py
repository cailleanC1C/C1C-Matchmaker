# bot_clanmatch_prefix.py
# C1C-Matchmaker — unified result posting, Reset deletion, and reload-on-expire
# Requires: discord.py v2.x

import os
import json
import asyncio
import logging
from typing import List, Dict, Optional

import discord
from discord.ext import commands
from discord.ui import View, button, Button, Modal, TextInput

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("matchmaker")

# ---------- ENV ----------
TOKEN = os.environ.get("DISCORD_TOKEN")  # required
PANEL_TIMEOUT_SECONDS = int(os.environ.get("PANEL_TIMEOUT_SECONDS", "600"))  # default 10 min

# Optional: Google Sheet search (fallbacks to demo data if not configured)
GSPREAD_CREDENTIALS = os.environ.get("GSPREAD_CREDENTIALS")  # JSON string (service account)
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")          # sheet id that holds clan data
CLANS_WORKSHEET_NAME = os.environ.get("CLANS_WORKSHEET_NAME", "Clans")

USE_GSHEETS = bool(GSPREAD_CREDENTIALS and GOOGLE_SHEET_ID)

# ---------- Discord Setup ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================================
#                 DATA ACCESS (SEARCH SOURCE)
# ============================================================

def _fallback_demo_data() -> List[Dict]:
    """Used if Google Sheets is not configured; safe demo."""
    return [
        {"Name": "Martyrs", "Tag": "MTR", "Focus": "Siege & Clash wins", "Vibe": "Piratey, fun-first, no shame tags"},
        {"Name": "Vagrants", "Tag": "VAG", "Focus": "Clash grinders", "Vibe": "Chill daily hitters"},
        {"Name": "Elders", "Tag": "ELD", "Focus": "Hydra/Chimera high tiers", "Vibe": "Competitive but kind"},
        {"Name": "Balors", "Tag": "BAL", "Focus": "Siege elite", "Vibe": "Bring your best gear"},
        {"Name": "Island Sacred", "Tag": "ISL", "Focus": "Hydra & teaching", "Vibe": "Island vibes, growth"},
    ]

def _normalize(s: Optional[str]) -> str:
    return (s or "").strip()

def _match_row(row: Dict[str, str], query: str) -> bool:
    q = query.lower()
    for v in row.values():
        if q in str(v).lower():
            return True
    return False

def fetch_clans(query: str) -> List[Dict]:
    """
    Return a list of rows (dicts) representing clans matching 'query'.
    If Google Sheets is configured, attempts to read all rows from the named worksheet
    and does a simple substring match across columns.
    Otherwise falls back to demo data.
    """
    query = _normalize(query)
    if not query:
        query = ""  # empty means "show many"

    if not USE_GSHEETS:
        rows = _fallback_demo_data()
        if not query:
            return rows
        return [r for r in rows if _match_row(r, query)]

    # ---- Google Sheets path ----
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_info = json.loads(GSPREAD_CREDENTIALS)
        creds = Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)
        ws = client.open_by_key(GOOGLE_SHEET_ID).worksheet(CLANS_WORKSHEET_NAME)
        all_values = ws.get_all_records()
        if not query:
            return all_values
        return [r for r in all_values if _match_row(r, query)]
    except Exception as e:
        log.exception("Failed to read from Google Sheets; falling back to demo data: %s", e)
        rows = _fallback_demo_data()
        if not query:
            return rows
        return [r for r in rows if _match_row(r, query)]

# ============================================================
#                     UI: MODALS & VIEWS
# ============================================================

class ClanSearchModal(Modal, title="C1C-Matchmaker • Search"):
    query: TextInput = TextInput(
        label="Search (name, tag, focus, vibe…)",
        placeholder="e.g., martyrs, hydra, siege, casual, elite",
        required=False,
        max_length=100,
    )

    def __init__(self, view: "ClanMatchView"):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        # Owner/admin check lives in view.run_search
        q = str(self.query.value or "").strip()
        await self.view_ref.run_search(interaction, q)


def build_panel_embed(user: discord.abc.User) -> discord.Embed:
    e = discord.Embed(
        title="C1C-Matchmaker Panel",
        description=(
            f"**{user.mention}** has summoned C1C-Matchmaker.\n"
            "🔔 _Only they (or an admin) can use this panel._\n\n"
            "Use **Search** to find clans. **Reset** removes the last result message. **Close** disables the panel.\n"
            "Panel will auto-expire after some time and show a **Reload new search** button."
        ),
        color=discord.Color.blurple(),
    )
    e.set_footer(text="C1C · Match smart, play together")
    return e


def can_control(inter: discord.Interaction, owner_id: int) -> bool:
    if inter.user.id == owner_id:
        return True
    perms = inter.user.guild_permissions if inter.guild else None
    if perms and perms.administrator:
        return True
    if inter.guild and inter.guild.owner_id == inter.user.id:
        return True
    return False


def chunk_fields(items: List[str], hard_cap: int = 25) -> List[List[str]]:
    """Split list into chunks of <= 25 (embed field limit)."""
    out = []
    for i in range(0, len(items), hard_cap):
        out.append(items[i:i + hard_cap])
    return out


def render_results_to_embeds(results: List[Dict], query: str) -> List[discord.Embed]:
    """
    Render all results into **one message** (possibly multiple embeds),
    but we will send them in a single send/edit call.
    """
    if not results:
        e = discord.Embed(
            title="No clans found",
            description="Try a broader term, or a different keyword.",
            color=discord.Color.red()
        )
        if query:
            e.add_field(name="Query", value=f"`{query}`", inline=False)
        return [e]

    lines = []
    for i, row in enumerate(results, start=1):
        name = _normalize(row.get("Name")) or "Unknown Clan"
        tag = _normalize(row.get("Tag"))
        focus = _normalize(row.get("Focus"))
        vibe = _normalize(row.get("Vibe"))
        head = f"**{i}. {name}**" + (f" — `{tag}`" if tag else "")
        bits = []
        if focus:
            bits.append(f"**Focus:** {focus}")
        if vibe:
            bits.append(f"**Vibe:** {vibe}")
        body = "\n".join(bits) if bits else "*No extra details provided.*"
        lines.append((head, body))

    # Build embeds with fields (<=25 per embed)
    embeds: List[discord.Embed] = []
    chunks = chunk_fields(lines, hard_cap=25)
    for idx, group in enumerate(chunks, start=1):
        title = "Clan Match Results"
        if len(chunks) > 1:
            title += f" (Page {idx}/{len(chunks)})"
        e = discord.Embed(
            title=title,
            description=f"Found **{len(results)}** match(es)" + (f" for `{query}`" if query else ""),
            color=discord.Color.green(),
        )
        for head, body in group:
            e.add_field(name=head, value=body, inline=False)
        e.set_footer(text="One message. Many results. • C1C Matchmaker")
        embeds.append(e)
    return embeds


class ReloadView(View):
    def __init__(self, owner_id: int):
        # no timeout; we want the button to stick until clicked
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    @button(label="Reload new search", style=discord.ButtonStyle.primary)
    async def reload(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can reload this panel.", ephemeral=True
            )

        # Replace with a fresh panel + fresh view
        new_view = ClanMatchView(owner_id=self.owner_id, timeout=PANEL_TIMEOUT_SECONDS)
        embed = build_panel_embed(inter.user if inter.user.id == self.owner_id else inter.client.user)

        # If we have a message captured, edit that; else reply
        try:
            if self.message:
                await self.message.edit(embed=embed, view=new_view)
                new_view.panel_message = self.message
                await inter.response.send_message("New panel loaded. Happy matching!", ephemeral=True)
            else:
                msg = await inter.channel.send(embed=embed, view=new_view)
                new_view.panel_message = msg
                await inter.response.send_message("New panel loaded.", ephemeral=True)
        except Exception as e:
            log.exception("Failed to reload panel: %s", e)
            await inter.response.send_message("Couldn't reload panel (missing perms?).", ephemeral=True)


class ClanMatchView(View):
    """
    Main interactive panel.
    - Posts ALL search results in ONE message (possibly multi-embed) and keeps a reference.
    - Reset deletes that single results message.
    - On timeout, disables self and swaps to a 'Reload new search' button.
    """
    def __init__(self, owner_id: int, timeout: Optional[float] = PANEL_TIMEOUT_SECONDS):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.panel_message: Optional[discord.Message] = None
        self.results_message: Optional[discord.Message] = None

    # ---------------- Buttons ----------------

    @button(label="Search", style=discord.ButtonStyle.blurple, emoji="🔎")
    async def btn_search(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can use this panel.", ephemeral=True
            )
        await inter.response.send_modal(ClanSearchModal(self))

    @button(label="Reset", style=discord.ButtonStyle.danger, emoji="🧹")
    async def btn_reset(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can reset this panel.", ephemeral=True
            )
        deleted = False
        if self.results_message:
            try:
                await self.results_message.delete()
                deleted = True
            except Exception:
                pass
            self.results_message = None
        msg = "Previous search results deleted." if deleted else "Nothing to delete."
        try:
            await inter.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await inter.followup.send(msg, ephemeral=True)

    @button(label="Close", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def btn_close(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can close this panel.", ephemeral=True
            )
        # Disable immediately & flip to reload button (same as on_timeout)
        await self._expire_to_reload(inter)

    # ---------------- Actions ----------------

    async def run_search(self, inter: discord.Interaction, query: str):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can search.", ephemeral=True
            )

        # Nuke prior results message to keep the channel clean
        if self.results_message:
            try:
                await self.results_message.delete()
            except Exception:
                pass
            self.results_message = None

        await inter.response.defer(thinking=True)
        results = fetch_clans(query)
        embeds = render_results_to_embeds(results, query)

        try:
            # One message send containing all embeds
            msg = await inter.channel.send(embeds=embeds)
            self.results_message = msg
            await inter.followup.send(
                f"Posted **{len(results)}** result(s) in a single message.", ephemeral=True
            )
        except Exception as e:
            log.exception("Failed to post search results: %s", e)
            await inter.followup.send("Couldn't post results (missing perms?).", ephemeral=True)

    # ---------------- Expiry Handling ----------------

    async def on_timeout(self):
        # View times out server-side; we still can edit the message if we held a reference
        try:
            if self.panel_message:
                for child in self.children:
                    child.disabled = True
                # Show disabled view immediately, and then swap to ReloadView
                await self.panel_message.edit(view=self)

                reload_view = ReloadView(owner_id=self.owner_id)
                reload_view.message = self.panel_message
                expired_embed = self.panel_message.embeds[0] if self.panel_message.embeds else build_panel_embed(self.panel_message.author)
                expired = discord.Embed(
                    title=expired_embed.title or "C1C-Matchmaker Panel",
                    description="⏳ This panel expired. Click **Reload new search** to start fresh.",
                    color=discord.Color.greyple()
                )
                await self.panel_message.edit(embed=expired, view=reload_view)
        except Exception as e:
            log.debug("on_timeout edit failed (message likely gone): %s", e)

    async def _expire_to_reload(self, inter: discord.Interaction):
        # Manual close mirrors timeout behavior
        try:
            for child in self.children:
                child.disabled = True
            await inter.response.edit_message(view=self)

            reload_view = ReloadView(owner_id=self.owner_id)
            reload_view.message = self.panel_message or inter.message
            expired = discord.Embed(
                title="C1C-Matchmaker Panel",
                description="Panel closed. Click **Reload new search** to start fresh.",
                color=discord.Color.greyple()
            )
            await (self.panel_message or inter.message).edit(embed=expired, view=reload_view)
        except Exception as e:
            log.exception("Failed to close panel: %s", e)
            try:
                await inter.followup.send("Couldn't close panel (missing perms?).", ephemeral=True)
            except Exception:
                pass


# ============================================================
#                        COMMANDS
# ============================================================

@bot.command(name="clanmatch", help="Summon the C1C-Matchmaker panel.")
async def clanmatch(ctx: commands.Context):
    # Post panel with ownership notice
    embed = build_panel_embed(ctx.author)
    view = ClanMatchView(owner_id=ctx.author.id, timeout=PANEL_TIMEOUT_SECONDS)
    msg = await ctx.send(embed=embed, view=view)
    view.panel_message = msg  # so on_timeout can edit it

    # Optional: teach users quickly
    try:
        await ctx.message.add_reaction("🔎")
    except Exception:
        pass


# ---------- Basic Up signal ----------
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

# ---------- Run ----------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in env.")
    bot.run(TOKEN)
