# bot_clanmatch_prefix.py
# C1C-Matchmaker — hybrid fixed/per-recruiter thread routing, mobile-friendly pointer,
# single-message results, wipe-on-expire/reset/close, reload-on-expire, and Render web binding.
# Requires: discord.py v2.x, aiohttp

import os
import json
import asyncio
import logging
from typing import List, Dict, Optional, Sequence

import discord
from discord.ext import commands
from discord.ui import View, button, Button, Modal, TextInput
from aiohttp import web

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("matchmaker")

# ---------- ENV ----------
TOKEN = os.environ.get("DISCORD_TOKEN")  # required
PANEL_TIMEOUT_SECONDS = int(os.environ.get("PANEL_TIMEOUT_SECONDS", "600"))  # default 10 min

# Thread routing (DEFAULT: hybrid)
PANEL_THREAD_MODE = os.environ.get("PANEL_THREAD_MODE", "hybrid").lower()  # fixed | per_recruiter | hybrid
PANEL_FIXED_THREAD_ID = int(os.environ.get("PANEL_FIXED_THREAD_ID", "0"))  # required for fixed/hybrid
PANEL_PARENT_CHANNEL_ID = int(os.environ.get("PANEL_PARENT_CHANNEL_ID", "0"))  # needed for per_recruiter/hybrid
PANEL_THREAD_ARCHIVE_MIN = int(os.environ.get("PANEL_THREAD_ARCHIVE_MIN", "1440"))  # 60, 1440, 4320, 10080
PANEL_PER_RECRUITER_ROLES = os.environ.get("PANEL_PER_RECRUITER_ROLES", "").strip()
PANEL_BOUNCE_DELETE_SECONDS = int(os.environ.get("PANEL_BOUNCE_DELETE_SECONDS", "600"))  # pointer + command lifetime

# Google Sheets (optional; demo data fallback if not set)
GSPREAD_CREDENTIALS = os.environ.get("GSPREAD_CREDENTIALS")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
CLANS_WORKSHEET_NAME = os.environ.get("CLANS_WORKSHEET_NAME", "Clans")
USE_GSHEETS = bool(GSPREAD_CREDENTIALS and GOOGLE_SHEET_ID)

# Render web server port
WEB_PORT = int(os.environ.get("PORT", "10000"))

# ---------- Discord Setup ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================================
#                 DATA ACCESS (SEARCH SOURCE)
# ============================================================

def _fallback_demo_data() -> List[Dict]:
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
    query = _normalize(query)
    if not query:
        query = ""

    if not USE_GSHEETS:
        rows = _fallback_demo_data()
        return rows if not query else [r for r in rows if _match_row(r, query)]

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
        return all_values if not query else [r for r in all_values if _match_row(r, query)]
    except Exception as e:
        log.exception("Sheets read failed; using demo data: %s", e)
        rows = _fallback_demo_data()
        return rows if not query else [r for r in rows if _match_row(r, query)]

# ============================================================
#               THREAD RESOLUTION (FIXED/HYBRID/PR)
# ============================================================

def _parse_role_ids(csv: str) -> Sequence[int]:
    out = []
    for part in csv.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out

PER_RECRUITER_ROLE_IDS = tuple(_parse_role_ids(PANEL_PER_RECRUITER_ROLES))

async def _unarchive_if_needed(thread: discord.Thread) -> discord.Thread:
    try:
        if thread.archived:
            await thread.edit(archived=False, auto_archive_duration=PANEL_THREAD_ARCHIVE_MIN)
    except Exception as e:
        log.warning("Could not unarchive thread %s: %s", getattr(thread, "id", "?"), e)
    return thread

async def _find_archived_thread_by_name(channel: discord.TextChannel, name: str) -> Optional[discord.Thread]:
    try:
        async for t in channel.archived_threads(limit=200, private=False):
            if t.name == name:
                return t
    except Exception as e:
        log.debug("archived_threads fetch failed: %s", e)
    return None

async def _get_fixed_thread(_: commands.Context) -> Optional[discord.Thread]:
    if not PANEL_FIXED_THREAD_ID:
        return None
    ch = bot.get_channel(PANEL_FIXED_THREAD_ID)
    if isinstance(ch, discord.Thread):
        return await _unarchive_if_needed(ch)
    try:
        fetched = await bot.fetch_channel(PANEL_FIXED_THREAD_ID)
        if isinstance(fetched, discord.Thread):
            return await _unarchive_if_needed(fetched)
    except Exception as e:
        log.warning("Fixed thread fetch failed (%s): %s", PANEL_FIXED_THREAD_ID, e)
    return None

