from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement

from .topics import Topics

__red_end_user_data_statement__ = get_end_user_data_statement(file=__file__)


async def setup(bot: Red) -> None:
    await bot.add_cog(Topics(bot))
