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
from typing import Any, Dict, List

import discord
from discord import Member, User


def field(name: str, content: str, *, inline: bool = False) -> Dict[str, Any]:
    """Build one dynamic embed field.

    Plain dict, not a class -- this crosses ``bot.get_cog()`` boundaries (see
    the module docstring), so it must stay a primitive rather than a vendored
    type that would be foreign on the other side.
    """
    return {"name": name, "content": content, "inline": inline}


def reason_field(reason: str | None) -> List[Dict[str, Any]]:
    """The common case of a single "Reason" field, or none at all.

    Keeps callers that only ever had a plain reason string a one-liner:
    ``fields=reason_field(reason)``.
    """
    return [field("Reason", reason)] if reason else []


def build_case_embed(
    *,
    action_name: str,
    action_color: discord.Colour,
    target_label: str,
    target: Member | User | int | None,
    target_emoji: str | None = None,
    fields: List[Dict[str, Any]] | None = None,
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

    ``fields`` are appended last, in the order given -- the caller decides
    what belongs here and in what order, whether that's a single "Reason" (see
    ``reason_field``) or several named pieces of context.
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
            embed.add_field(name=f"{target_label} ID:", value=f"`{target.id}`", inline=False)
            embed.set_thumbnail(url=target.display_avatar.url)
        else:
            label = f"`Unavailable`{target_emoji or ''}" if detailed else f"`{target}`{target_emoji or ''}"
            embed.add_field(name=f"{target_label}:", value=label, inline=False)
            embed.add_field(name=f"{target_label} ID:", value=f"`{target}`", inline=False)

    if detailed:
        embed.add_field(name="Duration:", value=f"`{duration or 'Not specified.'}`⏳", inline=False)
    elif duration:
        embed.add_field(name="Duration:", value=f"`{duration}`⏳", inline=False)

    for entry in fields or []:
        embed.add_field(
            name=f"{entry['name']}:", value=entry["content"], inline=entry.get("inline", False)
        )

    return embed