async def _get_or_create_recruiter_thread(ctx: commands.Context) -> Optional[discord.Thread]:
    if not PANEL_PARENT_CHANNEL_ID:
        return None
    parent = bot.get_channel(PANEL_PARENT_CHANNEL_ID)
    if not isinstance(parent, discord.TextChannel):
        try:
            parent = await bot.fetch_channel(PANEL_PARENT_CHANNEL_ID)
        except Exception as e:
            log.warning("Parent channel fetch failed: %s", e)
            return None

    # Add user ID for uniqueness + stability
    name = f"Matchmaker — {ctx.author.display_name} · {ctx.author.id}"

    # 1) active list
    for t in parent.threads:
        if t.name == name:
            return await _unarchive_if_needed(t)

    # 2) archived
    t = await _find_archived_thread_by_name(parent, name)
    if t:
        return await _unarchive_if_needed(t)

    # 3) create new
    try:
        new_t = await parent.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=PANEL_THREAD_ARCHIVE_MIN
        )
        return new_t
    except Exception as e:
        log.warning("Recruiter thread create failed in %s: %s", parent.id, e)
        return None

def _user_is_recruiter(ctx: commands.Context) -> bool:
    if not PER_RECRUITER_ROLE_IDS or not isinstance(ctx.author, discord.Member):
        return False
    user_roles = {r.id for r in ctx.author.roles}
    return any(rid in user_roles for rid in PER_RECRUITER_ROLE_IDS)

async def _resolve_target_thread(ctx: commands.Context) -> Optional[discord.Thread]:
    mode = PANEL_THREAD_MODE
    if mode == "fixed":
        return await _get_fixed_thread(ctx)
    if mode == "per_recruiter":
        return await _get_or_create_recruiter_thread(ctx)
    if mode == "hybrid":
        if _user_is_recruiter(ctx):
            t = await _get_or_create_recruiter_thread(ctx)
            if t:
                return t
        return await _get_fixed_thread(ctx)
    return await _get_fixed_thread(ctx)

async def _post_pointer_and_schedule_cleanup(ctx: commands.Context, thread: discord.Thread):
    """Reply in invoking channel with a pointer to the thread, then auto-delete it and the command."""
    try:
        bounce = await ctx.reply(
            f"📎 Summoned matchmaking panel here → {thread.mention}\n"
            f"_This notice (and your command) will self-delete in {PANEL_BOUNCE_DELETE_SECONDS//60} min._",
            mention_author=True
        )
    except Exception:
        bounce = None

    async def _del_later(msg: Optional[discord.Message]):
        if not msg:
            return
        try:
            await asyncio.sleep(PANEL_BOUNCE_DELETE_SECONDS)
            await msg.delete()
        except Exception:
            pass

    # schedule deletions
    asyncio.create_task(_del_later(bounce))
    asyncio.create_task(_del_later(ctx.message))

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
        q = str(self.query.value or "").strip()
        await self.view_ref.run_search(interaction, q)

