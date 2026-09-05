import asyncio
import datetime
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict

import discord
from discord import CategoryChannel, Role, Member, User
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

from ._common.interactions import Interactions
from ._common.modlog_proxy import ModLogProxy
from ._common.modlog_render import reason_field
from ._common.roles import Roles

log = logging.getLogger("red.vivi-cogs.quarantine")

NO_TRANSCRIPT = (
    "The quarantine transcript could not be attached to the case: Red's core modlog "
    "cannot store attachments. Install vivi-cogs/ModLog to retain transcripts."
)

DENY_ACCESS = discord.PermissionOverwrite(view_channel=False)
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

    __author__ = "vivirancy"
    __version__ = "1.0.0"

    DEFAULT_GUILD = {
        "category_id": None,
        "role_id": None,
    }

    DEFAULT_MEMBER = {
        "channel_id": None,
        "removed_role_ids": []
    }

    # Declared as plain data rather than ModLog.ActionType instances. ModLog owns
    # that class, and importing it here would reintroduce the cross-cog import
    # this restructure exists to remove.
    ACTION_TYPES = (
        {"type": "quarantine", "name": "Quarantine", "color": discord.Color.yellow(), "emoji": "🔒"},
        {"type": "unquarantine", "name": "Unquarantine", "color": discord.Color.green(), "emoji": "🔓"},
    )

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=12312389892, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self._background_tasks: set[asyncio.Task] = set()
        self.modlog = ModLogProxy(self, action_types=self.ACTION_TYPES)

    async def cog_load(self) -> None:
        await self.modlog.refresh()

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog) -> None:
        await self.modlog.on_cog_add(cog)

    ### ----------------------------------------------------------------
    ### Utilities
    ### ----------------------------------------------------------------

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "member"

    ### ----------------------------------------------------------------
    ### Configuration / Settings
    ### ----------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="quarantineset")
    async def quarantineset(self, ctx: commands.Context) -> None:
        """Configure the Quarantine cog."""

    @quarantineset.command(name="category")
    async def quarantineset_category(
            self, ctx: commands.Context, category: Optional[CategoryChannel] = None
    ) -> None:
        """The category where private discussion channels will be created for quarantined members."""
        guild = ctx.guild

        if not guild:
            return

        if category and not category.permissions_for(guild.me).manage_channels:
            await ctx.send(
                content="I need the **Manage Channels** permission on that category in order to use it.",
                ephemeral=True)
            return

        if not category and not guild.me.guild_permissions.manage_channels:
            await ctx.send(
                content="I need the **Manage Channels** permission in order to create the category for you.",
                ephemeral=True)
            return

        method = "set"

        if not category:
            confirmation = await Interactions.confirm(
                ctx=ctx,
                title="Confirm category creation",
                message="Are you sure you want to create the quarantine discussion category for you?",
                message_cancelled="I will not create the quarantine discussion category.",
                message_confirmed="I will create the quarantine discussion category."
            )

            if not confirmation:
                return

            overwrites = {guild.default_role: DENY_ACCESS}

            for role in (await self.bot.get_admin_roles(guild) + (await self.bot.get_mod_roles(guild))):
                overwrites[role] = MOD_ACCESS

            try:
                category = await guild.create_category(
                    "Quarantined Discussion",
                    overwrites=overwrites,
                    reason=f"Quarantine setup by {ctx.author}",
                )
            except discord.HTTPException:
                log.exception("Failed to create quarantine discussion category.")
                return

            method = "created"

        await self.config.guild(guild).category_id.set(category.id)

        await ctx.send(f"Quarantine discussion category {method}. New discussions will be created under **{category.name}**.")

    @quarantineset.command(name="role")
    async def quarantineset_role(
            self, ctx: commands.Context, role: Optional[Role] = None
    ) -> None:
        """Sets the quarantine role assigned to members when they are quarantined."""
        guild = ctx.guild

        if not guild:
            return

        if not role and not guild.me.guild_permissions.manage_roles:
            await ctx.send(
                content="I need the **Manage Roles** permission in order to create the quarantined role for you.",
                ephemeral=True)
            return

        if role and not guild.me.guild_permissions.manage_roles:
            await ctx.send(
                content="I need the **Manage Roles** permission in order to manage member roles during quarantine.",
                ephemeral=True)
            return

        method = "set"

        if not role:
            confirmation = await Interactions.confirm(
                ctx=ctx,
                title="Confirm role creation",
                message="Are you sure you want to create the quarantine role for you?",
                message_cancelled="I will not create the quarantine role.",
                message_confirmed="I will create the quarantine role."
            )

            if not confirmation:
                return

            try:
                role = await guild.create_role(
                    name="Quarantined", permissions=discord.Permissions.none(),
                    reason=f"Quarantine setup by {ctx.author}",
                )
            except discord.HTTPException:
                log.exception("Failed to create quarantined role.")
                return

            method = "created"

        role_problem = Roles.assignable_role_problem(guild=guild, role=role)

        if role_problem:
            await ctx.send(content=role_problem, ephemeral=True)
            return

        await self.config.guild(guild).role_id.set(role.id)

        await ctx.send(f"The quarantine role has been {method}. Quarantined users will receive the **{role.name}** role.")

        confirmation = await Interactions.confirm(
            ctx=ctx,
            title="Apply channel permissions",
            message="Would you like me to configure channel permissions for the quarantined role?",
            message_confirmed="Channel permissions will be configured for the quarantined role.",
            message_cancelled="Channel permissions will not be configured, you will need to manage permissions for this role.")

        if not confirmation:
            return

        applied = 0
        failed = 0
        for channel in guild.channels:
            try:
                await channel.set_permissions(
                    role, overwrite=DENY_ACCESS, reason=f"Quarantine setup by {ctx.author}"
                )
                applied += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

        message = f"Permissions for the quarantine role have been applied to {applied} channels."
        if failed:
            message += f"I failed to apply the quarantine role permissions on {failed} channels. I likely lack **Manage Channel** permission on those channels. You will need to give me access and re-run the command, or manage permissions on your own."
        await ctx.send(message)

    @quarantineset.command(name="settings")
    async def quarantineset_settings(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        guild = ctx.guild

        if not guild:
            return

        conf = await self.config.guild(guild).all()

        role = ctx.guild.get_role(conf["role_id"]) if conf["role_id"] else None
        category = (ctx.guild.get_channel(conf["category_id"]) if conf["category_id"] else None)
        embed = discord.Embed(title="Quarantine settings", color=await ctx.embed_color())

        embed.add_field(name="Role", value=role.mention if role else "*not set*", inline=True)
        embed.add_field(name="Category", value=f"`{category.name}`" if category else "*not set*", inline=True)

        await ctx.send(embed=embed)

    ### ----------------------------------------------------------------
    ### Transcripts / archiving
    ### ----------------------------------------------------------------

    def _transcript_path(self, user_id: int, timestamp: int) -> Path:
        directory = cog_data_path(self) / "transcripts"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{user_id}-{timestamp}.json"

    async def _write_transcript(
        self,
        member_id: int,
        channel: discord.TextChannel,
    ) -> Path:
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

        timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        path = self._transcript_path(member_id, timestamp)

        if path.exists():
            log.warning("Unable to write transcript! Found existing transcript file: %s", path)

        path.write_text(json.dumps(messages, indent=2))

        return path

    async def _unquarantine_and_cleanup(self, guild: discord.Guild, target_state: Dict, target: Member | User | int, actor: Member | User, reason: str | None, audit_reason: str):
        """Shared by unquarantine and the on_member_remove/on_member_ban listeners."""

        guild_state = await self.config.guild(guild).all()

        removed_role_ids = target_state["removed_role_ids"]
        removed_roles = []
        quarantine_role = guild.get_role(guild_state["role_id"])
        channel_id = target_state["channel_id"]
        channel = guild.get_channel(channel_id)

        for removed_role_id in removed_role_ids:
            removed_role = guild.get_role(removed_role_id)
            if removed_role:
                removed_roles.append(removed_role)

        removed_roles = Roles.manageable_roles(guild, removed_roles)

        if isinstance(target, int):
            target_member_id = target
            target_member = guild.get_member(target)
        else:
            target_member_id = target.id
            target_member = target

        transcript_path = None

        if channel:
            transcript_path = await self._write_transcript(member_id=target_member_id, channel=channel)
            try:
                await channel.delete(reason=audit_reason)
            except discord.HTTPException:
                log.exception("Failed to delete quarantine discussion channel.")

        if isinstance(target_member, Member):
            if len(removed_roles) != len(removed_role_ids):
                log.warning(f"Not all roles could be restored for member {target_member.id}.")

            await target_member.add_roles(*removed_roles, reason=audit_reason)

            if quarantine_role:
                await target_member.remove_roles(quarantine_role, reason=audit_reason)

        async with self.config.member_from_ids(guild.id, target_member_id)() as member_data:
            member_data["removed_role_ids"] = []
            member_data["channel_id"] = None

        attachments = [transcript_path] if transcript_path else None

        return await self.modlog.create_case(
            guild,
            action_type="unquarantine",
            actor=actor,
            target=target_member or target,
            fields=reason_field(reason),
            attachments=attachments
        )

    ### ----------------------------------------------------------------
    ### Listeners for members leaving
    ### ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        state = await self.config.member(member).all()

        if not state["channel_id"]:
            return

        await self._unquarantine_and_cleanup(
            guild=member.guild,
            target=member,
            target_state=state,
            actor=member.guild.me,
            reason="Member left or kicked.",
            audit_reason=f"Unquarantine of {member.id} by guild departure.")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        state = await self.config.member_from_ids(guild.id, user.id).all()

        if not state["channel_id"]:
            return

        await self._unquarantine_and_cleanup(
            guild=guild,
            target=user,
            target_state=state,
            actor=guild.me,
            reason="Member was banned.",
            audit_reason=f"Unquarantine of {user.id} by ban.")

    ### ----------------------------------------------------------------
    ### Quarantine / release members.
    ### ----------------------------------------------------------------

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("quarantine", aliases=["q"])
    async def quarantine(
            self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None
    ) -> None:
        """Quarantine a member."""

        guild = ctx.guild

        if not guild:
            return

        if not member:
            await ctx.send("A valid member must be provided.", ephemeral=True)
            return

        previous_member_state = await self.config.member(member).all()
        guild_state = await self.config.guild(guild).all()

        if previous_member_state["channel_id"]:
            await ctx.send("Members is already quarantined.", ephemeral=True)
            return

        if not guild.me.guild_permissions.manage_roles:
            await ctx.send("I need the **Manage Roles** permission to quarantine members.", ephemeral=True)
            return

        if not guild.me.guild_permissions.manage_channels:
            await ctx.send("I need the **Manage Channels** permission to quarantine members.", ephemeral=True)
            return

        quarantine_role_id = guild_state["role_id"]
        quarantine_role = guild.get_role(quarantine_role_id) if quarantine_role_id else None

        discussion_category_id = guild_state["category_id"]
        quarantine_category = guild.get_channel(discussion_category_id) if discussion_category_id else None

        if not quarantine_role:
            await ctx.send("A quarantine role must be configured in order to quarantine members.", ephemeral=True)
            return

        if not quarantine_category:
            await ctx.send("A quarantine discussion category must be configured in order to quarantine members.", ephemeral=True)
            return

        if not quarantine_category.permissions_for(guild.me).manage_channels:
            await ctx.send(f"I need the **Manage Channels** permission on **{quarantine_category.name}** to quarantine members.", ephemeral=True)
            return

        confirmation = await self.modlog.confirm_action(
            ctx=ctx,
            action_type="quarantine",
            fields=reason_field(reason),
            target=member
        )

        if not confirmation:
            return

        removed_role_ids = []
        removed_roles = []

        for role in member.roles:
            # Skip all public roles, managed roles, and the quarantine role (to self-heal)
            if role.id != quarantine_role.id and not role.is_default() and not role.managed:
                removed_role_ids.append(role.id)
                removed_roles.append(role)

        removable_roles = Roles.manageable_roles(guild, removed_roles)

        if len(removable_roles) != len(removed_roles):
            await ctx.send(f"I don't have permission to remove all of the roles for that user. In order to quarantine a user, the bot needs to be positioned above all other roles in the guild.", ephemeral=True)
            return

        audit_reason=f"Quarantine of {member} by {ctx.author}."
        overwrites = {guild.default_role: DENY_ACCESS, member: QUARANTINED_ACCESS, guild.me: BOT_ACCESS}

        for mod_role in (await self.bot.get_admin_roles(guild)) + (
            await self.bot.get_mod_roles(guild)
        ):
            overwrites[mod_role] = MOD_ACCESS

        discussion_channel = await quarantine_category.create_text_channel(
            name=f"{self._slugify(member.display_name)}-discussion",
            overwrites=overwrites,
            reason=audit_reason,
        )

        # Save config before we go further, we need a reference in case things blow up.

        async with self.config.member(member).all() as member_data:
            member_data["channel_id"] = discussion_channel.id
            member_data["removed_role_ids"] = removed_role_ids

        embed = discord.Embed(
            title="You have been quarantined",
            description=f"A team member from {guild.name} will be with you shortly.",
            color=await ctx.embed_color(),
        )

        await discussion_channel.send(f"|| {member.mention} {ctx.author.mention} ||", embed=embed)
        await member.remove_roles(*removable_roles, reason=audit_reason)
        await member.add_roles(quarantine_role, reason=audit_reason)
        case = await self.modlog.create_case(
            guild,
            action_type="quarantine",
            actor=ctx.author,
            target=member,
            fields=reason_field(reason))

        await self.modlog.send_case_action_summary(
            ctx,
            case,
            note=f"{discussion_channel.mention} has been created.",
            ephemeral=False)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("unquarantine", aliases=["uq"])
    async def unquarantine(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None
    ) -> None:
        """Unquarantine a member."""

        guild = ctx.guild

        if not guild:
            return

        if not member:
            await ctx.send("A valid member must be provided.", ephemeral=True)
            return

        member_state = await self.config.member(member).all()
        discussion_channel_id = member_state["channel_id"]

        if not discussion_channel_id:
            await ctx.send("Member is not quarantined.", ephemeral=True)
            return

        confirmation = await self.modlog.confirm_action(
            ctx=ctx,
            action_type="unquarantine",
            fields=reason_field(reason),
            target=member
        )

        if not confirmation:
            return

        audit_reason = f"Unquarantine of {member} by {ctx.author}."

        case = await self._unquarantine_and_cleanup(guild=guild, target_state=member_state, target=member, actor=ctx.author, reason=reason, audit_reason=audit_reason)

        # The transcript is written to disk either way, but only ModLog can
        # attach it to the case. Say so rather than letting it vanish quietly.
        note = None if self.modlog.supports_attachments else NO_TRANSCRIPT

        await self.modlog.send_case_action_summary(ctx, case, note=note, ephemeral=False)










