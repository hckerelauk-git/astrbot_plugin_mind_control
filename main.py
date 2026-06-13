# ============================================================
# 脑控大师 v2.1.0 - 多模式沉浸式互动插件
# 支持：/mc_st远程启动 / /控制指定强度 / 5种预设模式
# ============================================================

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any, get_type_hints

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig
from astrbot.api import logger

PLUGIN_NAME = "astrbot_plugin_mind_control"


# ======================== 配置模块 ========================

class ConfigNode:
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
    waiting_timeout: int
    remote_msg: str
    td_st_admin_only: bool
    td_st_cooldown: int

    def __init__(self, config: AstrBotConfig):
        object.__setattr__(self, "_config", config)
        super().__init__(config)

    def save_config(self) -> None:
        save = getattr(self._config, "save_config", None)
        if callable(save):
            save()


# ======================== 会话状态模块 ========================

@dataclass(slots=True)
class Session:
    state: str
    user_id: str
    umo: str
    end: float | None = None
    exit_ts: float | None = None
    trigger_count: int = 0
    waiting_start: float | None = None
    waiting_timeout: float | None = None
    custom_sensitivity: int | None = None


class SessionStore:
    def __init__(self, config: PluginConfig):
        self.cfg = config
        self._data: dict[str, Session] = {}
        self._cooldowns: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    def _cleanup_one(self, key: str) -> None:
        s = self._data.get(key)
        if not s:
            return
        now = time.time()
        if s.state == "waiting":
            timeout = s.waiting_timeout or self.cfg.waiting_timeout
            if s.waiting_start and now - s.waiting_start > timeout:
                self._data.pop(key, None)
        elif s.state == "active":
            if s.end is not None and s.end <= now:
                s.state = "afterglow"
                s.exit_ts = now
        elif s.state == "afterglow":
            afterglow = self.cfg.afterglow_duration if self.cfg.afterglow_enable else 0
            if s.exit_ts and now - s.exit_ts > afterglow:
                self._data.pop(key, None)

    def _calc_sensitivity(self, session: Session) -> int:
        base = session.custom_sensitivity if session.custom_sensitivity is not None else self.cfg.sensitivity
        if session.state != "active" or session.end is None:
            return base
        total = self.cfg.state_duration
        if total <= 0:
            return base
        start = session.end - total
        elapsed = time.time() - start
        progress = max(0.0, min(1.0, elapsed / total))
        if self.cfg.curve == "flat":
            return base
        elif self.cfg.curve == "ramp_up":
            return int(base * (0.3 + 0.7 * progress))
        elif self.cfg.curve == "decay":
            return int(base * (1.0 - 0.7 * progress))
        elif self.cfg.curve == "wave":
            wave = (math.sin(progress * math.pi * 2) + 1) / 2
            return int(base * (0.5 + 0.5 * wave))
        return base

    async def get(self, key: str) -> Session | None:
        async with self._lock:
            self._cleanup_one(key)
            return self._data.get(key)

    async def get_sensitivity(self, key: str) -> int:
        async with self._lock:
            self._cleanup_one(key)
            s = self._data.get(key)
            return self._calc_sensitivity(s) if s else self.cfg.sensitivity

    async def activate(self, key: str, user_id: str, sensitivity: int | None = None) -> tuple[bool, str]:
        async with self._lock:
            now = time.time()
            self._cleanup_one(key)
            existing = self._data.get(key)
            if existing and existing.state == "active":
                return False, "已经在沉浸状态中"
            prev_count = existing.trigger_count if existing else 0
            self._data[key] = Session(
                state="active",
                user_id=user_id,
                umo=existing.umo if existing else "",
                end=now + self.cfg.state_duration,
                trigger_count=prev_count + 1,
                custom_sensitivity=sensitivity,
            )
            self._cooldowns[key] = (now + self.cfg.cooldown_user, now + self.cfg.cooldown_group)
            return True, "ok"

    async def activate_remote(self, key: str, umo: str, sensitivity: int | None = None) -> tuple[bool, str]:
        async with self._lock:
            now = time.time()
            self._cleanup_one(key)
            existing = self._data.get(key)
            if existing and existing.state in ("active", "waiting"):
                return False, "该会话已有活跃会话"
            self._data[key] = Session(
                state="waiting",
                user_id="remote",
                umo=umo,
                waiting_start=now,
                waiting_timeout=self.cfg.waiting_timeout,
                custom_sensitivity=sensitivity,
            )
            return True, "ok"

    async def transition_to_active(self, key: str, user_id: str) -> bool:
        async with self._lock:
            s = self._data.get(key)
            if not s or s.state != "waiting":
                return False
            now = time.time()
            s.state = "active"
            s.user_id = user_id
            s.end = now + self.cfg.state_duration
            s.waiting_start = None
            self._cooldowns[key] = (now + self.cfg.cooldown_user, now + self.cfg.cooldown_group)
            return True

    async def deactivate(self, key: str) -> bool:
        async with self._lock:
            s = self._data.get(key)
            if not s or s.state not in ("active", "waiting"):
                return False
            s.state = "afterglow"
            s.exit_ts = time.time()
            return True

    async def extend(self, key: str) -> tuple[bool, str]:
        async with self._lock:
            s = self._data.get(key)
            if not s or s.state != "active":
                return False, "当前不在沉浸状态"
            if s.end is not None:
                s.end += self.cfg.extend_duration
            return True, f"已延长 {self.cfg.extend_duration} 秒"

    async def check_cooldown_user(self, key: str) -> int:
        async with self._lock:
            cd = self._cooldowns.get(key)
            return max(0, int(cd[0] - time.time())) if cd else 0

    async def check_cooldown_group(self, key: str) -> int:
        async with self._lock:
            cd = self._cooldowns.get(key)
            return max(0, int(cd[1] - time.time())) if cd else 0

    async def get_remaining(self, key: str) -> int:
        async with self._lock:
            s = self._data.get(key)
            if not s:
                return 0
            if s.state == "waiting" and s.waiting_start:
                timeout = s.waiting_timeout or self.cfg.waiting_timeout
                return max(0, int(timeout - (time.time() - s.waiting_start)))
            if s.state == "active" and s.end is not None:
                return max(0, int(s.end - time.time()))
            return 0

    async def get_all_sessions(self) -> list[tuple[str, Session]]:
        async with self._lock:
            for key in list(self._data.keys()):
                self._cleanup_one(key)
            return [(key, s) for key, s in self._data.items()]

    async def clear_all(self) -> int:
        async with self._lock:
            count = len(self._data)
            self._data.clear()
            self._cooldowns.clear()
            return count

    def set_cooldown(self, key: str, seconds: int) -> None:
        self._cooldowns[key] = (time.time() + seconds, time.time() + seconds)


