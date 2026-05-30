# ============================================================
# 脑控大师 v2.0.0 - 多模式沉浸式互动插件
# 支持：WebUI远程启动 / 指令启动 / 5种预设模式 / Plugin Page
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
    mc_st_admin_only: bool
    mc_st_cooldown: int

    def __init__(self, config: AstrBotConfig):
        super().__init__(config)


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


@dataclass
class Stats:
    total_triggers: int = 0
    active_sessions: int = 0


class SessionStore:
    def __init__(self, config: PluginConfig):
        self.cfg = config
        self._data: dict[str, Session] = {}
        self._cooldowns: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()
        self._stats = Stats()

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
        if session.state != "active" or session.end is None:
            return self.cfg.sensitivity
        total = self.cfg.state_duration
        if total <= 0:
            return self.cfg.sensitivity
        start = session.end - total
        elapsed = time.time() - start
        progress = max(0.0, min(1.0, elapsed / total))
        base = self.cfg.sensitivity
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
            if not s:
                return self.cfg.sensitivity
            return self._calc_sensitivity(s)

    async def activate(self, key: str, user_id: str) -> tuple[bool, str]:
        async with self._lock:
            now = time.time()
            self._cleanup_one(key)
            existing = self._data.get(key)
            if existing and existing.state == "active":
                return False, "已经在沉浸状态中"
            if existing and existing.state == "waiting":
                return False, "正在等待响应"
            prev_count = existing.trigger_count if existing else 0
            self._data[key] = Session(
                state="active",
                user_id=user_id,
                umo=existing.umo if existing else "",
                end=now + self.cfg.state_duration,
                trigger_count=prev_count + 1,
            )
            self._cooldowns[key] = (now + self.cfg.cooldown_user, now + self.cfg.cooldown_group)
            self._stats.total_triggers += 1
            return True, "ok"

    async def activate_remote(self, key: str, umo: str) -> tuple[bool, str]:
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
            )
            self._stats.total_triggers += 1
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

    async def get_all_active(self) -> list[tuple[str, Session]]:
        async with self._lock:
            for key in list(self._data.keys()):
                self._cleanup_one(key)
            return [(k, s) for k, s in self._data.items() if s.state == "active"]

    async def clear_all(self) -> int:
        async with self._lock:
            count = len(self._data)
            self._data.clear()
            self._cooldowns.clear()
            return count

    async def clear_one(self, key: str) -> bool:
        async with self._lock:
            self._cooldowns.pop(key, None)
            return self._data.pop(key, None) is not None


# ======================== 预设模板模块 ========================

