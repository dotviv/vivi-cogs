from __future__ import annotations

import asyncio
import datetime
import logging
import shutil
from math import ceil
from pathlib import Path
from typing import Dict, List, Optional

import discord
from discord import Colour, Member, User, Guild
from redbot.core import Config, modlog, commands
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_list

from ._common.interactions import Interactions, PageEmbedProvider
from ._common.log_channels import (
    CATEGORIES,
    missing_send_permissions,
    resolve_channel,
    set_category_channel,
    set_event_channel,
)
from ._common.modlog_render import build_case_embed

log = logging.getLogger("red.vivi-cogs.modlog")

#: Category assumed for a registration that didn't declare one -- most
#: registrants are case-worthy moderator actions, so this is the common case.
DEFAULT_CATEGORY = "modlog"


class UnknownActionType(Exception):
    """Raised when a case is requested for an action type nobody registered.

    Registration lives with the cog that owns the action, so this generally
    means that cog either failed to load or has not replayed its registrations
    since ModLog was last reloaded.
    """


class ModLog(commands.Cog):
    class ActionType:
        type: str
        name: str
        color: Colour
        emoji: str | None
        category: str

        def __init__(
            self, *, type: str, name: str, color: Colour, emoji: str | None, category: str = DEFAULT_CATEGORY
        ):
            self.type = type
            self.name = name
            self.color = color
            self.emoji = emoji
            self.category = category

    class Case:
        action_type: ModLog.ActionType
        case_number: int
        moderator: Member | User | int | None
        target: Member | User | int | None
        reason: str
        channel_id: int | None
        message_id: int | None
        timestamp: float
        duration: str | None
        attachments: List[Path]

        def __init__(
            self,
            *,
            action_type: ModLog.ActionType,
            case_number: int,
            moderator: Member | User | int | None,
            target: Member | User | int | None,
            reason: str,
            timestamp: float,
            duration: str | None,
            attachments: List[Path] | None = None,
            channel_id: int | None = None,
            message_id: int | None = None,
        ):
            self.action_type = action_type
            self.case_number = case_number
            self.moderator = moderator
            self.target = target
            self.reason = reason
            self.timestamp = timestamp
            self.duration = duration
            self.attachments = attachments or []

            # These stay None until the case is posted, and stay None forever if
            # the guild has no modlog channel. They must be assigned here rather
            # than left as bare annotations -- to_dict reads them unconditionally.
            self.channel_id = channel_id
            self.message_id = message_id

        @staticmethod
        def _identifier(who: Member | User | int | None) -> int | None:
            """Reduce a participant to a stored ID.

            Used for both fields, and None is legitimate for either: a target
            of None means a global or moderator-only action with no single
            member it happened to (e.g. warning a whole channel), and a
            moderator of None means an unattributed or automated one. Core
            modlog treats its moderator as optional the same way, though it
            has no way to represent a None target at all.
            """
            if who is None:
                return None
            return who if isinstance(who, int) else who.id

        def to_dict(self) -> dict:
            return {
                "action_type": self.action_type.type,
                "case_number": self.case_number,
                "reason": self.reason,
                "channel_id": self.channel_id,
                "message_id": self.message_id,
                "timestamp": self.timestamp,
                "duration": self.duration,
                "attachments": [str(p) for p in self.attachments],
                "moderator_id": self._identifier(self.moderator),
                "target_id": self._identifier(self.target),
            }

        @classmethod
        def from_dict(
            cls,
            bot: Red,
            guild: Guild,
            data: dict,
            action_types: Dict[str, ModLog.ActionType],
        ) -> ModLog.Case:
            """Rebuild a case from storage.

            ``action_types`` is passed in rather than read off the class, so a
            case can be rendered against whichever registry the caller holds.
            """
            mod_id = data["moderator_id"]
            target_id = data["target_id"]

            if mod_id is None:
                moderator = None
            else:
                moderator = guild.get_member(mod_id) or bot.get_user(mod_id) or mod_id

            if target_id is None:
                target = None
            else:
                target = guild.get_member(target_id) or bot.get_user(target_id) or target_id

            if data["action_type"] not in action_types:
                raise UnknownActionType(data["action_type"])

            return cls(
                action_type=action_types[data["action_type"]],
                case_number=data["case_number"],
                moderator=moderator,
                target=target,
                reason=data["reason"],
                timestamp=data.get("timestamp", 0.0),
                duration=data.get("duration"),
                attachments=[Path(s) for s in data.get("attachments", [])],
                channel_id=data.get("channel_id"),
                message_id=data.get("message_id"),
            )

    class CasePageEmbedProvider(PageEmbedProvider):
        def __init__(self, cog: ModLog, ctx: Context, guild: Guild, member: Member) -> None:
            self.cog = cog
            self.ctx = ctx
            self.guild = guild
            self.member = member
            self.page_len = 10
            self.member_cases: List[int] = []

        async def setup(self) -> None:
            user_cases = self.cog.config.guild(self.guild).user_cases
            self.member_cases = await user_cases.get_raw(str(self.member.id), default=[])

        async def provide(self, page: int) -> discord.Embed:
            end = len(self.member_cases) - (page - 1) * self.page_len
            start = max(0, end - self.page_len)
            case_ids = self.member_cases[start:end]

            cases = self.cog.config.guild(self.guild).cases
            content = ""

            for case_id in reversed(case_ids):
                raw = await cases.get_raw(str(case_id), default=None)
                if raw is None:
                    continue
                try:
                    case = self.cog.case_from_dict(self.guild, raw)
                except UnknownActionType:
                    # A case whose owning cog is currently unloaded still belongs
                    # in the member's history; skipping it would silently hide it.
                    content += f"`{raw['case_number']}` - `unknown action`\n"
                    continue
                emoji = case.action_type.emoji or ""
                content += (
                    f"`{case.case_number}` - {emoji}`{case.action_type.name}` "
                    f"[<t:{int(case.timestamp)}:F>]\n"
                )

            return discord.Embed(
                title=f"Case history for {self.member.name}",
                description=content,
                colour=await self.ctx.embed_color() or discord.Colour.blue(),
            )

        async def pages(self) -> int:
            cases = len(self.member_cases)
            return int(ceil(cases / self.page_len)) if cases > self.page_len else 1

    class ActionsPageEmbedProvider(PageEmbedProvider):
        """Cases a member took as moderator, rather than cases taken against them.

        A near-duplicate of `CasePageEmbedProvider` reading `moderator_cases`
        instead of `user_cases` -- kept separate rather than parameterized,
        since the two indexes and the two commands that read them are
        conceptually distinct (what happened to someone vs. what they did) and
        are likely to diverge further (e.g. a duration/severity summary makes
        sense for actions and not for cases).
        """

        def __init__(self, cog: ModLog, ctx: Context, guild: Guild, member: Member) -> None:
            self.cog = cog
            self.ctx = ctx
            self.guild = guild
            self.member = member
            self.page_len = 10
            self.moderator_cases: List[int] = []

        async def setup(self) -> None:
            moderator_cases = self.cog.config.guild(self.guild).moderator_cases
            self.moderator_cases = await moderator_cases.get_raw(str(self.member.id), default=[])

        async def provide(self, page: int) -> discord.Embed:
            end = len(self.moderator_cases) - (page - 1) * self.page_len
            start = max(0, end - self.page_len)
            case_ids = self.moderator_cases[start:end]

            cases = self.cog.config.guild(self.guild).cases
            content = ""

            for case_id in reversed(case_ids):
                raw = await cases.get_raw(str(case_id), default=None)
                if raw is None:
                    continue
                try:
                    case = self.cog.case_from_dict(self.guild, raw)
                except UnknownActionType:
                    content += f"`{raw['case_number']}` - `unknown action`\n"
                    continue
                emoji = case.action_type.emoji or ""
                content += (
                    f"`{case.case_number}` - {emoji}`{case.action_type.name}` "
                    f"[<t:{int(case.timestamp)}:F>]\n"
                )

            return discord.Embed(
                title=f"Actions taken by {self.member.name}",
                description=content,
                colour=await self.ctx.embed_color() or discord.Colour.blue(),
            )

        async def pages(self) -> int:
            cases = len(self.moderator_cases)
            return int(ceil(cases / self.page_len)) if cases > self.page_len else 1

    DEFAULT_GUILD = {
        "case_sequence": 1,
        "cases": {},
        "user_cases": {},
        "moderator_cases": {},
        "log_channels": {"categories": {category: None for category in CATEGORIES}, "events": {}},
    }

    DEFAULT_MEMBER: dict = {}

    CONFIG_IDENTIFIER = 1244378783399

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=ModLog.CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self._background_tasks: set[asyncio.Task] = set()

        # Instance state, not class state. Red reloads a cog by re-executing its
        # module in place, which rebinds the class object -- anything kept on the
        # class survives as a stale duplicate that the new instance cannot see.
        # Owning cogs re-register on load and on ModLog's own load; see the proxy.
        self._action_types: Dict[str, ModLog.ActionType] = {}

    ### ----------------------------------------------------------------
    ### Action type registration
    ### ----------------------------------------------------------------

    def register_action_type(
        self,
        *,
        type: str,
        name: str,
        color: Colour,
        emoji: str | None = None,
        category: str = DEFAULT_CATEGORY,
    ) -> None:
        """Register an action type this ModLog can create cases for.

        Takes primitives rather than an ``ActionType`` instance on purpose: each
        cog carries its own vendored copy of the shared helpers, so classes must
        never cross a cog boundary. Re-registering the same type overwrites it,
        which is what makes registration replay safe.

        An unrecognized ``category`` is coerced to the default rather than
        raising -- registration replay across a reload must stay best-effort.
        """
        if category not in CATEGORIES:
            log.warning("Unknown category %r for action type %s; using %r.", category, type, DEFAULT_CATEGORY)
            category = DEFAULT_CATEGORY

        self._action_types[type] = ModLog.ActionType(
            type=type, name=name, color=color, emoji=emoji, category=category
        )

    def action_type(self, action_type: str) -> ModLog.ActionType | None:
        """Look up a registered action type, or None if nobody registered it."""
        return self._action_types.get(action_type)

    def case_from_dict(self, guild: Guild, data: dict) -> ModLog.Case:
        """Rebuild a stored case against this instance's registry."""
        return ModLog.Case.from_dict(self.bot, guild, data, self._action_types)

    async def cases_since(self, guild: Guild, since_timestamp: float) -> List[ModLog.Case]:
        """Every case in ``guild`` created at or after ``since_timestamp``.

        Reads the whole ``cases`` group, same as `CasePageEmbedProvider` --
        acceptable here because this is a once-per-digest read, not something
        called on every action the way `create_case` is.
        """
        raw = await self.config.guild(guild).cases()
        cases = []

        for data in raw.values():
            if data.get("timestamp", 0.0) < since_timestamp:
                continue
            try:
                cases.append(self.case_from_dict(guild, data))
            except UnknownActionType:
                # Belongs to a cog that isn't loaded right now. A digest can't
                # name it, so it's skipped rather than shown as "unknown".
                continue

        return cases

    ### ----------------------------------------------------------------
    ### Rendering
    ### ----------------------------------------------------------------

    @classmethod
    def case_embed(cls, case: ModLog.Case, *, detailed: bool = False) -> discord.Embed:
        """Render a case through the shared builder.

        Consuming cogs render their moderator summaries with the same function
        from their own vendored copy, so a case looks the same in the channel
        and in the reply that follows the action.
        """
        return build_case_embed(
            action_name=case.action_type.name,
            action_color=case.action_type.color,
            action_emoji=case.action_type.emoji,
            case_number=case.case_number,
            moderator=case.moderator,
            target=case.target,
            reason=case.reason,
            timestamp=case.timestamp,
            duration=case.duration,
            detailed=detailed,
        )

    ### ----------------------------------------------------------------
    ### Utilities
    ### ----------------------------------------------------------------

    def _attachment_dir(self, case_number: int) -> Path:
        directory = cog_data_path(self) / f"cases/{case_number}/attachments"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def _archive_attachments(self, case: ModLog.Case, attachments: List[Path]) -> None:
        attachment_dir = self._attachment_dir(case_number=case.case_number)

        for file_path in attachments:
            src = Path(file_path)

            if src.is_file():
                shutil.copy2(src, attachment_dir)
            else:
                log.warning(
                    f"Attachment \"{file_path}\" could not be archived for case number "
                    f"{case.case_number}, the file could not be found."
                )

    async def _next_case_number(self, guild: Guild) -> int:
        """Reserve the next case number.

        Taken under the value's own lock: verification alone can fire several
        cases in the same moment during a raid, and two of them being handed the
        same number would overwrite one another in storage.
        """
        sequence = self.config.guild(guild).case_sequence

        async with sequence.get_lock():
            case_number = await sequence()
            await sequence.set(case_number + 1)

        return case_number

    ### ----------------------------------------------------------------
    ### Case creation
    ### ----------------------------------------------------------------

    async def create_case(
        self,
        guild: Guild,
        *,
        action_type: str,
        moderator: Member | User | int | None = None,
        target: Member | User | int | None = None,
        reason: str | None = None,
        duration: str | None = None,
        attachments: List[Path] | None = None,
    ) -> ModLog.Case:
        """Create, store, and post a modlog case.

        ``target`` may be omitted for a global or moderator-only action with
        no single member it happened to -- it is then simply not filed under
        anyone's `[p]cases` history, only under the moderator's `[p]actions`.
        """

        registered = self._action_types.get(action_type)

        if registered is None:
            raise UnknownActionType(action_type)

        target_id = ModLog.Case._identifier(target)
        resolved_target = target
        if isinstance(target, int):
            resolved_target = guild.get_member(target) or self.bot.get_user(target) or target

        case_number = await self._next_case_number(guild)

        case = ModLog.Case(
            action_type=registered,
            case_number=case_number,
            moderator=moderator,
            target=resolved_target,
            reason=reason
            or f"Responsible moderator, use `[p]reason {case_number}` to set the reason for this case.",
            timestamp=datetime.datetime.now(datetime.timezone.utc).timestamp(),
            duration=duration,
            attachments=attachments,
        )

        # Store before posting. A case is a record first and a message second --
        # a missing or unwritable modlog channel must not lose the record.
        #
        # Both writes are scoped to a single key. Reading the whole guild group
        # here would cost O(total cases) on every action, which verification
        # traffic turns into a real problem.
        await self.config.guild(guild).cases.set_raw(str(case_number), value=case.to_dict())

        if target_id is not None:
            user_cases = self.config.guild(guild).user_cases
            async with user_cases.get_lock():
                member_cases = await user_cases.get_raw(str(target_id), default=[])
                member_cases.append(case_number)
                await user_cases.set_raw(str(target_id), value=member_cases)

        moderator_id = ModLog.Case._identifier(moderator)
        if moderator_id is not None:
            moderator_cases = self.config.guild(guild).moderator_cases
            async with moderator_cases.get_lock():
                mod_cases = await moderator_cases.get_raw(str(moderator_id), default=[])
                mod_cases.append(case_number)
                await moderator_cases.set_raw(str(moderator_id), value=mod_cases)

        if case.attachments:
            await self._archive_attachments(case, case.attachments)

        await self._post_case(guild, case)

        return case

    async def _post_case(self, guild: Guild, case: ModLog.Case) -> None:
        """Post a stored case to a channel, if one resolves.

        Everything here is best-effort: the case is already recorded, and
        `[p]case` works whether or not this succeeds. Checks this cog's own
        per-event/category routing first, then falls back to Red core's single
        guild-wide modlog channel, matching pre-routing behaviour when nothing
        has been configured through `[p]modlogchannels`.
        """
        channel = await resolve_channel(
            guild,
            self.config.guild(guild).log_channels,
            action_type=case.action_type.type,
            category=case.action_type.category,
        )

        if channel is None:
            try:
                channel = await modlog.get_modlog_channel(guild)
            except (discord.HTTPException, RuntimeError):
                log.debug("No modlog channel is configured for guild %s.", guild.id)
                return

        if channel is None:
            return

        embed = self.case_embed(case)

        try:
            if case.attachments:
                files = [discord.File(str(p)) for p in case.attachments]
                message = await channel.send(embed=embed, files=files)
            else:
                message = await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to post case %s to the modlog.", case.case_number)
            return

        case.channel_id = message.channel.id
        case.message_id = message.id

        # Persist the message coordinates so [p]reason can edit the post later.
        # The original code set these on the object only, after it had already
        # been written, so the stored case never learned where it was posted.
        cases = self.config.guild(guild).cases
        await cases.set_raw(str(case.case_number), "channel_id", value=case.channel_id)
        await cases.set_raw(str(case.case_number), "message_id", value=case.message_id)

    ### ----------------------------------------------------------------
    ### Case management
    ### ----------------------------------------------------------------

    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    @commands.hybrid_command("reason", aliases=["r"])
    async def reason(
        self, ctx: commands.Context, case_number: int, *, reason: str | None = None
    ) -> None:
        """Updates the reason for a modlog case."""

        guild = ctx.guild

        if not guild:
            return

        if not reason:
            await ctx.send("You must provide a reason.", ephemeral=True)
            return

        cases = self.config.guild(guild).cases
        case_dict = await cases.get_raw(str(case_number), default=None)

        if not case_dict:
            await ctx.send(f"Case `{case_number}` could not be found.", ephemeral=True)
            return

        await cases.set_raw(str(case_number), "reason", value=reason)

        await ctx.send(f"Case `{case_number}` has been updated.")

        # The reason is stored at this point; the rest is just refreshing a
        # message that may have been deleted. Any failure here is tolerable,
        # and [p]case will still report the new reason either way.

        channel_id = case_dict.get("channel_id")
        message_id = case_dict.get("message_id")

        if not channel_id or not message_id:
            return

        channel = guild.get_channel(channel_id)

        if not channel:
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return

        if not message or not message.embeds:
            return

        embed = message.embeds[0]

        embed.set_field_at(len(embed.fields) - 1, name="Reason:", value=reason, inline=False)

        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            log.debug("Could not edit the posted message for case %s.", case_number)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    @commands.hybrid_command("case", aliases=["c"])
    async def case(self, ctx: commands.Context, case_number: int) -> None:
        """Returns details about a modlog case."""

        guild = ctx.guild

        if not guild:
            return

        raw = await self.config.guild(guild).cases.get_raw(str(case_number), default=None)

        if not raw:
            await ctx.send(f"Unable to locate modlog case `{case_number}`.", ephemeral=True)
            return

        try:
            case = self.case_from_dict(guild, raw)
        except UnknownActionType as error:
            await ctx.send(
                f"Case `{case_number}` has action type `{error.args[0]}`, which no loaded "
                f"cog has registered. Load the cog that owns it and try again.",
                ephemeral=True,
            )
            return

        embed = self.case_embed(case, detailed=True)

        try:
            if case.attachments:
                files = [discord.File(str(p)) for p in case.attachments]
                await ctx.send(embed=embed, files=files)
            else:
                await ctx.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to respond with case %s.", case_number)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    @commands.hybrid_command("cases")
    async def cases(self, ctx: commands.Context, target: Member) -> None:
        """Returns modlog cases for a specific user."""

        guild = ctx.guild

        if not guild:
            return

        user_cases = self.config.guild(guild).user_cases
        member_cases = await user_cases.get_raw(str(target.id), default=[])

        if not member_cases:
            await ctx.send(f"No modlog cases found for `{target.name}`.", ephemeral=True)
            return

        await Interactions.page(
            ctx=ctx,
            provider=ModLog.CasePageEmbedProvider(
                cog=self,
                ctx=ctx,
                guild=guild,
                member=target,
            ),
        )

    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    @commands.hybrid_command("actions")
    async def actions(self, ctx: commands.Context, moderator: Member) -> None:
        """Returns moderator actions taken by a specific user.

        A case is something that happened *to* someone (`[p]cases`); an action
        is something a moderator *did* -- this reads that second index, which
        every case with a moderator is filed under regardless of whether it
        also has a target.
        """

        guild = ctx.guild

        if not guild:
            return

        moderator_cases = self.config.guild(guild).moderator_cases
        mod_cases = await moderator_cases.get_raw(str(moderator.id), default=[])

        if not mod_cases:
            await ctx.send(f"No actions found for `{moderator.name}`.", ephemeral=True)
            return

        await Interactions.page(
            ctx=ctx,
            provider=ModLog.ActionsPageEmbedProvider(
                cog=self,
                ctx=ctx,
                guild=guild,
                member=moderator,
            ),
        )

    ### ----------------------------------------------------------------
    ### Channel configuration
    ### ----------------------------------------------------------------

    async def _set_log_channel(
        self, ctx: commands.Context, *, channel: Optional[discord.TextChannel], label: str, setter
    ) -> None:
        guild = ctx.guild

        if channel is None:
            await setter(None)
            await ctx.send(f"{label} channel cleared.")
            return

        missing = await missing_send_permissions(guild, channel)
        if missing:
            await ctx.send(f"I need {humanize_list(missing)} in {channel.mention} first.")
            return

        await setter(channel.id)
        await ctx.send(f"{label} channel set to {channel.mention}.")

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="modlogchannels")
    async def modlogchannels(self, ctx: commands.Context) -> None:
        """Configure which channels cases are posted to.

        This is separate from Red core's `[p]modlogset modlog`, which remains
        the last-resort fallback when nothing here resolves a channel.
        """

    @modlogchannels.command(name="category")
    async def modlogchannels_category(
        self, ctx: commands.Context, category: str, channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Set (or clear) the default channel for a category of case."""
        await self._modlogchannels_category(ctx, category, channel)

    async def _modlogchannels_category(
        self, ctx: commands.Context, category: str, channel: Optional[discord.TextChannel]
    ) -> None:
        category = category.lower()

        if category not in CATEGORIES:
            await ctx.send(f"Unknown category `{category}`. Choose one of: {humanize_list(list(CATEGORIES))}.")
            return

        guild = ctx.guild

        async def setter(channel_id: Optional[int]) -> None:
            await set_category_channel(self.config.guild(guild).log_channels, category, channel_id)

        await self._set_log_channel(ctx, channel=channel, label=f"`{category}`", setter=setter)

    @modlogchannels.command(name="event")
    async def modlogchannels_event(
        self, ctx: commands.Context, action_type: str, channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Set (or clear) the channel a specific action type is posted to."""
        await self._modlogchannels_event(ctx, action_type, channel)

    async def _modlogchannels_event(
        self, ctx: commands.Context, action_type: str, channel: Optional[discord.TextChannel]
    ) -> None:
        if action_type not in self._action_types:
            known = humanize_list(sorted(self._action_types)) if self._action_types else "*none registered yet*"
            await ctx.send(f"Unknown action type `{action_type}`. Currently registered: {known}.")
            return

        guild = ctx.guild

        async def setter(channel_id: Optional[int]) -> None:
            await set_event_channel(self.config.guild(guild).log_channels, action_type, channel_id)

        await self._set_log_channel(ctx, channel=channel, label=f"`{action_type}`", setter=setter)

    @modlogchannels.command(name="settings")
    async def modlogchannels_settings(self, ctx: commands.Context) -> None:
        """Show the current channel routing configuration."""
        await self._modlogchannels_settings(ctx)

    async def _modlogchannels_settings(self, ctx: commands.Context) -> None:
        conf = await self.config.guild(ctx.guild).log_channels()

        def render_channel(channel_id: Optional[int]) -> str:
            channel = ctx.guild.get_channel(channel_id) if channel_id else None
            return channel.mention if channel else "*not set*"

        embed = discord.Embed(title="Modlog channel routing", colour=await ctx.embed_colour())

        embed.add_field(
            name="Categories",
            value="\n".join(
                f"`{category}`: {render_channel(conf['categories'].get(category))}" for category in CATEGORIES
            ),
            inline=False,
        )

        events = conf.get("events", {})
        embed.add_field(
            name="Event overrides",
            value=(
                "\n".join(f"`{event}`: {render_channel(channel_id)}" for event, channel_id in sorted(events.items()))
                if events
                else "*none set*"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)