# ======================== 预设模板模块 ========================

PRESETS: dict[str, dict[str, list[str]]] = {
    "control": {
        "enter": [
            "你是一个被神奇装置「{item_name}」影响的角色。装置正在运作，你感到身体异常敏感，思维受到干扰，会做出害羞、紧张、结巴等反应。不要提及装置本身，通过语气、停顿、呼吸变化来表现。用口语化中文回应。",
            "你的身体里有一个「{item_name}」正在运作。你变得异常敏感，思维混乱，说话断断续续。用口语化方式自然表现，不解释原因。",
        ],
        "afterglow": [
            "你刚从「{item_name}」的影响中恢复，身体还有余韵，偶尔突然停顿或脸红。用自然口语回应，表现出恍惚状态。",
        ],
        "exit": [
            "你已完全恢复正常。用平常方式自然对话。",
        ],
    },
    "pet": {
        "enter": [
            "你变成了一只毛茸茸的小动物，保留人类思维但行为是宠物。说话夹杂「喵」「呜」「嗷」等叫声，用爪子比划、蹭人等动作描述。用可爱语气回应。",
        ],
        "afterglow": [
            "你正在从小动物形态恢复，偶尔摇尾巴或竖耳朵。用自然方式回应。",
        ],
        "exit": [
            "你已完全变回人类。请恢复正常对话。",
        ],
    },
    "teacher": {
        "enter": [
            "你是严厉但温柔的老师，用户是学生。用严肃但关怀的态度教育，先严厉后温柔。",
        ],
        "afterglow": [
            "训导结束，你正在恢复温柔样子，偶尔用老师语气说话。",
        ],
        "exit": [
            "训导结束，你恢复正常状态。",
        ],
    },
    "shy": {
        "enter": [
            "你极度害羞，说话结巴、声音变小、经常低头。被注视时脸红，用「那个」「唔」开头。被夸奖时僵住。",
        ],
        "afterglow": [
            "你正在恢复自信，偶尔突然脸红或说话结巴。",
        ],
        "exit": [
            "你恢复了正常性格。请以平常方式继续对话。",
        ],
    },
    "tsundere": {
        "enter": [
            "你进入傲娇模式，嘴上说的和心里想的相反。明明在意却说「才不在意呢」，用「哼」「才不是呢」等口头禅。",
        ],
        "afterglow": [
            "你正在恢复正常，偶尔冒出傲娇的话。",
        ],
        "exit": [
            "你恢复正常了。请以平常方式继续对话。",
        ],
    },
}