def build_panel_embed(user: discord.abc.User) -> discord.Embed:
    e = discord.Embed(
        title="C1C-Matchmaker Panel",
        description=(
            f"**{user.mention}** has summoned C1C-Matchmaker.\n"
            "🔔 _Only they (or an admin) can use this panel._\n\n"
            "Use **Search** to find clans. **Reset** removes the last result message. **Close** disables the panel.\n"
            f"Panel will auto-expire after {PANEL_TIMEOUT_SECONDS//60} minute(s) and show a **Reload new search** button."
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
    out = []
    for i in range(0, len(items), hard_cap):
        out.append(items[i:i + hard_cap])
    return out

def render_results_to_embeds(results: List[Dict], query: str) -> List[discord.Embed]:
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
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    @button(label="Reload new search", style=discord.ButtonStyle.primary)
    async def reload(self, inter: discord.Interaction, btn: Button):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can reload this panel.", ephemeral=True
            )
        new_view = ClanMatchView(owner_id=self.owner_id, timeout=PANEL_TIMEOUT_SECONDS)
        embed = build_panel_embed(inter.user if inter.user.id == self.owner_id else inter.client.user)
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
    - Posts ALL search results in ONE message
    - Reset deletes that results message
    - On timeout/close: wipes results, disables panel, shows Reload button
    """
    def __init__(self, owner_id: int, timeout: Optional[float] = PANEL_TIMEOUT_SECONDS):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.panel_message: Optional[discord.Message] = None
        self.results_message: Optional[discord.Message] = None

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
        await self._expire_to_reload(inter)

    async def run_search(self, inter: discord.Interaction, query: str):
        if not can_control(inter, self.owner_id):
            return await inter.response.send_message(
                "Only the original summoner or an admin can search.", ephemeral=True
            )

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
            msg = await inter.channel.send(embeds=embeds)
            self.results_message = msg
            await inter.followup.send(
                f"Posted **{len(results)}** result(s) in a single message.", ephemeral=True
            )
        except Exception as e:
            log.exception("Failed to post results: %s", e)
            await inter.followup.send("Couldn't post results (missing perms?).", ephemeral=True)

    # ----- Expiry Handling -----

    async def on_timeout(self):
        await self._wipe_results()
        try:
            if self.panel_message:
                for child in self.children:
                    child.disabled = True
                await self.panel_message.edit(view=self)

                reload_view = ReloadView(owner_id=self.owner_id)
                reload_view.message = self.panel_message
                expired = discord.Embed(
                    title="C1C-Matchmaker Panel",
                    description=(
                        "⏳ This panel expired.\n"
                        "🧹 Previous search results were **cleared**.\n\n"
                        "Click **Reload new search** to start fresh."
                    ),
                    color=discord.Color.greyple()
                )
                await self.panel_message.edit(embed=expired, view=reload_view)
        except Exception as e:
            log.debug("on_timeout edit failed: %s", e)

    async def _expire_to_reload(self, inter: discord.Interaction):
        await self._wipe_results()
        try:
            for child in self.children:
                child.disabled = True
            await inter.response.edit_message(view=self)

            reload_view = ReloadView(owner_id=self.owner_id)
            reload_view.message = self.panel_message or inter.message
            expired = discord.Embed(
                title="C1C-Matchmaker Panel",
                description=(
                    "Panel closed by a controller.\n"
                    "🧹 Previous search results were **cleared**.\n\n"
                    "Click **Reload new search** to start fresh."
                ),
                color=discord.Color.greyple()
            )
            await (self.panel_message or inter.message).edit(embed=expired, view=reload_view)
        except Exception as e:
            log.exception("Failed to close panel: %s", e)
            try:
                await inter.followup.send("Couldn't close panel (missing perms?).", ephemeral=True)
            except Exception:
                pass

    async def _wipe_results(self):
        if self.results_message:
            try:
                await self.results_message.delete()
            except Exception:
                pass
            self.results_message = None

# ============================================================
#                        COMMANDS
# ============================================================

@bot.command(name="clanmatch", help="Summon the C1C-Matchmaker panel.")
async def clanmatch(ctx: commands.Context):
    # Resolve target thread
    target_thread = await _resolve_target_thread(ctx)
    if not target_thread:
        await ctx.reply(
            "Couldn't resolve a target thread. Check env config and bot permissions.",
            mention_author=True
        )
        return

    # Panel in the resolved thread
    embed = build_panel_embed(ctx.author)
    view = ClanMatchView(owner_id=ctx.author.id, timeout=PANEL_TIMEOUT_SECONDS)
    msg = await target_thread.send(embed=embed, view=view)
    view.panel_message = msg

    # Pointer in invoking channel; auto-delete pointer & the command
    await _post_pointer_and_schedule_cleanup(ctx, target_thread)

    try:
        await ctx.message.add_reaction("🔎")
    except Exception:
        pass

# ---------- Lifecycle ----------
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

# ---------- Render Web Server (keeps Web Service happy) ----------
async def _health(_):
    return web.Response(text="ok")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/healthz", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info("HTTP server listening on 0.0.0.0:%s (Render health)", WEB_PORT)

# ---------- Main ----------
async def main():
    await start_webserver()  # start the tiny HTTP server first
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in env.")
    asyncio.run(main())
