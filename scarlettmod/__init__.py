from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement

__red_end_user_data_statement__ = get_end_user_data_statement(file=__file__)

from modlog.modlog import ModLog
from .moderation import Moderation
from .quarantine import Quarantine

async def setup(bot: Red) -> None:
    await bot.add_cog(ModLog(bot))
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(Quarantine(bot))
