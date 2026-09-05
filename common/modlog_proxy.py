"""Talk to the ModLog cog without depending on it.

Every cog in this repo that records moderation or security activity goes
through a ``ModLogProxy`` rather than importing ModLog. Three reasons:

* **ModLog is optional.** If it is not installed, cases fall back to Red's core
  modlog, which is less capable but always present.
* **A cross-cog import cannot survive a reload.** Red reloads a cog by
  re-executing its module in place, which rebinds the class object. An importer
  keeps the old class forever. The proxy resolves ``bot.get_cog("ModLog")`` per
  call instead, so it always holds the live instance.
* **Classes must not cross cog boundaries.** Each cog ships its own vendored
  copy of this package (see ``tools/sync_common.py``), so ModLog's ``Case`` and
  ``ActionType`` are foreign types here. The proxy speaks primitives and hands
  back a local `CaseRef`.

Usage from a cog::

    ACTION_TYPES = (
        {"type": "warn", "name": "Warning", "color": discord.Colour.yellow(), "emoji": "!"},
    )

    def __init__(self, bot):
        self.bot = bot
        self.modlog = ModLogProxy(self, action_types=self.ACTION_TYPES)

    async def cog_load(self):
        await self.modlog.refresh()

    @commands.Cog.listener()
    async def on_cog_add(self, cog):
        await self.modlog.on_cog_add(cog)
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

import discord
from discord import Colour, Member, User, Guild
from redbot.core import commands, modlog as core_modlog
from redbot.core.commands import Context

from .actor_tiers import resolve_actor_display
from .interactions import ConfirmationView
from .modlog_render import build_case_embed, reason_field

log = logging.getLogger("red.vivi-cogs.common.modlog_proxy")

#: Qualified name of the ModLog cog, as Red knows it.
MODLOG_COG = "ModLog"

#: Fallback emoji for core casetype registration, which requires a string.
DEFAULT_IMAGE = "🔨"


@dataclass
class CaseRef:
    """A case, described in terms this cog owns.

    Deliberately not ModLog's ``Case``: that class belongs to another cog and
    would be a foreign type here. Carries enough to render a summary without
    calling back into ModLog.
    """

    action_type: str
    action_name: str
    action_color: Colour
    action_emoji: str | None
    target: Member | User | int | None
    fields: List[Dict[str, Any]]
    timestamp: float
    case_number: int | None = None
    actor: Member | User | int | None = None
    duration: str | None = None
    target_label: str = "Target"
    target_emoji: str | None = "🎯"
    actor_label: str = "Actor"
    actor_emoji: str | None = "🛡️"

    #: Which backend recorded this: "modlog", "core", or "none" for an event
    #: that was rendered but never stored as a case.
    source: str = "modlog"


class ModLogProxy:
    def __init__(
        self,
        cog: commands.Cog,
        *,
        action_types: Sequence[Dict[str, Any]] = (),
        core_fallback: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        cog
            The owning cog. Must expose ``bot``.
        action_types
            This cog's action types, as dicts of ``type``/``name``/``color``/
            ``emoji``. Held locally as well as pushed to ModLog, so display
            names resolve even when ModLog is not loaded.
        core_fallback
            Whether cases may fall back to Red's core modlog. Turn this off for
            anything whose visibility matters: core's ``[p]case`` and
            ``[p]casesfor`` are guild-only, so any member can read them, while
            ModLog gates lookups behind mod permissions.
        """
        self.cog = cog
        self.bot = cog.bot
        self.core_fallback = core_fallback
        self._declared: Dict[str, Dict[str, Any]] = {
            declared["type"]: declared for declared in action_types
        }

    ### ----------------------------------------------------------------
    ### Resolution and registration
    ### ----------------------------------------------------------------

    @property
    def cog_instance(self):
        """The live ModLog cog, or None. Never cached -- see the module docstring."""
        return self.bot.get_cog(MODLOG_COG)

    @property
    def available(self) -> bool:
        """Whether the full-featured ModLog is loaded right now."""
        return self.cog_instance is not None

    @property
    def supports_attachments(self) -> bool:
        """Whether attachments will actually be kept.

        Red's core modlog has no equivalent, so callers with something to
        attach -- a quarantine transcript, say -- should check this and tell
        the actor when it will not be retained.
        """
        return self.available

    async def refresh(self) -> None:
        """Push this cog's action types to whichever backend is available.

        Safe to call repeatedly; that is the point. Registrations live on the
        ModLog instance, so they are lost whenever ModLog reloads, and the only
        way to get them back is to replay them from the cog that owns them.
        """
        modlog = self.cog_instance

        if modlog is not None:
            for declared in self._declared.values():
                modlog.register_action_type(**declared)

        if self.core_fallback:
            await self._register_core_casetypes()

    async def on_cog_add(self, cog: commands.Cog) -> None:
        """Hook for the owning cog's ``on_cog_add`` listener."""
        if cog.qualified_name == MODLOG_COG:
            await self.refresh()

    async def _register_core_casetypes(self) -> None:
        """Mirror the declarations into Red's core modlog for the fallback path.

        Core raises if a casetype is re-registered with values identical to what
        is stored, which makes replay noisy rather than idempotent. That
        particular RuntimeError means "already correct", so it is suppressed.
        """
        for declared in self._declared.values():
            try:
                await core_modlog.register_casetype(
                    name=declared["type"],
                    default_setting=True,
                    image=declared.get("emoji") or DEFAULT_IMAGE,
                    case_str=declared["name"],
                )
            except RuntimeError:
                pass
            except (ValueError, TypeError):
                log.exception(
                    "Could not register core casetype %s for the modlog fallback.",
                    declared["type"],
                )

    def _display(self, action_type: str) -> Dict[str, Any]:
        """Display attributes for an action type.

        Prefers ModLog's registry so a type registered by another cog still
        renders correctly, then falls back to this cog's own declarations, then
        to the bare type name.

        ``actor_label``/``actor_emoji`` are ``None`` unless explicitly
        registered/declared -- that's the "not configured, resolve it
        dynamically" sentinel ``resolve_actor_display`` expects.
        ``target_label``/``target_emoji`` are never dynamic, so those always
        carry a concrete default.
        """
        modlog = self.cog_instance

        if modlog is not None:
            registered = modlog.action_type(action_type)
            if registered is not None:
                return {
                    "name": registered.name,
                    "color": registered.color,
                    "emoji": registered.emoji,
                    "target_label": registered.target_label,
                    "target_emoji": registered.target_emoji,
                    "actor_label": registered.actor_label,
                    "actor_emoji": registered.actor_emoji,
                }

        declared = self._declared.get(action_type)

        if declared is not None:
            return {
                "name": declared["name"],
                "color": declared["color"],
                "emoji": declared.get("emoji"),
                "target_label": declared.get("target_label", "Target"),
                "target_emoji": declared.get("target_emoji", "🎯"),
                "actor_label": declared.get("actor_label"),
                "actor_emoji": declared.get("actor_emoji"),
            }

        log.warning("No registered or declared action type for %s.", action_type)
        return {
            "name": action_type,
            "color": discord.Colour.light_grey(),
            "emoji": None,
            "target_label": "Target",
            "target_emoji": "🎯",
            "actor_label": None,
            "actor_emoji": None,
        }

    ### ----------------------------------------------------------------
    ### Cases
    ### ----------------------------------------------------------------

    async def create_case(
        self,
        guild: Guild,
        *,
        action_type: str,
        target: Member | User | int | None = None,
        actor: Member | User | int | None = None,
        fields: List[Dict[str, Any]] | None = None,
        duration: str | None = None,
        attachments: List[Path] | None = None,
    ) -> CaseRef | None:
        """Record a case, using ModLog if present and core modlog otherwise.

        ``target`` may be omitted for a global or actor-only action with
        no single member it happened to -- ModLog still records and posts it,
        filed under the actor's ``[p]actions`` rather than anyone's
        ``[p]cases``. Core cannot represent this at all; see
        :meth:`_create_core_case`.

        Returns None when neither backend recorded anything -- ModLog absent
        with ``core_fallback`` off, core refusing the casetype, or the casetype
        being disabled in this guild. Callers should treat None as "the action
        happened but was not recorded" and say so.
        """
        modlog = self.cog_instance

        if modlog is not None:
            case = await modlog.create_case(
                guild,
                action_type=action_type,
                target=target,
                actor=actor,
                fields=fields,
                duration=duration,
                attachments=attachments,
            )
            return self._case_ref_from_modlog_case(case)

        if not self.core_fallback:
            log.debug(
                "ModLog is not loaded and core fallback is disabled; "
                "no case recorded for %s.", action_type
            )
            return None

        return await self._create_core_case(
            guild,
            action_type=action_type,
            target=target,
            actor=actor,
            fields=fields,
            duration=duration,
            attachments=attachments,
        )

    @staticmethod
    def _flatten_fields(fields: List[Dict[str, Any]] | None) -> str:
        """Collapse structured fields into the single ``reason`` string core's
        own ``create_case`` accepts.

        Nothing is lost here the way attachments/duration are for this
        fallback -- every field survives, just reformatted as prose -- so this
        gets no warning/debug line the way those do.
        """
        return "\n".join(f"**{entry['name']}:** {entry['content']}" for entry in (fields or []))

    async def _create_core_case(
        self,
        guild: Guild,
        *,
        action_type: str,
        target: Member | User | int | None,
        actor: Member | User | int | None,
        fields: List[Dict[str, Any]] | None,
        duration: str | None,
        attachments: List[Path] | None,
    ) -> CaseRef | None:
        if target is None:
            # Core's create_case takes the target as a required positional
            # argument -- there is no way to represent a targetless entry, so
            # this degrades the same way a disabled or unregistered casetype
            # does: log it, record nothing.
            log.debug(
                "Core modlog requires a target, so no case was recorded for the "
                "targetless %s action.", action_type
            )
            return None

        if attachments:
            # Core modlog cannot attach anything to a case. Losing a quarantine
            # transcript silently would be worse than the degraded log line.
            log.warning(
                "Core modlog cannot store the %s attachment(s) for this %s case; "
                "install vivi-cogs/ModLog to retain them.",
                len(attachments),
                action_type,
            )

        if duration:
            # Core takes an `until` datetime, which a free-form duration string
            # cannot be converted into reliably. Kept for rendering only.
            log.debug("Core modlog cannot record the duration %r.", duration)

        try:
            case = await core_modlog.create_case(
                self.bot,
                guild,
                datetime.datetime.now(datetime.timezone.utc),
                action_type,
                target,
                moderator=actor,
                reason=self._flatten_fields(fields),
            )
        except ValueError:
            log.warning(
                "Core modlog has no casetype %s, so no case was recorded.", action_type
            )
            return None
        except RuntimeError:
            # Core refuses to log the bot as a target.
            log.debug("Core modlog refused a case targeting the bot.")
            return None

        if case is None:
            # The casetype is registered but disabled in this guild.
            log.debug("Core casetype %s is disabled in guild %s.", action_type, guild.id)
            return None

        return await self._case_ref_from_core_case(
            case,
            action_type=action_type,
            actor=actor,
            target=target,
            fields=fields,
            duration=duration,
        )

    def _case_ref_from_modlog_case(self, case) -> CaseRef:
        """Build a ``CaseRef`` from a live ModLog ``Case`` instance.

        ``actor_label``/``actor_emoji`` come from the case itself, not
        ``action_type`` -- ModLog freezes them in at creation time (see
        ``resolve_actor_display``), so a case keeps the tier the actor held
        then, not whatever they are now.
        """
        return CaseRef(
            action_type=case.action_type.type,
            action_name=case.action_type.name,
            action_color=case.action_type.color,
            action_emoji=case.action_type.emoji,
            case_number=case.case_number,
            actor=case.actor,
            target=case.target,
            fields=case.fields,
            timestamp=case.timestamp,
            duration=case.duration,
            target_label=case.action_type.target_label,
            target_emoji=case.action_type.target_emoji,
            actor_label=case.actor_label,
            actor_emoji=case.actor_emoji,
            source="modlog",
        )

    async def _case_ref_from_core_case(
        self,
        case,
        *,
        action_type: str,
        actor: Member | User | int | None,
        target: Member | User | int,
        fields: List[Dict[str, Any]] | None,
        duration: str | None,
    ) -> CaseRef:
        """Build a ``CaseRef`` from a core-modlog ``Case`` instance.

        Core's own object already carries the target/actor/reason it
        stored, but callers of ``create_case`` know theirs precisely and a
        freshly created case may not have them resolved to full objects yet,
        so the values passed in are preferred over re-reading the case.

        Unlike ModLog, core has no field to freeze a resolved actor tier into
        -- it's Red's own schema, not ours. So the tier is resolved fresh right
        here every time: accurate for the confirmation embed shown immediately
        after an action, but a much later re-read of an old core-fallback case
        (e.g. via :meth:`recent_cases`) reflects the actor's tier *now*, not
        whatever it was back then. This mirrors core's other losses on this
        path (no attachments, no real duration).
        """
        display = self._display(action_type)

        if actor is None:
            actor_label, actor_emoji = "Actor", "🛡️"
        else:
            actor_label, actor_emoji = await resolve_actor_display(
                self.bot,
                actor,
                configured_label=display["actor_label"],
                configured_emoji=display["actor_emoji"],
            )

        created_at = getattr(case, "created_at", None)

        if isinstance(created_at, datetime.datetime):
            timestamp = created_at.timestamp()
        elif isinstance(created_at, (int, float)):
            timestamp = float(created_at)
        else:
            timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp()

        return CaseRef(
            action_type=action_type,
            action_name=display["name"],
            action_color=display["color"],
            action_emoji=display["emoji"],
            case_number=getattr(case, "case_number", None),
            actor=actor,
            target=target,
            fields=fields or [],
            timestamp=timestamp,
            duration=duration,
            target_label=display["target_label"],
            target_emoji=display["target_emoji"],
            actor_label=actor_label,
            actor_emoji=actor_emoji,
            source="core",
        )

    async def recent_cases(self, guild: Guild, *, since: datetime.datetime) -> List[CaseRef]:
        """Every case created in ``guild`` at or after ``since``.

        Reads whichever backend is live. Unlike :meth:`create_case`, this
        always reads core modlog when ModLog is absent, regardless of
        ``core_fallback``: that flag exists to stop a *new* case from becoming
        readable through core's own ungated ``[p]case``/``[p]casesfor``, which
        does not apply to a case that already exists there. A caller reading
        case history through its own mod-gated command adds no visibility
        core did not already have.
        """
        modlog = self.cog_instance
        since_ts = since.timestamp()

        if modlog is not None:
            cases = await modlog.cases_since(guild, since_ts)
            return [self._case_ref_from_modlog_case(case) for case in cases]

        cases = await core_modlog.get_all_cases(guild, self.bot)
        refs = []

        for case in cases:
            created_at = getattr(case, "created_at", None)
            if isinstance(created_at, datetime.datetime):
                timestamp = created_at.timestamp()
            else:
                timestamp = float(created_at or 0)

            if timestamp < since_ts:
                continue

            refs.append(
                await self._case_ref_from_core_case(
                    case,
                    action_type=case.action_type,
                    actor=getattr(case, "moderator", None),
                    target=case.user,
                    # Core only ever held one flattened reason string (see
                    # _flatten_fields), so a past core-fallback case can only be
                    # read back as a single "Reason" field, even if it was
                    # originally written from several -- an accepted lossy
                    # round-trip specific to this fallback path.
                    fields=reason_field(getattr(case, "reason", None)),
                    duration=None,
                )
            )

        return refs

    ### ----------------------------------------------------------------
    ### Events
    ### ----------------------------------------------------------------

    async def log_event(
        self,
        guild: Guild,
        *,
        action_type: str,
        target: Member | User | int | None = None,
        fields: List[Dict[str, Any]],
        actor: Member | User | int | None = None,
        timestamp: float | None = None,
        channel: discord.abc.Messageable | None = None,
    ) -> bool:
        """Post a log entry that is deliberately not a case.

        For things worth an actor's attention that do not belong in a
        member's permanent record. Renders identically to a case minus the case
        number, and needs no backend at all -- it posts straight to the guild's
        modlog channel, so it behaves the same whether ModLog is loaded or not.

        ``channel`` overrides the destination, for a caller that keeps its own
        dedicated channel per event category rather than sharing the single
        guild-wide modlog channel.
        """
        channel = channel or await self._modlog_channel(guild)

        if channel is None:
            return False

        display = self._display(action_type)

        if actor is None:
            actor_label, actor_emoji = "Actor", "🛡️"
        else:
            actor_label, actor_emoji = await resolve_actor_display(
                self.bot,
                actor,
                configured_label=display["actor_label"],
                configured_emoji=display["actor_emoji"],
            )

        embed = build_case_embed(
            action_name=display["name"],
            action_color=display["color"],
            action_emoji=display["emoji"],
            target_label=display["target_label"],
            target=target,
            target_emoji=display["target_emoji"],
            actor_label=actor_label,
            actor=actor,
            actor_emoji=actor_emoji,
            fields=fields,
            timestamp=timestamp or datetime.datetime.now(datetime.timezone.utc).timestamp(),
        )

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not write to the modlog channel in guild %s.", guild.id)
            return False

        return True

    @staticmethod
    async def _modlog_channel(guild: Guild):
        try:
            return await core_modlog.get_modlog_channel(guild)
        except (discord.HTTPException, RuntimeError):
            return None

    ### ----------------------------------------------------------------
    ### Actor feedback
    ### ----------------------------------------------------------------

    async def confirm_action(
        self,
        ctx: Context,
        *,
        action_type: str,
        target: Member | User,
        fields: List[Dict[str, Any]] | None = None,
        duration: str | None = None,
        title: str = "Confirm action",
        timeout: int = 30,
        ephemeral: bool = True,
    ) -> bool:
        """Ask the actor to confirm an action before it is carried out.

        Pure UI -- it needs no backend, only the action's display name, which
        the proxy can always resolve from its own declarations.
        """
        display = self._display(action_type)
        action = f"`{display['name']}`{display['emoji'] or ''}"

        view = ConfirmationView(author=ctx.author, timeout=timeout)
        embed = discord.Embed(title=title, color=discord.Colour.yellow())

        embed.set_footer(text=f"You have {timeout} seconds to confirm.")

        embed.add_field(name="Type:", value=action, inline=True)
        embed.add_field(name="Target:", value=f"`{target.name}`", inline=True)
        embed.add_field(name="Target ID:", value=f"`{target.id}`", inline=True)

        if duration:
            embed.add_field(name="Duration:", value=f"`{duration}`", inline=False)

        for entry in fields or []:
            embed.add_field(
                name=f"{entry['name']}:", value=entry["content"], inline=entry.get("inline", False)
            )

        message = await ctx.send(view=view, embed=embed, ephemeral=ephemeral)

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

    async def send_case_action_summary(
        self,
        ctx: Context,
        case: CaseRef | None,
        *,
        note: str | None = None,
        ephemeral: bool = True,
    ) -> None:
        """Report a completed action back to the actor who carried it out."""

        # A case is not guaranteed: ModLog may be absent, the core fallback may
        # be off or disabled. The action itself still happened, so the actor
        # still needs to hear about it.

        if case is None:
            content = "The action completed, but no modlog case could be created for it."

            if note:
                content = f"{content} {note}"

            await ctx.send(content, ephemeral=ephemeral)
            return

        embed = build_case_embed(
            action_name=case.action_name,
            action_color=case.action_color,
            action_emoji=case.action_emoji,
            case_number=case.case_number,
            target_label=case.target_label,
            actor_label=case.actor_label,
            actor=case.actor,
            actor_emoji=case.actor_emoji,
            target=case.target,
            target_emoji=case.target_emoji,
            fields=case.fields,
            timestamp=case.timestamp,
            duration=case.duration,
        )

        # The note is a one-off aside about this particular delivery (e.g.
        # "transcript not attached"), not part of the case record itself, so it
        # stays in the description rather than becoming a field alongside the
        # case's own.

        if note:
            embed.description = note

        try:
            await ctx.send(embed=embed, ephemeral=ephemeral)
        except discord.HTTPException:
            log.exception("Failed to respond with the summary for case %s.", case.case_number)
