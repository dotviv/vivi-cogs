"""Gate new members behind an image captcha, entered through a button panel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list

from . import captcha
from .views import CaptchaPrompt, VerificationPanel

log = logging.getLogger("red.vivi-cogs.verification")

FAILURE_ACTIONS = ("none", "kick")

PASS_COLOUR = discord.Colour.green()
FAIL_COLOUR = discord.Colour.orange()
LOCKOUT_COLOUR = discord.Colour.red()


class Verification(commands.Cog):
    """Require new members to solve an image captcha before they are let in."""

    __author__ = "vivinancy"
    __version__ = "1.1.0"

    DEFAULT_GUILD = {
        "enabled": False,
        "channel_id": None,
        "panel_message_id": None,
        "modlog_channel_id": None,
        "join_roles": [],
        "add_roles": [],
        "remove_roles": [],
        "code_length": 6,
        "max_attempts": 3,
        "timeout": 600,
        "on_failure": "none",
    }

    DEFAULT_MEMBER = {
        "code": None,
        "attempts": 0,
        "expires_at": None,
        "locked_out": False,
    }

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        # This identifier keys every guild's stored settings. Never change it.
        self.config = Config.get_conf(self, identifier=1357924680, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self._sweep_expired.start()

    async def cog_load(self) -> None:
        # Re-registers the panel button so it keeps working after a restart.
        # Handlers are keyed by custom_id, so a cog reload overwrites rather
        # than stacking duplicates.
        self.bot.add_view(VerificationPanel(self))

    def cog_unload(self) -> None:
        self._sweep_expired.cancel()

    def format_help_for_context(self, ctx: commands.Context) -> str:
        return f"{super().format_help_for_context(ctx)}\n\nAuthor: {self.__author__}\nVersion: {self.__version__}"

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """Drop any pending verification record belonging to ``user_id``."""
        for guild_id, members in (await self.config.all_members()).items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()

    # ------------------------------------------------------------------
    # Role safety
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

    async def _apply_roles(
        self,
        member: discord.Member,
        *,
        add: List[int],
        remove: List[int],
        reason: str,
    ) -> None:
        guild = member.guild
        to_add = [r for r in (guild.get_role(i) for i in add) if r and r not in member.roles]
        to_remove = [r for r in (guild.get_role(i) for i in remove) if r and r in member.roles]
        try:
            if to_add:
                await member.add_roles(*to_add, reason=reason)
            if to_remove:
                await member.remove_roles(*to_remove, reason=reason)
        except discord.Forbidden:
            log.warning(
                "Missing permissions to update roles for %s in guild %s.", member.id, guild.id
            )
        except discord.HTTPException as error:
            log.warning("Failed to update roles for %s: %s", member.id, error)

    @staticmethod
    def _already_verified(member: discord.Member, conf: dict) -> bool:
        held = {role.id for role in member.roles}
        if conf["add_roles"]:
            return all(role_id in held for role_id in conf["add_roles"])
        # Guilds that only strip an "Unverified" role have no grant list, so
        # the absence of every removal role is what marks them as done.
        if conf["remove_roles"]:
            return not any(role_id in held for role_id in conf["remove_roles"])
        return False

    # ------------------------------------------------------------------
    # Mod log
    # ------------------------------------------------------------------

    async def _modlog(
        self,
        member: discord.Member,
        *,
        title: str,
        description: str,
        colour: discord.Colour,
    ) -> None:
        """Post a verification event to the mod-log channel, if one is set."""
        channel_id = await self.config.guild(member.guild).modlog_channel_id()
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if channel is None:
            return
        embed = discord.Embed(
            title=title,
            description=description,
            colour=colour,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=f"{member} ({member.id})", icon_url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not write to the mod-log channel in guild %s.", member.guild.id)

    # ------------------------------------------------------------------
    # Outcomes
    # ------------------------------------------------------------------

    async def _succeed(self, member: discord.Member, conf: dict) -> None:
        await self._apply_roles(
            member,
            add=conf["add_roles"],
            remove=conf["remove_roles"],
            reason="Verification: captcha solved",
        )
        await self.config.member(member).clear()
        await self._modlog(
            member,
            title="Verification passed",
            description=f"{member.mention} solved the captcha.",
            colour=PASS_COLOUR,
        )

    async def _lock_out(self, member: discord.Member, conf: dict, reason: str) -> None:
        """Attempts exhausted: block further tries until a mod resets them."""
        await self.config.member(member).set(
            {"code": None, "attempts": 0, "expires_at": None, "locked_out": True}
        )
        await self._modlog(
            member,
            title="Verification locked out",
            description=f"{member.mention} {reason}.",
            colour=LOCKOUT_COLOUR,
        )
        await self._apply_failure_action(member, conf, reason)

    async def _expire(self, member: discord.Member, conf: dict) -> None:
        """Timed out mid-flow. Attempts are preserved -- they can start over."""
        await self.config.member(member).code.set(None)
        await self.config.member(member).expires_at.set(None)
        await self._modlog(
            member,
            title="Verification expired",
            description=f"{member.mention} did not finish in time.",
            colour=FAIL_COLOUR,
        )
        await self._apply_failure_action(member, conf, "did not verify in time")

    async def _apply_failure_action(
        self, member: discord.Member, conf: dict, reason: str
    ) -> None:
        if conf["on_failure"] != "kick":
            return
        try:
            await member.kick(reason=f"Verification: {reason}")
        except discord.Forbidden:
            log.warning(
                "Configured to kick on failed verification but missing permissions in guild %s.",
                member.guild.id,
            )
        except discord.HTTPException as error:
            log.warning("Failed to kick %s: %s", member.id, error)

    # ------------------------------------------------------------------
    # Interaction handling
    # ------------------------------------------------------------------

    async def handle_panel_click(self, interaction: discord.Interaction) -> None:
        """Someone pressed Verify on the panel."""
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            return

        conf = await self.config.guild(interaction.guild).all()
        if not conf["enabled"]:
            await interaction.response.send_message(
                "Verification isn't enabled here right now.", ephemeral=True
            )
            return

        if self._already_verified(member, conf):
            await interaction.response.send_message(
                "You're already verified — nothing to do here.", ephemeral=True
            )
            return

        state = await self.config.member(member).all()
        if state["locked_out"]:
            await interaction.response.send_message(
                "You've used all of your attempts. Please contact a moderator for help.",
                ephemeral=True,
            )
            return

        # Only refill the attempt budget when nothing is already in flight --
        # otherwise clicking Verify again would hand out a fresh set of tries
        # and the attempt limit would mean nothing.
        attempts = state["attempts"] if state["code"] else conf["max_attempts"]
        await self._send_captcha(interaction, member, conf, attempts)

    async def handle_code_submit(
        self, interaction: discord.Interaction, issued_code: str, submitted: str
    ) -> None:
        """Someone submitted the modal."""
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            return

        conf = await self.config.guild(interaction.guild).all()
        state = await self.config.member(member).all()

        # A newer captcha was issued after this prompt was opened. Reject it
        # without decrementing -- the member didn't cause this race.
        if not state["code"] or state["code"] != issued_code:
            await interaction.response.send_message(
                "That captcha is no longer valid. Press **Verify** again for a new one.",
                ephemeral=True,
            )
            return

        if state["expires_at"] and state["expires_at"] < datetime.now(timezone.utc).timestamp():
            await self._expire(member, conf)
            await interaction.response.send_message(
                "That captcha expired. Press **Verify** again for a new one.", ephemeral=True
            )
            return

        if submitted.strip().upper() == state["code"].upper():
            await self._succeed(member, conf)
            await interaction.response.send_message(
                f"Verified — welcome to **{interaction.guild.name}**!", ephemeral=True
            )
            return

        remaining = state["attempts"] - 1
        if remaining <= 0:
            await self._lock_out(member, conf, "ran out of attempts")
            await interaction.response.send_message(
                "That was incorrect, and it was your last attempt. "
                "Please contact a moderator for help.",
                ephemeral=True,
            )
            return

        await self._modlog(
            member,
            title="Verification attempt failed",
            description=f"{member.mention} submitted an incorrect code. {remaining} left.",
            colour=FAIL_COLOUR,
        )
        # Burn the failed code and issue a fresh one.
        await self._send_captcha(
            interaction, member, conf, remaining, note="That code was incorrect."
        )

    async def _send_captcha(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        conf: dict,
        attempts: int,
        *,
        note: Optional[str] = None,
    ) -> None:
        """Generate a code, persist it, and reply with the image, privately."""
        code = captcha.generate_code(conf["code_length"])
        await self.config.member(member).set(
            {
                "code": code,
                "attempts": attempts,
                "expires_at": datetime.now(timezone.utc).timestamp() + conf["timeout"],
                "locked_out": False,
            }
        )

        description = (
            f"Type the **{conf['code_length']} characters** shown below, then press "
            "**Enter Code**.\nLetters are not case-sensitive."
        )
        embed = discord.Embed(
            title="Verification",
            description=f"{note}\n\n{description}" if note else description,
            colour=discord.Colour.blurple(),
        )
        embed.set_image(url="attachment://captcha.png")
        embed.set_footer(text=f"{attempts} attempt(s) remaining")

        await interaction.response.send_message(
            embed=embed,
            file=discord.File(captcha.render(code), filename="captcha.png"),
            view=CaptchaPrompt(self, code, conf["timeout"]),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        conf = await self.config.guild(member.guild).all()
        if not conf["enabled"] or not conf["join_roles"]:
            return
        await self._apply_roles(
            member, add=conf["join_roles"], remove=[], reason="Verification: pending"
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.config.member(member).clear()

    # ------------------------------------------------------------------
    # Expiry sweep
    # ------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _sweep_expired(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        for guild_id, members in (await self.config.all_members()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            conf = await self.config.guild(guild).all()
            for member_id, state in members.items():
                # Only people who actually started and stalled mid-flow.
                if not state.get("code") or not state.get("expires_at"):
                    continue
                if state["expires_at"] > now:
                    continue
                member = guild.get_member(member_id)
                if member is None:
                    await self.config.member_from_ids(guild_id, member_id).clear()
                    continue
                await self._expire(member, conf)

    @_sweep_expired.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_red_ready()

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="verifyset")
    async def verifyset(self, ctx: commands.Context) -> None:
        """Configure member verification."""

    @verifyset.command(name="channel")
    async def verifyset_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel the verification panel lives in."""
        missing = self._missing_channel_perms(ctx.guild, channel)
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        # A panel in the old channel is no longer ours to track.
        await self.config.guild(ctx.guild).panel_message_id.set(None)
        message = (
            f"Verification channel set to {channel.mention}. "
            f"Post the panel with `{ctx.clean_prefix}verifyset panel`."
        )
        if missing:
            message += f"\n\nHeads up — I'm missing {humanize_list(missing)} there."
        await ctx.send(message)

    @staticmethod
    def _missing_channel_perms(
        guild: discord.Guild, channel: discord.TextChannel
    ) -> List[str]:
        perms = channel.permissions_for(guild.me)
        return [
            name
            for name, has in (
                ("Send Messages", perms.send_messages),
                ("Embed Links", perms.embed_links),
                ("Attach Files", perms.attach_files),
            )
            if not has
        ]

    def _panel_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title=f"Welcome to {guild.name}",
            description=(
                "This server is protected by captcha verification.\n\n"
                "Press **Verify** below to receive a code that only you can see, "
                "then type it into the form that appears."
            ),
            colour=discord.Colour.blurple(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return embed

    @verifyset.command(name="panel")
    async def verifyset_panel(self, ctx: commands.Context) -> None:
        """Post (or refresh) the verification panel."""
        conf = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(conf["channel_id"]) if conf["channel_id"] else None
        if channel is None:
            await ctx.send(
                f"Set a verification channel first with `{ctx.clean_prefix}verifyset channel`."
            )
            return

        missing = self._missing_channel_perms(ctx.guild, channel)
        if missing:
            await ctx.send(f"I need {humanize_list(missing)} in {channel.mention} first.")
            return

        embed = self._panel_embed(ctx.guild)
        view = VerificationPanel(self)
        message = None

        if conf["panel_message_id"]:
            try:
                message = await channel.fetch_message(conf["panel_message_id"])
                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if message is None:
            message = await channel.send(embed=embed, view=view)
            await self.config.guild(ctx.guild).panel_message_id.set(message.id)
            await ctx.send(f"Panel posted in {channel.mention}.")
        else:
            await ctx.send(f"Existing panel in {channel.mention} refreshed.")

        if channel.permissions_for(ctx.guild.default_role).send_messages:
            await ctx.send(
                f"One more thing: `@everyone` can still send messages in {channel.mention}. "
                "Nothing about this flow needs them to, so denying **Send Messages** there "
                "closes the last public surface in the channel."
            )

    @verifyset.command(name="modlog")
    async def verifyset_modlog(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Set a channel for verification events. Omit the channel to turn it off."""
        if channel is None:
            await self.config.guild(ctx.guild).modlog_channel_id.set(None)
            await ctx.send("Verification logging disabled.")
            return
        await self.config.guild(ctx.guild).modlog_channel_id.set(channel.id)
        await ctx.send(f"Verification events will be logged to {channel.mention}.")

    async def _modify_role_list(
        self, ctx: commands.Context, key: str, role: discord.Role, *, adding: bool
    ) -> None:
        if adding:
            problem = self._role_problem(ctx.guild, role)
            if problem:
                await ctx.send(problem)
                return
        async with self.config.guild(ctx.guild).get_attr(key)() as roles:
            if adding:
                if role.id in roles:
                    await ctx.send(f"{role.mention} is already in that list.")
                    return
                roles.append(role.id)
            else:
                if role.id not in roles:
                    await ctx.send(f"{role.mention} isn't in that list.")
                    return
                roles.remove(role.id)
        await ctx.send(
            f"{role.mention} {'added to' if adding else 'removed from'} the `{key}` list."
        )

    @verifyset.group(name="joinrole")
    async def verifyset_joinrole(self, ctx: commands.Context) -> None:
        """Roles applied the moment someone joins (e.g. Unverified)."""

    @verifyset_joinrole.command(name="add")
    async def joinrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Apply this role on join."""
        await self._modify_role_list(ctx, "join_roles", role, adding=True)

    @verifyset_joinrole.command(name="remove")
    async def joinrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Stop applying this role on join."""
        await self._modify_role_list(ctx, "join_roles", role, adding=False)

    @verifyset.group(name="addrole")
    async def verifyset_addrole(self, ctx: commands.Context) -> None:
        """Roles granted once verification succeeds."""

    @verifyset_addrole.command(name="add")
    async def addrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Grant this role on success."""
        await self._modify_role_list(ctx, "add_roles", role, adding=True)

    @verifyset_addrole.command(name="remove")
    async def addrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Stop granting this role on success."""
        await self._modify_role_list(ctx, "add_roles", role, adding=False)

    @verifyset.group(name="removerole")
    async def verifyset_removerole(self, ctx: commands.Context) -> None:
        """Roles stripped once verification succeeds."""

    @verifyset_removerole.command(name="add")
    async def removerole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Strip this role on success."""
        await self._modify_role_list(ctx, "remove_roles", role, adding=True)

    @verifyset_removerole.command(name="remove")
    async def removerole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Stop stripping this role on success."""
        await self._modify_role_list(ctx, "remove_roles", role, adding=False)

    @verifyset.command(name="attempts")
    async def verifyset_attempts(self, ctx: commands.Context, amount: int) -> None:
        """Set how many tries a member gets before lockout (1-10)."""
        if not 1 <= amount <= 10:
            await ctx.send("Pick a number between 1 and 10.")
            return
        await self.config.guild(ctx.guild).max_attempts.set(amount)
        await ctx.send(f"Members now get **{amount}** attempt(s) before lockout.")

    @verifyset.command(name="timeout")
    async def verifyset_timeout(self, ctx: commands.Context, seconds: int) -> None:
        """Set how long a captcha stays valid, in seconds (60-900)."""
        if not 60 <= seconds <= 900:
            await ctx.send(
                "Pick a value between 60 and 900 seconds. Discord expires the "
                "private captcha message after about 15 minutes, so anything "
                "longer couldn't be honoured anyway."
            )
            return
        await self.config.guild(ctx.guild).timeout.set(seconds)
        await ctx.send(f"Captchas now expire after **{seconds}** seconds.")

    @verifyset.command(name="length")
    async def verifyset_length(self, ctx: commands.Context, characters: int) -> None:
        """Set how many characters the code contains (4-10)."""
        if not 4 <= characters <= 10:
            await ctx.send("Pick a length between 4 and 10.")
            return
        await self.config.guild(ctx.guild).code_length.set(characters)
        await ctx.send(f"Codes are now **{characters}** characters long.")

    @verifyset.command(name="onfail")
    async def verifyset_onfail(self, ctx: commands.Context, action: str) -> None:
        """What to do when someone fails or times out: `none` or `kick`."""
        action = action.lower()
        if action not in FAILURE_ACTIONS:
            await ctx.send(f"Choose one of: {humanize_list(list(FAILURE_ACTIONS))}.")
            return
        if action == "kick" and not ctx.guild.me.guild_permissions.kick_members:
            await ctx.send("I need the **Kick Members** permission for that.")
            return
        await self.config.guild(ctx.guild).on_failure.set(action)
        if action == "kick":
            await ctx.send(
                "Members who fail or time out will now be kicked. Note this only "
                "applies to people who actually start verifying — someone who "
                "joins and never presses the button is never kicked."
            )
        else:
            await ctx.send("Members who fail will stay unverified and can be reset by a mod.")

    @verifyset.command(name="toggle")
    async def verifyset_toggle(self, ctx: commands.Context) -> None:
        """Turn verification on or off."""
        conf = await self.config.guild(ctx.guild).all()
        if conf["enabled"]:
            await self.config.guild(ctx.guild).enabled.set(False)
            await ctx.send("Verification disabled.")
            return

        problems = []
        if not conf["channel_id"] or ctx.guild.get_channel(conf["channel_id"]) is None:
            problems.append("Set a verification channel with `[p]verifyset channel`.")
        if not conf["panel_message_id"]:
            problems.append(
                "Post the panel with `[p]verifyset panel` — it's the only way in now."
            )
        if not conf["add_roles"] and not conf["remove_roles"]:
            problems.append(
                "Configure at least one role to add or remove with "
                "`[p]verifyset addrole add` or `[p]verifyset removerole add`."
            )
        if problems:
            listed = "\n".join(f"- {p.replace('[p]', ctx.clean_prefix)}" for p in problems)
            await ctx.send(f"Not ready yet:\n{listed}")
            return

        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Verification enabled.")

    @verifyset.command(name="settings")
    async def verifyset_settings(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        conf = await self.config.guild(ctx.guild).all()

        def render_roles(ids: List[int]) -> str:
            mentions = [r.mention for r in (ctx.guild.get_role(i) for i in ids) if r]
            return humanize_list(mentions) if mentions else "*none*"

        def render_channel(channel_id: Optional[int]) -> str:
            channel = ctx.guild.get_channel(channel_id) if channel_id else None
            return channel.mention if channel else "*not set*"

        embed = discord.Embed(title="Verification settings", colour=await ctx.embed_colour())
        embed.add_field(
            name="Status", value="Enabled" if conf["enabled"] else "Disabled", inline=True
        )
        embed.add_field(name="Channel", value=render_channel(conf["channel_id"]), inline=True)
        embed.add_field(
            name="Panel", value="Posted" if conf["panel_message_id"] else "*not posted*", inline=True
        )
        embed.add_field(name="Roles on join", value=render_roles(conf["join_roles"]), inline=False)
        embed.add_field(name="Added on success", value=render_roles(conf["add_roles"]), inline=False)
        embed.add_field(
            name="Removed on success", value=render_roles(conf["remove_roles"]), inline=False
        )
        embed.add_field(name="Code length", value=str(conf["code_length"]), inline=True)
        embed.add_field(name="Attempts", value=str(conf["max_attempts"]), inline=True)
        embed.add_field(name="Timeout", value=f"{conf['timeout']}s", inline=True)
        embed.add_field(name="On failure", value=f"`{conf['on_failure']}`", inline=True)
        embed.add_field(
            name="Mod log", value=render_channel(conf["modlog_channel_id"]), inline=True
        )
        await ctx.send(embed=embed)

    @verifyset.command(name="test")
    async def verifyset_test(self, ctx: commands.Context) -> None:
        """Preview a captcha here. Changes no roles and records no state."""
        conf = await self.config.guild(ctx.guild).all()
        code = captcha.generate_code(conf["code_length"])
        embed = discord.Embed(
            title="Captcha preview",
            description=f"This is only a preview. The code is ||`{code}`||.",
            colour=await ctx.embed_colour(),
        )
        embed.set_image(url="attachment://captcha.png")
        await ctx.send(
            embed=embed, file=discord.File(captcha.render(code), filename="captcha.png")
        )

    # ------------------------------------------------------------------
    # Moderator commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.group(name="verify")
    async def verify(self, ctx: commands.Context) -> None:
        """Manually resolve a member's verification."""

    @verify.command(name="approve")
    async def verify_approve(self, ctx: commands.Context, member: discord.Member) -> None:
        """Pass a member through without solving the captcha."""
        conf = await self.config.guild(ctx.guild).all()
        await self._succeed(member, conf)
        await ctx.send(f"{member.mention} has been manually verified.")

    @verify.command(name="reject")
    async def verify_reject(self, ctx: commands.Context, member: discord.Member) -> None:
        """Lock a member out and apply the configured failure action."""
        conf = await self.config.guild(ctx.guild).all()
        await self._lock_out(member, conf, f"was rejected by {ctx.author}")
        await ctx.send(f"{member.mention}'s verification was rejected.")

    @verify.command(name="reset")
    async def verify_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        """Clear a lockout so a member can try again."""
        await self.config.member(member).clear()
        await ctx.send(f"{member.mention} can verify again.")