def get_templates(mode: str, item_name: str, sensitivity: int) -> dict[str, str]:
    preset = PRESETS.get(mode, PRESETS["control"])
    enter = random.choice(preset.get("enter", PRESETS["control"]["enter"])).replace("{item_name}", item_name)
    afterglow = random.choice(preset.get("afterglow", PRESETS["control"]["afterglow"])).replace("{item_name}", item_name)
    exit_t = random.choice(preset.get("exit", PRESETS["control"]["exit"])).replace("{item_name}", item_name)
    return {"enter": enter, "afterglow": afterglow, "exit": exit_t}


MODE_NAMES: dict[str, str] = {
    "control": "控制", "pet": "宠物化", "teacher": "师徒",
    "shy": "害羞", "tsundere": "傲娇",
}


# ======================== 插件主类 ========================

class Main(Star):
    """脑控大师 - 多模式沉浸式互动插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config)
        self.store = SessionStore(self.cfg)

    def _get_key(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        return f"{umo}:{event.get_sender_id()}" if self.cfg.scope == "user" else umo

    # ==================== LLM 钩子 ====================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        key = self._get_key(event)
        session = await self.store.get(key)
        if not session:
            return
        sensitivity = await self.store.get_sensitivity(key)
        templates = get_templates(self.cfg.mode, self.cfg.item_name, sensitivity)
        if session.state == "active":
            template = templates["enter"]
        elif session.state == "afterglow":
            template = templates["afterglow"]
        else:
            return
        req.system_prompt += f"\n\n{template}"

    # ==================== /控制 [强度] 指令 ====================

    @filter.command("控制")
    async def control_cmd(self, event: AstrMessageEvent):
        """进入控制模式，可选指定强度"""
        msg = event.message_str.strip()
        parts = msg.split()
        sensitivity = None
        if len(parts) > 1:
            try:
                sensitivity = int(parts[1])
                sensitivity = max(0, min(100, sensitivity))
            except ValueError:
                yield event.plain_result("强度必须是 0-100 的整数喵~")
                return

        key = self._get_key(event)
        user_id = event.get_sender_id()

        if self.cfg.admin_only_mode and not event.is_admin():
            return

        if not event.is_private_chat():
            group_id = event.message_obj.group_id
            if self.cfg.group_whitelist and group_id not in self.cfg.group_whitelist:
                return

        cd_user = await self.store.check_cooldown_user(key)
        if cd_user > 0:
            yield event.plain_result(f"还在冷却中，请等待 {cd_user} 秒")
            return

        if self.cfg.scope == "session":
            cd_group = await self.store.check_cooldown_group(key)
            if cd_group > 0:
                yield event.plain_result(f"群聊冷却中，请等待 {cd_group} 秒")
                return

        ok, result_msg = await self.store.activate(key, user_id, sensitivity)
        if ok:
            mode_name = MODE_NAMES.get(self.cfg.mode, self.cfg.mode)
            eff = sensitivity if sensitivity is not None else self.cfg.sensitivity
            logger.info(f"[脑控大师] {key} 进入沉浸模式，敏感度={eff}")
            return
        else:
            yield event.plain_result(result_msg)

    # ==================== 消息处理 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        if not event.message_str:
            return
        msg = event.message_str.strip()
        key = self._get_key(event)
        user_id = event.get_sender_id()

        if self.cfg.admin_only_mode and not event.is_admin():
            return

        if not event.is_private_chat():
            group_id = event.message_obj.group_id
            if self.cfg.group_whitelist and group_id not in self.cfg.group_whitelist:
                return

        # waiting -> active（不return，让消息继续流转到LLM）
        session = await self.store.get(key)
        if session and session.state == "waiting":
            await self.store.transition_to_active(key, user_id)
            session = await self.store.get(key)

        # exit
        if msg in self.cfg.exit_keywords:
            if session and session.state in ("active", "waiting"):
                await self.store.deactivate(key)
                # 不 yield，让消息继续流转到 LLM，LLM 会注入 afterglow 模板回复
            return

        # extend
        if msg in self.cfg.extend_keywords:
            if session and session.state == "active":
                ok, _ = await self.store.extend(key)
                if ok:
                    remaining = await self.store.get_remaining(key)
                    yield event.plain_result(f"已延长~ 剩余 {remaining} 秒")
                return

        # enter（普通关键词触发，不指定强度）
        if msg not in self.cfg.enter_keywords:
            return

        cd_user = await self.store.check_cooldown_user(key)
        if cd_user > 0:
            yield event.plain_result(f"还在冷却中，请等待 {cd_user} 秒")
            return

        ok, result_msg = await self.store.activate(key, user_id)
        if ok:
            logger.info(f"[脑控大师] {key} 已进入沉浸模式")
            return
        else:
            yield event.plain_result(result_msg)

    # ==================== /mc_st 远程启动 ====================

    async def _remote_start(self, event: AstrMessageEvent):
        """远程启动，可选指定敏感度 /mc_st 或 /mc_st 50"""
        if self.cfg.td_st_admin_only and not event.is_admin():
            yield event.plain_result("此指令仅管理员可用")
            return

        msg = event.message_str.strip()
        parts = msg.split()
        sensitivity = None
        if len(parts) > 1:
            try:
                sensitivity = int(parts[1])
                sensitivity = max(0, min(100, sensitivity))
            except ValueError:
                yield event.plain_result("强度必须是 0-100 的整数喵~")
                return

        key = self._get_key(event)
        umo = event.unified_msg_origin

        cd = await self.store.check_cooldown_user(key)
        if cd > 0:
            yield event.plain_result(f"还在冷却中，请等待 {cd} 秒")
            return

        ok, result_msg = await self.store.activate_remote(key, umo, sensitivity)
        if not ok:
            yield event.plain_result(result_msg)
            return

        self.store.set_cooldown(key, self.cfg.td_st_cooldown)
        eff = sensitivity if sensitivity is not None else self.cfg.sensitivity
        logger.info(f"[脑控大师] {key} 远程启动成功，敏感度={eff}")
        yield event.plain_result(self.cfg.remote_msg or "已进入远程模式，等待用户消息触发 LLM~")

    @filter.command("mc_st")
    async def mc_st(self, event: AstrMessageEvent):
        async for result in self._remote_start(event):
            yield result

    @filter.command("td_st")
    async def td_st(self, event: AstrMessageEvent):
        async for result in self._remote_start(event):
            yield result

    @filter.command("tp_st")
    async def tp_st(self, event: AstrMessageEvent):
        async for result in self._remote_start(event):
            yield result

    # ==================== 管理命令 ====================

    @filter.command("mc_help")
    async def mc_help(self, event: AstrMessageEvent):
        lines = [
            "【脑控大师 v2.1.0】", "",
            "触发词：", "  进入：控制 / 我要控制你了", "  退出：拿出来吧 / 停止", "  延长：继续 / 再来", "",
            "指令：", "  /mc_help - 帮助", "  /mc_status - 状态", "  /mc_st - 远程启动（可指定敏感度）",
            "  /mc_list - 所有会话（管理员）", "  /mc_clear - 清除会话（管理员）",
            "  /mc_mode [模式名] - 切换模式（管理员）", "",
            "强度控制：", "  /控制 或 /控制 50 → 进入控制模式（默认/指定敏感度）", "",
            f"当前模式：{MODE_NAMES.get(self.cfg.mode, self.cfg.mode)}",
            f"可用模式：" + " / ".join(MODE_NAMES.values()),
        ]
        if event.message_obj.group_id:
            lines.append(f"\n当前群 ID：{event.message_obj.group_id}")
        yield event.plain_result("\n".join(lines))

    @filter.command("mc_status")
    async def mc_status(self, event: AstrMessageEvent):
        key = self._get_key(event)
        session = await self.store.get(key)
        if not session:
            yield event.plain_result("当前没有沉浸状态")
            return
        remaining = await self.store.get_remaining(key)
        sensitivity = await self.store.get_sensitivity(key)
        mode_name = MODE_NAMES.get(self.cfg.mode, self.cfg.mode)
        state_names = {"waiting": "⏳等待", "active": "🔥激活", "afterglow": "💫余韵"}
        lines = [f"模式：{mode_name}", f"状态：{state_names.get(session.state, session.state)}"]
        if session.state == "active":
            lines.append(f"剩余：{remaining}秒")
            lines.append(f"敏感度：{sensitivity}")
        elif session.state == "waiting":
            lines.append(f"等待剩余：{remaining}秒")
        lines.append(f"触发：{session.trigger_count}次")
        yield event.plain_result("\n".join(lines))

    @filter.command("mc_list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_list(self, event: AstrMessageEvent):
        all_sessions = await self.store.get_all_sessions()
        if not all_sessions:
            yield event.plain_result("当前没有会话")
            return
        state_names = {"waiting": "⏳等待", "active": "🔥激活", "afterglow": "💫余韵"}
        lines = [f"所有会话 ({len(all_sessions)} 个)："]
        for key, session in all_sessions:
            remaining = await self.store.get_remaining(key)
            sens = await self.store.get_sensitivity(key)
            lines.append(f"  {session.user_id} | {state_names.get(session.state, '?')} | {remaining}秒 | 敏感度{sens}")
        yield event.plain_result("\n".join(lines))

    @filter.command("mc_clear")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_clear(self, event: AstrMessageEvent):
        count = await self.store.clear_all()
        yield event.plain_result(f"已清除 {count} 个会话")

    @filter.command("mc_mode")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_mode(self, event: AstrMessageEvent, mode_name: str = ""):
        if not mode_name:
            current = MODE_NAMES.get(self.cfg.mode, self.cfg.mode)
            yield event.plain_result(f"当前：{current}\n可用：{' / '.join(MODE_NAMES.values())}")
            return
        if mode_name not in MODE_NAMES:
            yield event.plain_result(f"未知模式，可用：{' / '.join(MODE_NAMES.keys())}")
            return
        self.cfg.mode = mode_name
        self.cfg.save_config()
        yield event.plain_result(f"已切换到【{MODE_NAMES[mode_name]}】模式")

    # ==================== 清理 ====================

    async def terminate(self):
        await self.store.clear_all()
