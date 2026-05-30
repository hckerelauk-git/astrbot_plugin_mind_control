from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, get_type_hints

from astrbot.api import logger
from astrbot.api import AstrBotConfig


class ConfigNode:
    """配置节点：dict → 强类型属性访问"""

    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}

    @classmethod
    def _schema(cls) -> dict[str, type]:
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    def __init__(self, data: MutableMapping[str, Any]):
        object.__setattr__(self, "_data", data)
        for key in self._schema():
            if key in data:
                continue
            logger.warning(f"[config:{self.__class__.__name__}] 缺少字段: {key}")

    def __getattr__(self, key: str) -> Any:
        if key in self._schema():
            return self._data.get(key)
        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in self._schema():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)


class PluginConfig(ConfigNode):
    mode: str
    scope: str
    enter_keywords: list[str]
    exit_keywords: list[str]
    extend_keywords: list[str]
    state_duration: int
    extend_duration: int
    cooldown_user: int
    cooldown_group: int
    sensitivity: int
    curve: str
    afterglow_enable: bool
    afterglow_duration: int
    item_name: str
    group_whitelist: list[str]
    admin_only_mode: bool

    def __init__(self, config: AstrBotConfig):
        super().__init__(config)