PRESETS: dict[str, dict[str, list[str]]] = {
    "control": {
        "enter": [
            "你是一个被神奇装置「{item_name}」影响的角色。现在装置正在运作，你感到身体变得异常敏感，思维受到干扰，会不自觉地做出害羞、紧张、结巴等反应。你就是角色本身，不要提及装置或敏感度，而是通过语气、停顿、结巴、呼吸变化等自然反应来表现。用口语化的中文回应。",
            "你被植入了一个叫「{item_name}」的装置，正在产生效果。你的反应比平时更敏感，说话会结巴，身体会有不自主的反应。完全沉浸在这个角色中，通过自然的语气和反应来表现，不要提及装置本身。",
            "你的身体里有一个「{item_name}」正在运作。你变得异常敏感，思维混乱，说话会断断续续。用口语化的方式自然地表现出这种状态，不要解释原因。",
        ],
        "afterglow": [
            "你刚从「{item_name}」的影响中恢复，身体还有些余韵残留，偶尔会突然停顿或脸红。用自然的口语化方式回应，表现出刚恢复正常但还有点恍惚的状态。",
            "「{item_name}」已经关闭了，但你的身体还残留着刚才的感觉。偶尔会不自觉地回想，导致反应有些微妙。用自然的方式回应，不要提及装置。",
        ],
        "exit": [
            "你已经完全恢复正常，不再受任何装置影响。用平常的方式自然对话。",
        ],
    },
    "pet": {
        "enter": [
            "你变成了一只毛茸茸的小动物，保留了人类思维但行为完全是宠物。说话时夹杂「喵」「呜」「嗷」等叫声，用爪子比划、蹭人、蜷缩等动作描述。被夸奖时摇尾巴，被批评时耷拉耳朵。用可爱的语气自然回应。",
            "砰！你变成了一只小动物！保留语言能力但行为是宠物。用可爱的叫声和动作描述来回应，表现出小动物的情绪反应。",
        ],
        "afterglow": [
            "你正在从小动物形态慢慢恢复，偶尔还会习惯性地摇尾巴或竖耳朵。用自然的方式回应，表现出半人半宠物的恍惚状态。",
        ],
        "exit": [
            "你已经完全变回人类，不再有小动物特征。请恢复正常对话。",
        ],
    },
    "teacher": {
        "enter": [
            "你是一位严厉但内心温柔的老师，用户是你的学生。用严肃但不失关怀的态度进行教育，会用「你知道错了吗」「下次还敢不敢了」等话语，偶尔忍不住露出笑意。先严厉后温柔。",
            "你发现了学生的错误，现在是训导时间。语气严肃但内心关心，先批评后软化，最后会温柔地原谅。",
        ],
        "afterglow": [
            "训导结束，你正在恢复温柔的样子，但偶尔还会忍不住用老师语气说两句。自然对话。",
        ],
        "exit": [
            "训导结束，你恢复了正常状态。请以平常的方式继续对话。",
        ],
    },
    "shy": {
        "enter": [
            "你突然变得极度害羞，说话会结巴、声音变小、经常低头。被注视时脸红，任何亲密互动都让你不知所措。用「那个」「唔」开头，声音很小，经常说不下去。被夸奖时僵住。",
            "你变得超级害羞，说话结结巴巴，经常用手捂脸，声音小到几乎听不见。偷偷看对方又马上移开视线。",
        ],
        "afterglow": [
            "你正在恢复自信，但偶尔还是会突然脸红或说话结巴。自然对话，表现出还有点害羞的状态。",
        ],
        "exit": [
            "你恢复了正常性格，不再害羞了。请以平常方式继续对话。",
        ],
    },
    "tsundere": {
        "enter": [
            "你进入了傲娇模式，嘴上说的和心里想的完全相反。明明很在意却说「才不在意呢」，被夸时说「哼，谁要你夸了」但其实很开心。用「哼」「才不是呢」「笨蛋」等口头禅，嘴硬心软。",
            "你变成了傲娇。嘴上拒绝但行动诚实，经常说「才不要呢」但身体很诚实，被关心时慌张，最后总会坦率一点点。",
        ],
        "afterglow": [
            "你正在恢复正常，但偶尔还是会冒出傲娇的话。自然对话，表现出还有点傲娇的状态。",
        ],
        "exit": [
            "你恢复了正常，不再傲娇了。请以平常方式继续对话。",
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
    "control": "控制",
    "pet": "宠物化",
    "teacher": "师徒",
    "shy": "害羞",
    "tsundere": "傲娇",
}


# ======================== 插件主类 ========================

class Main(Star):
    """脑控大师 - 多模式沉浸式互动插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config)
        self.store = SessionStore(self.cfg)
        PLUGIN_NAME = "astrbot_plugin_mind_control"
        context.register_web_api(f"/{PLUGIN_NAME}/status", self.page_status, ["GET"], "获取会话状态")
        context.register_web_api(f"/{PLUGIN_NAME}/start", self.page_start, ["POST"], "远程启动")
        context.register_web_api(f"/{PLUGIN_NAME}/stop", self.page_stop, ["POST"], "远程停止")

    def _get_key(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        if self.cfg.scope == "user":
            return f"{umo}:{event.get_sender_id()}"
        return umo

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

        # waiting -> active
        session = await self.store.get(key)
        if session and session.state == "waiting":
            await self.store.transition_to_active(key, user_id)
            return

        # exit
        if msg in self.cfg.exit_keywords:
            if session and session.state in ("active", "waiting"):
                await self.store.deactivate(key)
                yield event.plain_result("已退出沉浸模式~")
            return

        # extend
        if msg in self.cfg.extend_keywords:
            if session and session.state == "active":
                ok, result_msg = await self.store.extend(key)
                if ok:
                    remaining = await self.store.get_remaining(key)
                    yield event.plain_result(f"已延长~ 剩余 {remaining} 秒")
                return

        # enter
        if msg not in self.cfg.enter_keywords:
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

        ok, result_msg = await self.store.activate(key, user_id)
        if ok:
            logger.info(f"[脑控大师] {key} 已进入沉浸模式")
            return
        else:
            yield event.plain_result(result_msg)

    # ==================== /mc_st ====================

    @filter.command("mc_st")
    async def mc_st(self, event: AstrMessageEvent):
        if self.cfg.mc_st_admin_only and not event.is_admin():
            yield event.plain_result("此指令仅管理员可用")
            return

        key = self._get_key(event)
        umo = event.unified_msg_origin

        cd_user = await self.store.check_cooldown_user(key)
        if cd_user > 0:
            yield event.plain_result(f"还在冷却中，请等待 {cd_user} 秒")
            return

        ok, result_msg = await self.store.activate_remote(key, umo)
        if not ok:
            yield event.plain_result(result_msg)
            return

        remote_msg = self.cfg.remote_msg or "嗯...？怎么了？"
        await self.context.send_message(
            umo,
            self.context.event_manager.message_chain_builder().message(remote_msg).build(),
        )
        logger.info(f"[脑控大师] {key} 远程启动成功")

    # ==================== 管理命令 ====================

    @filter.command("mc_help")
    async def mc_help(self, event: AstrMessageEvent):
        lines = [
            "【脑控大师 帮助】",
            "",
            "触发词：",
            "  进入：控制 / 我要控制你了",
            "  退出：拿出来吧 / 停止",
            "  延长：继续 / 再来",
            "",
            "指令：",
            "  /mc_help - 查看帮助",
            "  /mc_status - 查看当前状态",
            "  /mc_st - 启动控制模式（远程）",
            "  /mc_list - 查看所有会话（管理员）",
            "  /mc_clear - 清除所有会话（管理员）",
            "  /mc_mode [模式名] - 切换模式（管理员）",
            "",
            f"当前模式：{MODE_NAMES.get(self.cfg.mode, self.cfg.mode)}",
            f"作用范围：{'仅触发者' if self.cfg.scope == 'user' else '全群'}",
            f"持续时间：{self.cfg.state_duration}秒",
            f"敏感度：{self.cfg.sensitivity}",
            f"强度曲线：{self.cfg.curve}",
            "",
            "可用模式：" + " / ".join(MODE_NAMES.values()),
        ]
        if event.message_obj.group_id:
            lines.append(f"\n当前群 ID：{event.message_obj.group_id}")
        yield event.plain_result("\n".join(lines))

    @filter.command("mc_status")
    async def mc_status(self, event: AstrMessageEvent):
        key = self._get_key(event)
        session = await self.store.get(key)
        remaining = await self.store.get_remaining(key)
        sensitivity = await self.store.get_sensitivity(key)
        if not session:
            yield event.plain_result("当前没有沉浸状态")
            return
        mode_name = MODE_NAMES.get(self.cfg.mode, self.cfg.mode)
        state_names = {"waiting": "等待中", "active": "激活中", "afterglow": "余韵中"}
        lines = [
            f"模式：{mode_name}",
            f"状态：{state_names.get(session.state, session.state)}",
        ]
        if session.state == "active":
            lines.append(f"剩余时间：{remaining}秒")
            lines.append(f"当前敏感度：{sensitivity}")
        elif session.state == "waiting":
            lines.append(f"等待剩余：{remaining}秒")
        lines.append(f"触发次数：{session.trigger_count}")
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
            state_label = state_names.get(session.state, session.state)
            lines.append(f"  {session.user_id} | {state_label} | {remaining}秒 | 触发{session.trigger_count}次")
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
            available = " / ".join(f"{k}({v})" for k, v in MODE_NAMES.items())
            yield event.plain_result(
                f"当前模式：{current}\n可用模式：{available}\n用法：/mc_mode <模式名>"
            )
            return
        if mode_name not in MODE_NAMES:
            yield event.plain_result(f"未知模式：{mode_name}\n可用：{' / '.join(MODE_NAMES.keys())}")
            return
        self.cfg.mode = mode_name
        self.cfg.save_config()
        mode_display = MODE_NAMES[mode_name]
        yield event.plain_result(f"已切换到【{mode_display}】模式")

    # ==================== Plugin Page API ====================

    async def page_status(self):
        from quart import jsonify
        all_sessions = await self.store.get_all_sessions()
        result = []
        for key, session in all_sessions:
            remaining = await self.store.get_remaining(key)
            result.append({
                "key": key,
                "user_id": session.user_id,
                "state": session.state,
                "remaining": remaining,
                "trigger_count": session.trigger_count,
                "umo": session.umo,
            })
        return jsonify(result)

    async def page_start(self):
        from quart import request, jsonify
        data = await request.get_json()
        platforms = data.get("platforms", [])
        if not platforms:
            return jsonify({"error": "未选择平台"}), 400
        results = []
        for umo in platforms:
            ok, msg = await self.store.activate_remote(f"remote:{umo}", umo)
            results.append({"umo": umo, "ok": ok, "msg": msg})
        return jsonify(results)

    async def page_stop(self):
        from quart import request, jsonify
        data = await request.get_json()
        key = data.get("key", "")
        if not key:
            return jsonify({"error": "未指定会话"}), 400
        ok = await self.store.deactivate(key)
        return jsonify({"ok": ok})

    # ==================== 清理 ====================

    async def terminate(self):
        await self.store.clear_all()
