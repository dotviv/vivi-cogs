import logging

import discord
from redbot.core import commands, Config, modlog

log = logging.getLogger("red.vivi-cogs.shared.ModLog")

class ModLog:

    @staticmethod
    async def send(
        guild: discord.Guild,
        *,
        title: str,
        description: str,
        colour: discord.Colour,
    ) -> None:
        embed = discord.Embed(
            title=title, description=description, colour=colour, timestamp=discord.utils.utcnow()
        )

        await ModLog.send_embed(guild=guild, embed=embed)

    @staticmethod
    async def send_embed(
            guild: discord.Guild,
            *,
            embed: discord.Embed):
        channel = await modlog.get_modlog_channel(guild)

        if channel is None:
            return

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not write to the mod log channel in guild %s.", guild.id)