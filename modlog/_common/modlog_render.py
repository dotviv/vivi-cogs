# ---------------------------------------------------------------------------
# GENERATED FILE -- DO NOT EDIT.
#
# Vendored from common/modlog_render.py by tools/sync_common.py.
# Edit the source file and re-run the script; CI rejects drift.
# ---------------------------------------------------------------------------

"""The one embed builder for everything this repo logs.

Both the ModLog cog (posting a case to the channel) and consuming cogs
(reporting an action back to the actor, or logging a non-case event) render
through here, so a verification pass and a ban read as siblings in the channel
rather than each carrying its own house style.

Takes primitives rather than any ``ActionType``/``Case`` object: every cog
carries its own vendored copy of this module, so a class defined in one cog is
an unrelated type in another. Only strings, colours and discord.py objects
cross a cog boundary.
"""

from __future__ import annotations

import datetime

import discord
from discord import Member, User


def build_case_embed(
    *,
    action_name: str,
    action_color: discord.Colour,
    target_label: str,
    target: Member | User | int | None,
    target_emoji: str | None = None,
    reason: str | None = None,
    timestamp: float,
    action_emoji: str | None = None,
    actor_label: str,
    actor: Member | User | int | None = None,
    actor_emoji: str | None = None,
    duration: str | None = None,
    case_number: int | None = None,
    detailed: bool = False,
) -> discord.Embed:
    """Render one log entry.

    ``case_number`` is optional so that events which are deliberately not cases
    still render in the same shape, minus the case field. ``target`` is
    optional too, for a global or actor-only action with no single member
    it happened to -- the Target field is omitted entirely rather than shown
    as unavailable, since there was never one to begin with.

    ``detailed`` adds the raw IDs and always shows a duration; it is what
    ``[p]case`` uses. The compact form is what gets posted to the channel.

    Reason is always the final field. ``[p]reason`` edits a posted case by
    index from the end, so nothing may be appended after it.
    """
    embed = discord.Embed(colour=action_color)
    embed.timestamp = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)

    if case_number is not None:
        embed.add_field(name="Case:", value=f"`{case_number}`✅", inline=True)

    embed.add_field(name="Type:", value=f"`{action_name}`{action_emoji or ''}", inline=True)

    if actor is not None:
        if isinstance(actor, (Member, User)):
            embed.add_field(name=f"{actor_label}:", value=f"`{actor.name}`{actor_emoji or ''}", inline=not detailed)
            if detailed:
                embed.add_field(name=f"{actor_label} ID:", value=f"`{actor.id}`", inline=False)
        else:
            label = f"`Unavailable`{actor_emoji or ''}" if detailed else f"`{actor}`{actor_emoji or ''}"
            embed.add_field(name=f"{actor_label}:", value=label, inline=not detailed)
            if detailed:
                embed.add_field(name=f"{actor_label} ID:", value=f"`{actor}`", inline=False)

    if target is not None:
        if isinstance(target, (Member, User)):
            embed.add_field(name=f"{target_label}:", value=f"`{target.name}`{target_emoji or ''}", inline=False)
            if detailed:
                embed.add_field(name="Target ID:", value=f"`{target.id}`", inline=False)
            embed.set_thumbnail(url=target.display_avatar.url)
        else:
            label = f"`Unavailable`{target_emoji or ''}" if detailed else f"`{target}`{target_emoji or ''}"
            embed.add_field(name=f"{target_label}:", value=label, inline=False)
            if detailed:
                embed.add_field(name=f"{target_label} ID:", value=f"`{target}`", inline=False)

    if detailed:
        embed.add_field(name="Duration:", value=f"`{duration or 'Not specified.'}`⏳", inline=False)
    elif duration:
        embed.add_field(name="Duration:", value=f"`{duration}`⏳", inline=False)

    if reason:
        embed.add_field(name="Reason:", value=reason, inline=False)

    return embed
