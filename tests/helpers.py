"""Stand-ins that let the cogs be tested without a running bot.

The Config fakes mirror only the surface the cogs actually touch -- ``get_raw``,
``set_raw``, ``get_lock`` and value access. ``FakeConfig`` records every write
path it is given, which is what lets the tests assert that case creation never
loads and rewrites a whole guild group.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any, Dict, List, Tuple


def install_pil_stub() -> None:
    """Make ``verification.captcha`` importable without Pillow.

    Pillow is a real dependency of the verification cog and is installed in CI.
    This keeps the suite runnable in a bare checkout, where the captcha image
    code is never exercised anyway.
    """
    try:
        import PIL  # noqa: F401
    except ImportError:
        stub = types.ModuleType("PIL")
        for name in ("Image", "ImageDraw", "ImageFilter", "ImageFont"):
            setattr(stub, name, types.SimpleNamespace())
        sys.modules["PIL"] = stub


class FakeValue:
    """A single Config value."""

    def __init__(self, data: dict, key: str, default: Any) -> None:
        self.data = data
        self.key = key
        self.default = default
        self._lock = asyncio.Lock()

    def get_lock(self) -> asyncio.Lock:
        return self._lock

    async def __call__(self) -> Any:
        return self.data.get(self.key, self.default)

    async def set(self, value: Any) -> None:
        self.data[self.key] = value


class FakeGroup:
    """A Config group supporting scoped raw access."""

    def __init__(self, data: dict, key: str, writes: List[Tuple[str, ...]]) -> None:
        self.data = data
        self.key = key
        self.writes = writes
        self._lock = asyncio.Lock()

    def get_lock(self) -> asyncio.Lock:
        return self._lock

    async def get_raw(self, *path: Any, default: Any = ...) -> Any:
        node = self.data.setdefault(self.key, {})
        for part in path:
            if str(part) not in node:
                return default if default is not ... else {}
            node = node[str(part)]
        return node

    async def set_raw(self, *path: Any, value: Any) -> None:
        self.writes.append((self.key,) + tuple(str(p) for p in path))
        node = self.data.setdefault(self.key, {})
        for part in path[:-1]:
            node = node.setdefault(str(part), {})
        node[str(path[-1])] = value


class FakeGuildConfig:
    def __init__(self, data: dict, writes: List[Tuple[str, ...]]) -> None:
        self.case_sequence = FakeValue(data, "case_sequence", 1)
        self.cases = FakeGroup(data, "cases", writes)
        self.user_cases = FakeGroup(data, "user_cases", writes)


class FakeConfig:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.writes: List[Tuple[str, ...]] = []
        self._guild = FakeGuildConfig(self.data, self.writes)

    def guild(self, guild: Any) -> FakeGuildConfig:
        return self._guild


class FakeUser:
    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"


class FakeGuild:
    id = 999

    def __init__(self) -> None:
        self.me = FakeUser(1, "ViviBot")

    def get_member(self, member_id: int):
        return None


class FakeMember(FakeUser):
    def __init__(self, user_id: int, name: str, guild: FakeGuild) -> None:
        super().__init__(user_id, name)
        self.guild = guild


class FakeChannel:
    id = 77
    mention = "<#77>"


class FakeBot:
    def __init__(self) -> None:
        self.cogs: Dict[str, Any] = {}
        self.guilds: List[Any] = []

    def get_cog(self, name: str):
        return self.cogs.get(name)

    def get_user(self, user_id: int):
        return None


class FakeMessage:
    id = 555

    class channel:
        id = 444


class RecordingChannel:
    """A modlog channel that captures what was sent to it."""

    def __init__(self) -> None:
        self.sent: List[dict] = []

    async def send(self, **kwargs: Any) -> FakeMessage:
        self.sent.append(kwargs)
        return FakeMessage()


class FakeCoreModlog:
    """Stub of ``redbot.core.modlog``.

    ``register_casetype`` reproduces core's real and surprising behaviour of
    raising when a casetype is re-registered with unchanged values.
    """

    def __init__(self) -> None:
        self.registered: List[str] = []
        self.created: List[str] = []
        self.channel: Any = None
        self.raise_on_duplicate = True
        #: "case" | None | "value_error" | "runtime_error"
        self.create_result: Any = "case"

    async def register_casetype(self, *, name, default_setting, image, case_str):
        if name in self.registered and self.raise_on_duplicate:
            raise RuntimeError("That case type is already registered!")
        self.registered.append(name)
        return object()

    async def create_case(self, bot, guild, created_at, action_type, user, **kwargs):
        self.created.append(action_type)

        if self.create_result == "value_error":
            raise ValueError(f"{action_type} is not a valid action type.")
        if self.create_result == "runtime_error":
            raise RuntimeError("The bot itself can not be the target of a modlog entry.")
        if self.create_result is None:
            return None

        class CoreCase:
            case_number = 42
            created_at = 1700000000

        return CoreCase()

    async def get_modlog_channel(self, guild):
        return self.channel


def make_modlog_cog(bot: FakeBot):
    """A ModLog instance without Config or data-path setup."""
    from modlog.modlog import ModLog

    cog = object.__new__(ModLog)
    cog.bot = bot
    cog.config = FakeConfig()
    cog._action_types = {}
    return cog
