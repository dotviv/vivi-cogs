import datetime
from abc import abstractmethod, ABC
from enum import Enum

import discord
from discord import Member, User, colour
from redbot.core.commands import Context


class ConfirmationView(discord.ui.View):
    def __init__(self, author: User | Member, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.author = author
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(f"Only {self.author.mention} can confirm this action.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True

        await interaction.response.defer()

        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False

        await interaction.response.defer()

        self.stop()

class PageAction(Enum):
    PREVIOUS = -1
    STOP = 0
    NEXT = 1

class Page:
    action: PageAction
    message: discord.Message
    embed: discord.Embed

    def __init__(self, action: PageAction, message: discord.Message, embed: discord.Embed):
        self.action = action
        self.message = message
        self.embed = embed

class PageEmbedProvider(ABC):
    @abstractmethod
    async def setup(self) -> None:
        pass

    @abstractmethod
    async def provide(self, page: int) -> discord.Embed:
        pass

    @abstractmethod
    async def pages(self) -> int:
        pass

class PaginationView(discord.ui.View):
    def __init__(self, *, author: User | Member, previous_enabled: bool, next_enabled: bool, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.author = author
        self.previous_enabled = previous_enabled
        self.next_enabled = next_enabled
        self.value : PageAction | None = None
        self.previous_page.disabled = not previous_enabled
        self.next_page.disabled = not next_enabled

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(f"Only {self.author.mention} can use this control.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.primary)
    async def previous_page(self: "PaginationView", interaction: discord.Interaction, button: discord.ui.Button):
        self.value = PageAction.PREVIOUS

        await interaction.response.defer()

        self.stop()

    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.primary)
    async def stop_paging(self: "PaginationView", interaction: discord.Interaction, button: discord.ui.Button):
        self.value = PageAction.STOP

        await interaction.response.defer()

        self.stop()

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.primary)
    async def next_page(self: "PaginationView", interaction: discord.Interaction, button: discord.ui.Button):
        self.value = PageAction.NEXT

        await interaction.response.defer()

        self.stop()

class Interactions:
    @staticmethod
    async def confirm(ctx: Context, *, title: str | None, message: str, message_cancelled: str, message_confirmed: str, timeout: int = 30, ephemeral: bool = False) -> bool:
        title = title or "Confirmation required"
        view = ConfirmationView(author=ctx.author, timeout=timeout)
        embed = discord.Embed(title=title, description=message, color=colour.Colour.yellow())

        embed.set_footer(text=f"You have {timeout} seconds to confirm.")

        message = await ctx.send(
            view=view,
            embed=embed,
            ephemeral=ephemeral
        )

        await view.wait()

        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        if view.value is None:
            embed.description = message_cancelled
            embed.set_footer(text="Timed out")
            embed.colour = colour.Colour.red()
            await message.edit(embed=embed, view=None)
            return False

        if not view.value:
            embed.description = message_cancelled
            embed.set_footer(text="Cancelled")
            embed.colour = colour.Colour.dark_grey()
            await message.edit(embed=embed, view=None)
            return False

        embed.description = message_confirmed
        embed.set_footer(text="Confirmed")
        embed.colour = colour.Colour.green()
        await message.edit(embed=embed, view=None)
        return True

    @staticmethod
    async def page(ctx: Context, *, provider: PageEmbedProvider, timeout: int = 30, ephemeral: bool = False) -> None:
        """
        Displays a paginated embed provided by the supplied provider.

        The embed provider will be called per page request so long as that page has not yet been
        requested, as subsequent page re-requests will automatically be cached.

        The available pages are defined by the providers pages parameter, it is assumed that at least one
        page is available.

        If the timeout is reached, pagination will cease and this method will return.
        """

        await provider.setup()

        page = 1
        pages = await provider.pages()
        message = None
        view = None
        page_embeds = {}

        while True:
            next_page = page + 1 if page < pages else pages
            previous_page = page - 1 if page > 1 else 1
            has_previous_page = previous_page != page
            has_next_page = next_page != page

            view = PaginationView(
                author=ctx.author,
                previous_enabled=has_previous_page,
                next_enabled=has_next_page,
                timeout=timeout)

            if not page in page_embeds:
                page_embeds[page] = await provider.provide(page)
                page_embeds[page].set_footer(text=f"Page: {page} of {pages} | Paging will timeout after {timeout} seconds.")

            if not message:
                message = await ctx.send(
                    view=view,
                    embed=page_embeds[page],
                    ephemeral=ephemeral
                )
            else:
                await message.edit(view=view, embed=page_embeds[page])

            await view.wait()

            if view.value is None:
                page_embeds[page].set_footer(text=f"Page: {page} of {pages} | Paging has expired.")
                page_embeds[page].color = discord.Colour.dark_grey()
                await message.edit(embed=page_embeds[page], view=None)
                return

            if view.value == PageAction.STOP:
                page_embeds[page].set_footer(text=f"Page: {page} of {pages} | Paging has ended.")
                page_embeds[page].color = discord.Colour.dark_grey()
                await message.edit(embed=page_embeds[page], view=None)
                return

            if view.value == PageAction.NEXT:
                page += 1

            if view.value == PageAction.PREVIOUS:
                page -= 1
