from __future__ import annotations

import asyncio
import datetime
import logging
import shutil
from math import ceil
from pathlib import Path
from sqlite3 import NotSupportedError

import discord
from discord import Colour, Member, User, Guild
from redbot.core import Config, modlog, commands
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.data_manager import cog_data_path
from typing_extensions import List

from common.interactions import ConfirmationView, Interactions, PageEmbedProvider

log = logging.getLogger("red.vivi-cogs.scarlettmod.modlog")

class ModLog(commands.Cog):
    class ActionType:
        type: str
        name: str
        color: Colour
        emoji: str | None

        def __init__(self, *, type: str, name: str, color: Colour, emoji: str | None):
            self.type = type
            self.name = name
            self.color = color
            self.emoji = emoji

    class Case:
        action_type: ModLog.ActionType
        case_number: int
        moderator: Member | User | int
        target: Member | User | int
        reason: str
        channel_id: int | None
        message_id: int | None
        timestamp: float
        duration: str | None
        attachments: List[Path]

        def __init__(self, *, action_type: ModLog.ActionType, case_number: int, moderator: Member | User, target: Member | User | int, reason: str, timestamp: float, duration: str | None, attachments: List[Path] | None = None):
            self.action_type = action_type
            self.case_number = case_number
            self.moderator = moderator
            self.target = target
            self.reason = reason
            self.timestamp = timestamp
            self.duration = duration
            self.attachments = attachments or []

        def to_dict(self) -> dict:
            d = {
                "action_type": self.action_type.type,
                "case_number": self.case_number,
                "reason": self.reason,
                "channel_id": self.channel_id,
                "message_id": self.message_id,
                "timestamp": self.timestamp,
                "duration": self.duration,
                "attachments": [str(p) for p in self.attachments],
            }

            if isinstance(self.moderator, int):
                d["moderator_id"] = self.moderator
            else:
                d["moderator_id"] = self.moderator.id

            if isinstance(self.target, int):
                d["target_id"] = self.target
            else:
                d["target_id"] = self.target.id

            return d

        @classmethod
        def from_dict(cls, bot: discord.Client, guild: discord.Guild, data: dict) -> ModLog.Case:
            mod_id = data["moderator_id"]
            target_id = data["target_id"]

            moderator = guild.get_member(mod_id) or bot.get_user(mod_id) or mod_id
            target = guild.get_member(target_id) or bot.get_user(target_id) or target_id

            if data["action_type"] in ModLog.ACTION_TYPES:
                action_type = ModLog.ACTION_TYPES[data["action_type"]]
            else:
                raise commands.BadArgument("Unknown action type.")

            case = cls(
                action_type=action_type,
                case_number=data["case_number"],
                moderator=moderator,
                target=target,
                reason=data["reason"],
                timestamp=0.0,
                duration=None,
                attachments=[Path(s) for s in data["attachments"]] if "attachments" in data else [],
            )

            case.channel_id = data["channel_id"]
            case.message_id = data["message_id"]

            if "timestamp" in data:
                case.timestamp = data["timestamp"]

            if "duration" in data:
                case.duration = data["duration"]

            return case

    class CasePageEmbedProvider(PageEmbedProvider):

        def __init__(self, bot: Red, guild: Guild, config: Config, ctx: Context, member: Member) -> None:
            self.bot = bot
            self.guild = guild
            self.config = config
            self.ctx = ctx
            self.member = member
            self.page_len = 10
            self.member_cases = []

        async def setup(self) -> None:
            user_case_data = await self.config.guild(self.guild).user_cases()
            member_id_key = str(self.member.id)

            if member_id_key in user_case_data:
                self.member_cases = user_case_data[member_id_key]

            pass

        async def provide(self, page: int) -> discord.Embed:
            case_data = {}
            end = len(self.member_cases) - (page - 1) * self.page_len
            start = max(0, end - self.page_len)
            case_ids = self.member_cases[start:end]

            for case_id in case_ids:
                raw = await self.config.guild(self.guild).cases.get_raw(str(case_id), default=None)
                if raw is not None:
                    case_data[str(case_id)] = raw

            content = ""

            for case_id in reversed(case_ids):
                if str(case_id) not in case_data:
                    continue

                case = ModLog.Case.from_dict(self.bot, self.guild, case_data[str(case_id)])
                content += f"`{case.case_number}` - {case.action_type.emoji or ''}`{case.action_type.name}` [<t:{int(case.timestamp)}:F>]\n"

            embed = discord.Embed(
                title=f"Case history for {self.member.name}",
                description=content,
                colour=await self.ctx.embed_color() or discord.Colour.blue())

            return embed

        async def pages(self) -> int:
            cases = len(self.member_cases)

            return int(ceil(cases / self.page_len)) if cases > self.page_len else 1


    DEFAULT_GUILD = {
        "case_sequence": 1,
        "cases": {},
        "user_cases": {}
    }

    DEFAULT_MEMBER = {
    }

    ACTION_TYPES={}

    CONFIG_IDENTIFIER = 1244378783399

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=ModLog.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self._background_tasks: set[asyncio.Task] = set()

    ### ----------------------------------------------------------------
    ### Utilities
    ### ----------------------------------------------------------------

    @staticmethod
    async def _case_to_detailed_embed(case: ModLog.Case) -> discord.Embed:
        embed = discord.Embed()

        embed.colour = case.action_type.color

        embed.timestamp = datetime.datetime.fromtimestamp(case.timestamp, tz=datetime.timezone.utc)

        embed.add_field(name="Case:", value=f"`{case.case_number}`✅", inline=True)
        embed.add_field(name="Type:", value=f"`{case.action_type.name}`{case.action_type.emoji or ''}", inline=True)

        if isinstance(case.moderator, Member) or isinstance(case.moderator, User):
            embed.add_field(name="Moderator:", value=f"`{case.moderator.name}`🛡", inline=False)
            embed.add_field(name="Moderator ID:", value=f"`{case.moderator.id}`", inline=False)
        else:
            embed.add_field(name="Moderator:", value=f"`Unavailable`🛡", inline=False)
            embed.add_field(name="Moderator ID:", value=f"`{case.moderator}`", inline=False)

        if isinstance(case.target, Member) or isinstance(case.target, User):
            embed.add_field(name="Target:", value=f"`{case.target.name}`🎯", inline=False)
            embed.add_field(name="Target ID:", value=f"`{case.target.id}`", inline=False)
            embed.set_thumbnail(url=case.target.display_avatar.url)
        else:
            embed.add_field(name="Target:", value=f"`Unavailable`🎯", inline=False)
            embed.add_field(name="Target ID:", value=f"`{case.target}`", inline=False)

        embed.add_field(name="Duration:", value=f"`{case.duration or 'Not specified.'}`⏳", inline=False)

        embed.add_field(name="Reason:", value=case.reason, inline=False)

        return embed

    @staticmethod
    async def _case_to_minimal_embed(case: ModLog.Case) -> discord.Embed:
        embed = discord.Embed()

        embed.colour = case.action_type.color

        embed.timestamp = datetime.datetime.fromtimestamp(case.timestamp, tz=datetime.timezone.utc)

        embed.add_field(name="Case:", value=f"`{case.case_number}`✅", inline=True)
        embed.add_field(name="Type:", value=f"`{case.action_type.name}`{case.action_type.emoji or ''}", inline=True)

        if isinstance(case.moderator, Member) or isinstance(case.moderator, User):
            embed.add_field(name="Moderator:", value=f"`{case.moderator.name}`🛡", inline=True)
        else:
            embed.add_field(name="Moderator:", value=f"`{case.moderator}`🛡", inline=True)

        if isinstance(case.target, Member) or isinstance(case.target, User):
            embed.add_field(name="Target:", value=f"`{case.target.name}`🎯", inline=False)
            embed.set_thumbnail(url=case.target.display_avatar.url)
        else:
            embed.add_field(name="Target:", value=f"`{case.target}`🎯", inline=False)

        if case.duration:
            embed.add_field(name="Duration:", value=f"`{case.duration}`⏳", inline=False)

        embed.add_field(name="Reason:", value=case.reason, inline=False)

        return embed

    def _attachment_dir(self, case_number: int) -> Path:
        directory = cog_data_path(self) / f"cases/{case_number}/attachments"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def _archive_attachments(
        self,
        case: ModLog.Case,
        attachments: List[Path],
    ) -> None:
        attachment_dir = self._attachment_dir(case_number=case.case_number)

        for file_path in attachments:
            src = Path(file_path)

            if src.is_file():
                shutil.copy2(src, attachment_dir)
            else:
                log.warning(f"Attachment \"{file_path}\" could not be archived for case number {case.case_number}, the file could not be found.")

    ### ----------------------------------------------------------------
    ### Action Type registration / Case creation
    ### ----------------------------------------------------------------

    @staticmethod
    def register_action_type(action_type: ActionType):
        """Registers an action type to the modlog."""

        ModLog.ACTION_TYPES[action_type.type] = action_type

    @staticmethod
    async def create_case(bot: Red, guild: Guild, action_type: str, moderator: Member | User, target: Member | User | int, reason: str | None = None, duration: str | None = None, attachments: List[Path] | None = None) -> ModLog.Case | None:
        """Creates and logs a new modlog case."""

        if not action_type in ModLog.ACTION_TYPES:
            raise NotSupportedError(f"Action type \"{action_type}\" has not been registered.")

        action_type = ModLog.ACTION_TYPES[action_type]

        config = Config.get_conf(cog_instance=None, cog_name="ModLog", identifier=ModLog.CONFIG_IDENTIFIER, force_registration=True)

        if isinstance(target, int):
            target_member_id = target
            target_member = guild.get_member(target) or bot.get_user(target)
        else:
            target_member_id = target.id
            target_member = target

        async with config.guild(guild)() as guild_data:
            case_number = guild_data["case_sequence"]
            guild_data["case_sequence"] = case_number + 1

        case = ModLog.Case(
            action_type=action_type,
            case_number=case_number,
            moderator=moderator,
            target=target_member or target_member_id,
            reason=reason or f"Responsible moderator, use `[p]reason {case_number}` to set the reason for this case.",
            timestamp=datetime.datetime.now(datetime.timezone.utc).timestamp(),
            duration=duration,
            attachments=attachments
        )

        embed = await ModLog._case_to_minimal_embed(case)

        channel = None

        # Add case to the config first, worry about the channel stuff later.

        async with config.guild(guild)() as guild_data:
            guild_data["cases"][str(case_number)] = case.to_dict()
            guild_data["user_cases"].setdefault(str(target_member_id), []).append(case.case_number)

        # If a channel exists for modlog, print it out, otherwise we don't care, we can
        # store cases regardless of the presence of a modlog output channel, and
        # [p]cases will still work normally.

        try:
            channel = await modlog.get_modlog_channel(guild)
        except (discord.HTTPException, RuntimeError):
            log.exception("No modlog channel has been configured. Modlog is disabled.")

        if channel:
            try:
                if len(case.attachments) > 0:
                    files = [discord.File(str(p)) for p in case.attachments]
                    message = await channel.send(embed=embed, files=files)
                else:
                    message = await channel.send(embed=embed)

                case.channel_id = message.channel.id
                case.message_id = message.id
            except discord.HTTPException:
                log.exception(f"Failed to post case {case_number} to the modlog.")

        return case

    ### ----------------------------------------------------------------
    ### Moderator feedback
    ### ----------------------------------------------------------------

    @staticmethod
    async def confirm_action(ctx: Context, *, action_type: str, target: Member | User, reason: str | None = None, duration: str | None = None, title: str = "Confirm action", timeout: int = 30, ephemeral: bool = True) -> bool:
        """Asks the moderator to confirm an action before it is carried out."""

        if action_type in ModLog.ACTION_TYPES:
            registered_action_type = ModLog.ACTION_TYPES[action_type]
            action = f"`{registered_action_type.name}`{registered_action_type.emoji or ''}"
        else:
            log.warning(f"Confirming an action with no registered action type for {action_type}.")
            action = f"`{action_type}`"

        view = ConfirmationView(author=ctx.author, timeout=timeout)
        embed = discord.Embed(title=title, color=discord.Colour.yellow())

        embed.set_footer(text=f"You have {timeout} seconds to confirm.")

        embed.add_field(name="Type:", value=action, inline=True)
        embed.add_field(name="Target:", value=f"`{target.name}`", inline=True)
        embed.add_field(name="Target ID:", value=f"`{target.id}`", inline=True)

        if duration:
            embed.add_field(name="Duration:", value=f"`{duration}`", inline=False)

        if reason:
            embed.add_field(name="Reason:", value=f"{reason}", inline=False)

        message = await ctx.send(
            view=view,
            embed=embed,
            ephemeral=ephemeral
        )

        await view.wait()

        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        if view.value is None:
            embed.set_footer(text="Timed out")
            embed.colour = discord.Colour.red()
            await message.edit(embed=embed, view=None)
            return False

        if not view.value:
            embed.set_footer(text="Cancelled")
            embed.colour = discord.Colour.dark_grey()
            await message.edit(embed=embed, view=None)
            return False

        embed.set_footer(text="Confirmed")
        embed.colour = discord.Colour.green()
        await message.edit(embed=embed, view=None)
        return True

    @staticmethod
    async def send_case_action_summary(ctx: Context, case: ModLog.Case | None, *, note: str | None = None, ephemeral: bool = True) -> None:
        """Reports a completed moderation action back to the moderator who carried it out."""

        # A case is not guaranteed, the action type may be unregistered or the guild may have no modlog
        # channel configured. The action itself still happened, so the moderator still needs to hear about it.

        if not case:
            content = "The action completed, but no modlog case could be created for it."

            if note:
                content = f"{content} {note}"

            await ctx.send(content, ephemeral=ephemeral)
            return

        embed = await ModLog._case_to_minimal_embed(case)

        # The note belongs in the description rather than a field, "Reason" must remain the last field.

        if note:
            embed.description = note

        try:
            await ctx.send(embed=embed, ephemeral=ephemeral)
        except discord.HTTPException:
            log.exception(f"Failed to respond with the summary for case {case.case_number}.")

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

        case_dict = None

        async with self.config.guild(guild).cases() as cases:
            case_number_index = str(case_number)
            if case_number_index in cases:
                case_dict = cases[case_number_index]
                cases[case_number_index]["reason"] = reason

        if not case_dict:
            await ctx.send(f"Case `{case_number}` could not be found.", ephemeral=True)
            return

        await ctx.send(f"Case `{case_number}` has been updated.")

        # At this point the reason is updated within the database, the rest of this is just updating the message / UI
        # which could be deleted and no longer valid, so treat any error as tolerable. Commands to lookup case details
        # will still work just fine regardless.

        if not case_dict["channel_id"]:
            return

        channel = guild.get_channel(case_dict["channel_id"])

        if not channel or not case_dict["message_id"]:
            return

        message = await channel.fetch_message(case_dict["message_id"])

        if not message:
            return

        embed = message.embeds[0]

        if not embed:
            return

        embed.set_field_at(len(embed.fields) - 1, name="Reason:", value=reason, inline=False)

        await message.edit(embed=embed)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    @commands.hybrid_command("case", aliases=["c"])
    async def case(self, ctx: commands.Context, case_number: int) -> None:
        """Returns details about a modlog case."""

        guild = ctx.guild

        if not guild:
            return

        case = None

        async with self.config.guild(guild).cases() as cases:
            case_number_index = str(case_number)
            if case_number_index in cases:
                case_dict = cases[case_number_index]
                case = ModLog.Case.from_dict(self.bot, guild, case_dict)

        if not case:
            await ctx.send(f"Unable to locate modlog case `{case_number}`.", ephemeral=True)
            return

        embed = await ModLog._case_to_detailed_embed(case)

        try:
            if len(case.attachments) > 0:
                files = [discord.File(str(p)) for p in case.attachments]
                await ctx.send(embed=embed, files=files)
            else:
                await ctx.send(embed=embed)
        except discord.HTTPException:
            log.exception(f"Failed to respond with case {case_number}.")

    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    @commands.hybrid_command("cases")
    async def cases(self, ctx: commands.Context, target: Member) -> None:
        """Returns modlog cases for a specific user."""

        guild = ctx.guild

        if not guild:
            return

        user_case_data = await self.config.guild(guild).user_cases()

        if str(target.id) not in user_case_data:
            await ctx.send(f"No modlog cases found for `{target.name}`.", ephemeral=True)
            return

        await Interactions.page(
            ctx=ctx,
            provider=ModLog.CasePageEmbedProvider(
                bot=self.bot,
                guild=guild,
                config=self.config,
                ctx=ctx,
                member=target)
        )