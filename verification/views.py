"""Discord UI components for the verification flow.

These stay deliberately thin: every component hands straight back to the cog so
the decision-making lives in one place rather than being spread across widgets.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .verification import Verification

log = logging.getLogger("red.vivi-cogs.verification")

# Must stay stable forever. discord.py keys persistent button handlers by
# (component type, custom_id), so changing this orphans every panel already
# posted -- their buttons would keep rendering but nothing would answer them.
PANEL_CUSTOM_ID = "vivi_verification:start"

# Discord interaction tokens stop working after roughly 15 minutes, so an
# ephemeral prompt cannot outlive that however long the guild's timeout is set.
MAX_PROMPT_TIMEOUT = 900


class VerificationPanel(discord.ui.View):
    """The permanent embed's Verify button.

    A single registered instance serves every guild, so this must never hold
    guild state -- the guild is resolved from the interaction each time.
    """

    def __init__(self, cog: "Verification") -> None:
        super().__init__(timeout=None)  # timeout=None + custom_id == survives restarts
        self.cog = cog

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="\N{WHITE HEAVY CHECK MARK}",
        custom_id=PANEL_CUSTOM_ID,
    )
    async def start(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.handle_panel_click(interaction)


class CaptchaPrompt(discord.ui.View):
    """Attached to the ephemeral captcha; opens the code entry form.

    No ``interaction_check`` is needed -- an ephemeral message is only visible
    and clickable by the person it was sent to.
    """

    def __init__(self, cog: "Verification", code: str, timeout: int) -> None:
        super().__init__(timeout=min(timeout, MAX_PROMPT_TIMEOUT))
        self.cog = cog
        self.code = code

    @discord.ui.button(
        label="Enter Code",
        style=discord.ButtonStyle.primary,
        emoji="\N{KEYBOARD}\N{VARIATION SELECTOR-16}",
    )
    async def enter_code(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(CodeModal(self.cog, self.code))


class CodeModal(discord.ui.Modal):
    """The popup form. Modals accept text inputs only -- an image cannot go in
    here, which is why the captcha is sent as a message beforehand."""

    def __init__(self, cog: "Verification", code: str) -> None:
        super().__init__(title="Enter your verification code", timeout=MAX_PROMPT_TIMEOUT)
        self.cog = cog
        # The code this prompt was issued with. Compared against the stored code
        # so a stale prompt can be rejected without burning an attempt.
        self.issued_code = code
        self.code_input = discord.ui.TextInput(
            label="Verification code",
            placeholder="The characters shown in the image",
            min_length=len(code),
            # Slack for stray whitespace, which the cog strips. Pinning
            # max_length exactly to the code length would reject a trailing
            # space outright, with no explanation the member could act on.
            max_length=len(code) + 2,
            required=True,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_code_submit(
            interaction, self.issued_code, self.code_input.value
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("Verification modal failed", exc_info=error)
        message = "Something went wrong on my end. Please try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
