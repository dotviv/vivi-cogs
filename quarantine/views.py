import discord

UNQUARANTINE_PANEL_CUSTOM_ID = "vivi_quarantine:del_channel"

class QuarantineLiftedChannelPanel(discord.ui.View):
    """The panel that shows at the bottom of a quarantine chat after the member is no longer quarantined."""

    def __init__(self, cog: "Quarantine") -> None:
        super().__init__(timeout=None)  # timeout=None + custom_id == survives restarts
        self.cog = cog

    @discord.ui.button(
        label="Delete Channel",
        style=discord.ButtonStyle.danger,
        emoji="\N{NEGATIVE SQUARED CROSS MARK}",
        custom_id=UNQUARANTINE_PANEL_CUSTOM_ID,
    )
    async def delete_channel(
            self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_panel_click_delete_channel(interaction)