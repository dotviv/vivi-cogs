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
from importlib import import_module
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

    async def __call__(self) -> dict:
        return dict(self.data.get(self.key, {}))

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
        self.actor_cases = FakeGroup(data, "actor_cases", writes)
        self.log_channels = FakeGroup(data, "log_channels", writes)


class FakeConfig:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.writes: List[Tuple[str, ...]] = []
        self._guild = FakeGuildConfig(self.data, self.writes)

    def guild(self, guild: Any) -> FakeGuildConfig:
        return self._guild


class FakeUser:
    def __init__(self, user_id: int, name: str, *, bot: bool = False) -> None:
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"
        #: Mirrors discord.py's real attribute -- resolve_actor_display checks
        #: this first so an automated action never reads as a human admin.
        self.bot = bot


class FakeGuild:
    id = 999

    def __init__(self, *, owner_id: int = 0) -> None:
        self.me = FakeUser(1, "ViviBot", bot=True)
        #: channel_id -> channel object, for tests exercising log-channel routing.
        self.channels: Dict[int, Any] = {}
        #: 0 by default -- distinct from any real test member's id, so nobody
        #: accidentally resolves as owner unless a test sets this explicitly.
        self.owner_id = owner_id

    def get_member(self, member_id: int):
        return None

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)


class FakeMember(FakeUser):
    def __init__(self, user_id: int, name: str, guild: FakeGuild, *, bot: bool = False) -> None:
        super().__init__(user_id, name, bot=bot)
        self.guild = guild


class FakeChannel:
    id = 77
    mention = "<#77>"


class FakeBot:
    def __init__(self) -> None:
        self.cogs: Dict[str, Any] = {}
        self.guilds: List[Any] = []
        #: IDs a test has opted into a tier by adding to the matching set --
        #: empty by default, so nobody is an owner/admin/mod unless a test says so.
        self.owner_ids: set[int] = set()
        self.admin_ids: set[int] = set()
        self.mod_ids: set[int] = set()

    def get_cog(self, name: str):
        return self.cogs.get(name)

    def get_user(self, user_id: int):
        return None

    async def is_owner(self, user) -> bool:
        return getattr(user, "id", user) in self.owner_ids

    async def is_admin(self, user) -> bool:
        return getattr(user, "id", user) in self.admin_ids

    async def is_mod(self, user) -> bool:
        return getattr(user, "id", user) in self.mod_ids


class FakeMessage:
    id = 555

    class channel:
        id = 444


class RecordingChannel:
    """A modlog channel that captures what was sent to it."""

    def __init__(self, id: int = 0) -> None:
        self.id = id
        self.mention = f"<#{id}>"
        self.sent: List[dict] = []

    async def send(self, **kwargs: Any) -> FakeMessage:
        self.sent.append(kwargs)
        return FakeMessage()

    def permissions_for(self, member: Any) -> types.SimpleNamespace:
        return types.SimpleNamespace(send_messages=True, embed_links=True)


class FakeCoreCase:
    """Stands in for a real core-modlog ``Case``, as returned by ``get_all_cases``."""

    def __init__(self, *, case_number, created_at, action_type, user, moderator, reason):
        self.case_number = case_number
        self.created_at = created_at
        self.action_type = action_type
        self.user = user
        self.moderator = moderator
        self.reason = reason


class FakeCoreModlog:
    """Stub of ``redbot.core.modlog``.

    ``register_casetype`` reproduces core's real and surprising behaviour of
    raising when a casetype is re-registered with unchanged values.
    """

    def __init__(self) -> None:
        self.registered: List[str] = []
        self.created: List[str] = []
        self.cases: List[FakeCoreCase] = []
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

        case = FakeCoreCase(
            case_number=42,
            created_at=1700000000,
            action_type=action_type,
            user=user,
            moderator=kwargs.get("moderator"),
            reason=kwargs.get("reason"),
        )
        self.cases.append(case)
        return case

    async def get_modlog_channel(self, guild):
        return self.channel

    async def get_all_cases(self, guild, bot):
        return list(self.cases)


def make_modlog_cog(bot: FakeBot):
    """A ModLog instance without Config or data-path setup."""
    from modlog.modlog import ModLog

    cog = object.__new__(ModLog)
    cog.bot = bot
    cog.config = FakeConfig()
    cog._action_types = {}
    return cog


async def _stub_is_admin_or_superior(bot, obj) -> bool:
    return await bot.is_owner(obj) or await bot.is_admin(obj)


async def _stub_is_mod_or_superior(bot, obj) -> bool:
    return await bot.is_owner(obj) or await bot.is_mod(obj)


#: Every cog that vendors common/actor_tiers.py, by import path.
_VENDORED_COGS = ("audit", "moderation", "modlog", "quarantine", "topics", "verification")


def install_actor_tier_stubs() -> None:
    """Swap Red's real ``is_admin_or_superior``/``is_mod_or_superior`` for stubs
    that accept ``FakeMember``/``FakeUser``.

    Red's versions do a strict ``isinstance(obj, discord.Member)`` check that no
    plain test double can satisfy without subclassing a real discord.py type.
    The stubs check ``FakeBot.is_owner``/``is_admin``/``is_mod`` instead, which
    tests configure via ``FakeBot.owner_ids``/``admin_ids``/``mod_ids``.

    Each cog carries its own vendored copy of ``actor_tiers.py`` -- these are
    distinct module objects even though the source is identical, so every one
    needs patching. Importing ``verification``'s copy pulls in the real
    ``verification`` package first (its ``__init__.py`` imports the cog module,
    which imports the Pillow-dependent captcha code), so the PIL stub must be
    installed before that import happens.
    """
    install_pil_stub()

    modules = [import_module("common.actor_tiers")]
    modules.extend(import_module(f"{cog}._common.actor_tiers") for cog in _VENDORED_COGS)

    for module in modules:
        module.is_admin_or_superior = _stub_is_admin_or_superior
        module.is_mod_or_superior = _stub_is_mod_or_superior


install_actor_tier_stubs()
