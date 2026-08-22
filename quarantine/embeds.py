import discord

from discord import User, Member, Role, CategoryChannel, Color
from discord.abc import GuildChannel

from shared.colors import WARN_COLOR, DANGER_COLOR, INFO_COLOR
from shared.formatting import Formatting


class QuarantineEmbeds:
    @staticmethod
    def settings_quarantine_settings(color: Color, role: Role | None, category: CategoryChannel | None):
        embed = discord.Embed(
            title="Quarantine settings",
            colour=color
        )

        embed.add_field(name="Role", value=role.mention if role else "*not set*", inline=True)

        embed.add_field(
            name="Category", value=category.name if category else "*not set*", inline=True
        )

        return embed

    @staticmethod
    def discussion_channel_member_quarantined(moderator: Member | User, reason: str | None):
        embed = discord.Embed(
            title="You have been quarantined.",
            description="A moderator will be with you shortly.",
            colour=INFO_COLOR,
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(name="Quarantined by", value=moderator.mention)
        embed.add_field(name="Quarantine reason", value=reason or "*No reason provided.*")

    @staticmethod
    def discussion_channel_quarantine_lifted(member: User | Member):
        embed = discord.Embed(
            title= "Member quarantine lifted.",
            description=f"**{member.name}** has had their quarantine lifted and can no longer access this channel.",
            colour=WARN_COLOR,
            timestamp=discord.utils.utcnow()
        )

        return embed

    @staticmethod
    def discussion_channel_deletion_pending():
        embed = discord.Embed(
            title="Channel deletion requested.",
            description="The channel will be nuked from orbit momentarily.",
            colour=DANGER_COLOR,
            timestamp = discord.utils.utcnow()
        )

        return embed

    @staticmethod
    def modlog_member_quarantined(moderator: User | Member, member: User | Member, reason: str | None):
        embed = discord.Embed(
            title="Member quarantined",
            description=f"{member.mention} has been quarantined.",
            colour=WARN_COLOR,
            timestamp=discord.utils.utcnow()
        )

        embed.add_field(
            name="Moderator",
            value=moderator.mention,
        )

        embed.add_field(
            name="Reason",
            value=reason or "*No reason provided.*",
        )

        embed.set_author(name=Formatting.member_name_id(member), icon_url=member.display_avatar.url)

    @staticmethod
    def modlog_discussion_channel_deleted(channel: GuildChannel, *, moderator: User | Member):
        embed = discord.Embed(
            title="Quarantine channel deleted",
            description="A quarantine discussion channel has been deleted.",
            colour=DANGER_COLOR,
            timestamp = discord.utils.utcnow()
        )

        embed.add_field(
            name="Channel Name",
            value=channel.name,
        )

        embed.add_field(
            name="Channel ID",
            value=channel.id,
        )

        embed.add_field(
            name="Moderator",
            value=moderator.mention,
        )