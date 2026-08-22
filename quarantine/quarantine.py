"""Pull a troublesome member out of every channel and into a private room with mods."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import Role, Member, PermissionOverwrite
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.predicates import MessagePredicate

from quarantine.embeds import QuarantineEmbeds
from quarantine.views import QuarantineLiftedChannelPanel
from shared.mod_log import ModLog

log = logging.getLogger("red.vivi-cogs.quarantine")

LOG_COLOUR = discord.Colour.dark_red()
UNQUARANTINE_COLOUR = discord.Colour.green()

DENY_VIEW = discord.PermissionOverwrite(view_channel=False)

QUARANTINED_ACCESS = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    attach_files=True,
    read_message_history=True
)
MOD_ACCESS = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    attach_files=True,
    read_message_history=True,
    manage_messages=True,
    use_application_commands=True,
)
BOT_ACCESS = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    attach_files=True,
    embed_links=True,
    read_message_history=True,
    manage_messages=True,
    use_application_commands=True,
)


class Quarantine(commands.Cog):
    """Strip a member's roles and channel access, and give mods a private room to talk to them."""

    __author__ = "dotviv"
    __version__ = "1.0.0"

    DEFAULT_GUILD = {
        "quarantine_role_id": None,
        "category_id": None,
        "log_channel_id": None,
    }

    DEFAULT_MEMBER = {
        "quarantined": False,
        "channel_id": None,
        "previous_roles": [],
        "quarantined_by": None,
        "quarantined_at": None,
        "reason": None,
    }

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        # This identifier keys every guild's stored settings. Never change it.
        self.config = Config.get_conf(self, identifier=1928374650, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self._background_tasks: set[asyncio.Task] = set()

    def _fire_and_forget(self, coro) -> None:
        """Run ``coro`` without blocking the caller. Keeps a strong ref so it isn't GC'd mid-flight."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)

    def _background_task_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.exception("Background quarantine task failed", exc_info=task.exception())

    async def apply_bot_overwrite(self, ctx: commands.Context,
                                  overwrites: dict[Role | Member, PermissionOverwrite]) -> None:
        bot_member = ctx.guild.get_member(self.bot.user.id)

        if bot_member is None:
            try:
                bot_member = await ctx.guild.fetch_member(self.bot.user.id)
            except discord.HTTPException:
                log.exception("Failed to locate bot member.")

        assert bot_member is not None # Python is stupid as shit.

        overwrites[bot_member] = BOT_ACCESS

    def format_help_for_context(self, ctx: commands.Context) -> str:
        return f"{super().format_help_for_context(ctx)}\n\nAuthor: {self.__author__}\nVersion: {self.__version__}"

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """Drop any quarantine record and transcript belonging to ``user_id``."""
        for guild_id, members in (await self.config.all_members()).items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()
        path = self._transcript_path(user_id)
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Role safety (mirrors verification.py's hierarchy checks)
    # ------------------------------------------------------------------

    def _role_problem(self, guild: discord.Guild, role: discord.Role) -> Optional[str]:
        """Return why the bot cannot manage ``role``, or ``None`` if it can.

        Discord fails role assignment *silently* when the hierarchy is wrong, so
        this is checked at configuration time rather than at 3am during a raid.
        """
        me = guild.me
        if role.is_default():
            return "That's the `@everyone` role, which can't be assigned."
        if role.managed:
            return f"{role.mention} is managed by an integration and can't be assigned manually."
        if not me.guild_permissions.manage_roles:
            return "I don't have the **Manage Roles** permission in this server."
        if role >= me.top_role:
            return (
                f"{role.mention} is above (or equal to) my highest role, so I can't manage it. "
                "Move my role higher in **Server Settings → Roles**."
            )
        return None

    @staticmethod
    def _manageable_roles(guild: discord.Guild, roles: List[discord.Role]) -> List[discord.Role]:
        """Roles the bot can actually add/remove: not @everyone, not managed, below our top role."""
        me = guild.me
        return [
            role
            for role in roles
            if not role.is_default() and not role.managed and role < me.top_role
        ]

    async def _modlog(
        self,
        guild: discord.Guild,
        *,
        title: str,
        description: str,
        colour: discord.Colour = LOG_COLOUR,
    ) -> None:
        # Config key stays log_channel_id -- guilds already have this set, and renaming
        # the key would silently drop their existing configuration.
        channel_id = await self.config.guild(guild).log_channel_id()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        embed = discord.Embed(
            title=title, description=description, colour=colour, timestamp=discord.utils.utcnow()
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not write to the mod log channel in guild %s.", guild.id)

    # ------------------------------------------------------------------
    # Channel visibility
    # ------------------------------------------------------------------

    async def _deny_channel(self, channel: discord.abc.GuildChannel, role: discord.Role) -> bool:
        """Apply the quarantine deny-overwrite to a single channel. Returns success."""
        try:
            await channel.set_permissions(
                role, overwrite=DENY_VIEW, reason="Quarantine: hide channel from quarantined role"
            )
        except (discord.Forbidden, discord.HTTPException):
            return False
        return True

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        role_id = await self.config.guild(channel.guild).quarantine_role_id()
        if not role_id:
            return
        role = channel.guild.get_role(role_id)
        if role is None:
            return
        await self._deny_channel(channel, role)

    # ------------------------------------------------------------------
    # Transcript archiving
    # ------------------------------------------------------------------

    def _transcript_path(self, user_id: int):
        directory = cog_data_path(self) / "transcripts"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{user_id}.json"

    async def _write_transcript(
        self,
        member_id: int,
        channel: discord.TextChannel,
        *,
        session: Dict[str, Any],
        resolution: str,
    ) -> None:
        messages = []
        try:
            async for message in channel.history(limit=None, oldest_first=True):
                messages.append(
                    {
                        "author_id": message.author.id,
                        "author_name": str(message.author),
                        "timestamp": message.created_at.timestamp(),
                        "content": message.content,
                        "attachments": [a.url for a in message.attachments],
                    }
                )
        except (discord.Forbidden, discord.HTTPException) as error:
            log.warning("Could not read history for channel %s: %s", channel.id, error)

        session = dict(session)
        session["ended_at"] = datetime.now(timezone.utc).timestamp()
        session["resolution"] = resolution
        session["messages"] = messages

        path = self._transcript_path(member_id)
        existing: List[Dict[str, Any]] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                log.warning("Transcript file for %s was unreadable; starting fresh.", member_id)
        existing.append(session)
        path.write_text(json.dumps(existing, indent=2))

    async def _archive_and_cleanup(
        self, guild: discord.Guild, member_id: int, state: Dict[str, Any], resolution: str
    ) -> None:
        """Shared by unquarantine and the on_member_remove/on_member_ban listeners."""
        channel = guild.get_channel(state["channel_id"]) if state["channel_id"] else None
        session = {
            "guild_id": guild.id,
            "channel_id": state["channel_id"],
            "quarantined_by": state["quarantined_by"],
            "quarantined_at": state["quarantined_at"],
            "reason": state["reason"],
        }
        if channel is not None:
            await self._write_transcript(member_id, channel, session=session, resolution=resolution)
            try:
                await channel.delete(reason=f"Quarantine: {resolution}")
            except (discord.Forbidden, discord.HTTPException) as error:
                log.warning("Could not delete quarantine channel %s: %s", channel.id, error)
        await self.config.member_from_ids(guild.id, member_id).clear()

    # ------------------------------------------------------------------
    # Listeners: departure while quarantined
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        state = await self.config.member(member).all()
        if not state["quarantined"]:
            return
        await self._archive_and_cleanup(member.guild, member.id, state, "left_or_kicked")
        await self._modlog(
            member.guild,
            title="Quarantine ended: member left or was kicked",
            description=f"{member} ({member.id}) left the server while quarantined.",
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        state = await self.config.member_from_ids(guild.id, user.id).all()
        if not state["quarantined"]:
            return
        await self._archive_and_cleanup(guild, user.id, state, "banned")
        await self._modlog(
            guild,
            title="Quarantine ended: member banned",
            description=f"{user} ({user.id}) was banned while quarantined.",
        )

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="quarantineset")
    async def quarantineset(self, ctx: commands.Context) -> None:
        """Configure the Quarantine cog."""

    @quarantineset.command(name="role")
    async def quarantineset_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ) -> None:
        """Set (or auto-create) the Quarantined role, and hide every channel from it."""
        if role is None:
            if not ctx.guild.me.guild_permissions.manage_roles:
                await ctx.send("I need the **Manage Roles** permission to create one.")
                return
            try:
                role = await ctx.guild.create_role(
                    name="Quarantined", permissions=discord.Permissions.none(),
                    reason=f"Quarantine setup by {ctx.author}",
                )
            except discord.Forbidden:
                await ctx.send("I don't have permission to create roles here.")
                return
        else:
            problem = self._role_problem(ctx.guild, role)
            if problem:
                await ctx.send(problem)
                return

        channels = ctx.guild.channels
        await ctx.send(
            f"This will deny **View Channel** for {role.mention} on all "
            f"{len(channels)} channels and categories in this server. Reply `yes` to confirm."
        )
        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send("Timed out — nothing was changed.")
            return
        if not pred.result:
            await ctx.send("Cancelled.")
            return

        await self.config.guild(ctx.guild).quarantine_role_id.set(role.id)
        applied = 0
        failed = 0
        for channel in channels:
            if await self._deny_channel(channel, role):
                applied += 1
            else:
                failed += 1
        message = f"Quarantine role set to {role.mention}. Hid {applied} channel(s)."
        if failed:
            message += f" Failed on {failed} — I likely lack **Manage Channels** there."
        await ctx.send(message)

    @quarantineset.command(name="category")
    async def quarantineset_category(
        self, ctx: commands.Context, category: Optional[discord.CategoryChannel] = None
    ) -> None:
        """Set (or auto-create) the category quarantine discussion channels live in."""
        if not ctx.guild.me.guild_permissions.manage_channels:
            await ctx.send("I need the **Manage Channels** permission for that.")
            return

        overwrites = {ctx.guild.default_role: DENY_VIEW}

        for role in (await self.bot.get_admin_roles(ctx.guild)) + (
            await self.bot.get_mod_roles(ctx.guild)
        ):
            overwrites[role] = MOD_ACCESS

        await self.apply_bot_overwrite(ctx, overwrites)

        try:
            if category is None:
                category = await ctx.guild.create_category(
                    "Quarantined Discussion",
                    overwrites=overwrites,
                    reason=f"Quarantine setup by {ctx.author}",
                )
            else:
                for target, overwrite in overwrites.items():
                    await category.set_permissions(target, overwrite=overwrite)
        except discord.Forbidden:
            await ctx.send("I don't have permission to manage that category.")
            return

        await self.config.guild(ctx.guild).category_id.set(category.id)
        await ctx.send(f"Quarantine discussion channels will be created under **{category.name}**.")

    @quarantineset.command(name="settings")
    async def quarantineset_settings(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        conf = await self.config.guild(ctx.guild).all()

        role = ctx.guild.get_role(conf["quarantine_role_id"]) if conf["quarantine_role_id"] else None
        category = (
            ctx.guild.get_channel(conf["category_id"]) if conf["category_id"] else None
        )
        modlog_channel = (
            ctx.guild.get_channel(conf["log_channel_id"]) if conf["log_channel_id"] else None
        )

        await ctx.send(embed=QuarantineEmbeds.settings_quarantine_settings(
            color=await ctx.embed_colour(),
            role=role,
            category=category,
        ))

    # ------------------------------------------------------------------
    # Moderator commands
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "member"

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command(name="quarantine")
    async def quarantine(
        self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None
    ) -> None:
        """Strip a member of every role and channel, and open a private room with mods."""
        conf = await self.config.guild(ctx.guild).all()
        role = ctx.guild.get_role(conf["quarantine_role_id"]) if conf["quarantine_role_id"] else None
        category = ctx.guild.get_channel(conf["category_id"]) if conf["category_id"] else None

        if role is None or category is None:
            await ctx.send(
                f"Set up the quarantine role and category first with "
                f"`{ctx.clean_prefix}quarantineset role` and `{ctx.clean_prefix}quarantineset category`.",
                ephemeral=True,
            )
            return

        state = await self.config.member(member).all()

        if state["quarantined"]:
            existing = ctx.guild.get_channel(state["channel_id"]) if state["channel_id"] else None
            await ctx.send(
                f"{member.mention} is already quarantined"
                + (f" — see {existing.mention}." if existing else "."),
                ephemeral=True,
            )
            return

        problem = self._role_problem(ctx.guild, role)

        if problem:
            await ctx.send(problem, ephemeral=True)
            return

        to_strip = self._manageable_roles(ctx.guild, member.roles)
        final_roles = [r for r in member.roles if r not in to_strip]

        if role not in final_roles:
            final_roles.append(role)
        try:
            await member.edit(roles=final_roles, reason=f"Quarantine: {reason or 'no reason given'}")
        except (discord.Forbidden, discord.HTTPException) as error:
            await ctx.send(f"Couldn't update {member.mention}'s roles: {error}", ephemeral=True)
            return

        overwrites = {ctx.guild.default_role: DENY_VIEW, member: QUARANTINED_ACCESS}

        for mod_role in (await self.bot.get_admin_roles(ctx.guild)) + (
            await self.bot.get_mod_roles(ctx.guild)
        ):
            overwrites[mod_role] = MOD_ACCESS

        await self.apply_bot_overwrite(ctx, overwrites)

        try:
            channel = await category.create_text_channel(
                f"{self._slugify(member.display_name)}-discussion",
                overwrites=overwrites,
                reason=f"Quarantine of {member} by {ctx.author}.",
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            await ctx.send(
                f"Roles were updated, but I couldn't create the discussion channel: {error}",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc).timestamp()

        await self.config.member(member).set(
            {
                "quarantined": True,
                "channel_id": channel.id,
                "previous_roles": [r.id for r in to_strip],
                "quarantined_by": ctx.author.id,
                "quarantined_at": now,
                "reason": reason,
            }
        )

        await asyncio.gather(
            channel.send(content=f"|| {member.mention} {ctx.author.mention} ||", embed=QuarantineEmbeds.discussion_channel_member_quarantined(moderator=ctx.author, reason=reason)),
            ctx.send(f"{member.mention} has been quarantined. See {channel.mention}.", ephemeral=True)
        )

        self._fire_and_forget(
            self._modlog(
                ctx.guild,
                title="Member quarantined",
                description=f"{member} ({member.id}) quarantined by {ctx.author}.\n{reason or ''}",
            )
        )

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command(name="unquarantine")
    async def unquarantine(self, ctx: commands.Context, member: discord.Member) -> None:
        """Restore a quarantined member's roles and archive their discussion channel."""
        state = await self.config.member(member).all()
        if not state["quarantined"]:
            await ctx.send(f"{member.mention} isn't quarantined.", ephemeral=True)
            return

        role_id = await self.config.guild(ctx.guild).quarantine_role_id()
        role = ctx.guild.get_role(role_id) if role_id else None

        restored, skipped = [], []
        for prev_role_id in state["previous_roles"]:
            candidate = ctx.guild.get_role(prev_role_id)
            if candidate is None:
                skipped.append(f"<@&{prev_role_id}> (deleted)")
                continue
            if self._role_problem(ctx.guild, candidate):
                skipped.append(candidate.mention)
                continue
            restored.append(candidate)

        final_roles = [r for r in member.roles if role is None or r != role]
        for candidate in restored:
            if candidate not in final_roles:
                final_roles.append(candidate)
        try:
            await member.edit(roles=final_roles, reason=f"Unquarantine by {ctx.author}")
        except (discord.Forbidden, discord.HTTPException) as error:
            await ctx.send(f"Couldn't fully restore {member.mention}'s roles: {error}", ephemeral=True)

        message = f"{member.mention} has been unquarantined."
        if skipped:
            message += f"\n\nCouldn't restore: {humanize_list(skipped)}."

        await ctx.send(message, ephemeral=True)

        channel = ctx.guild.get_channel(state["channel_id"]) if state["channel_id"] else None

        try:
            await channel.send(embed=QuarantineEmbeds.discussion_channel_quarantine_lifted(member=member), view=QuarantineLiftedChannelPanel(self))
        except discord.HTTPException:
            log.exception("Failed to send quarantine lifted embed to quarantine discussion channel.")

    async def handle_panel_click_delete_channel(self, interaction: discord.Interaction) -> None:
        """If a moderator or an admin clicked the delete channel button after unquarantine, nuke the channel from orbit."""
        member = interaction.user
        guild = interaction.guild
        channel_id = interaction.channel_id

        if guild is None or not isinstance(member, discord.Member) or channel_id is None:
            return

        is_mod = False

        for mod_role in (await self.bot.get_admin_roles(guild)) + (
                await self.bot.get_mod_roles(guild)
        ):
            if member.get_role(mod_role.id) is not None:
                is_mod = True
                break

        if not is_mod:
            log.warning(f"Non moderator member {member.id} attempted to delete quarantine discussion channel {channel_id}.")
            return

        channel = interaction.channel

        if channel is None:
            return

        await channel.send(embed=QuarantineEmbeds.discussion_channel_deletion_pending())

        try:
            await channel.delete(f"Moderator {member.id} initiated deletion of quarantine discussion channel.")
        except discord.HTTPException:
            log.exception("Failed to delete quarantine discussion channel.")

        await ModLog.send_embed(
            guild=guild,
            embed=QuarantineEmbeds.modlog_discussion_channel_deleted(
                channel=channel,
                moderator=interaction.user)
        )