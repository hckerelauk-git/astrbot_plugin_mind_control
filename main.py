# ============================================================
# 脑控大师 v2.6.1 - 多模式沉浸式互动插件
# 支持：/mc_st远程启动 / /控制指定强度 / 5种预设模式
# ============================================================

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig
from astrbot.api import logger

PLUGIN_NAME = "astrbot_plugin_mind_control"


# ======================== 会话状态模块 ========================

@dataclass(slots=True)
class Session:
    state: str
    user_id: str
    umo: str
    start: float | None = None
    end: float | None = None
    exit_ts: float | None = None
    trigger_count: int = 0
    waiting_start: float | None = None
    waiting_timeout: float | None = None
    custom_sensitivity: int | None = None


class SessionStore:
    def __init__(self, config: AstrBotConfig):
        self.cfg = config
        self._data: dict[str, Session] = {}
        self._cooldowns: dict[str, tuple[float, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _copy_session(self, s: Session) -> Session:
        return Session(
            state=s.state,
            user_id=s.user_id,
            umo=s.umo,
            start=s.start,
            end=s.end,
            exit_ts=s.exit_ts,
            trigger_count=s.trigger_count,
            waiting_start=s.waiting_start,
            waiting_timeout=s.waiting_timeout,
            custom_sensitivity=s.custom_sensitivity,
        )

    def _cleanup_one(self, key: str) -> None:
        s = self._data.get(key)
        if not s:
            return
        now = time.time()
        if s.state == "waiting":
            timeout = s.waiting_timeout or self.cfg.get("waiting_timeout", 300)
            if s.waiting_start and now - s.waiting_start > timeout:
                self._data.pop(key, None)
                self._cooldowns.pop(key, None)
                # 不要 pop _locks，锁对象要一直保留避免 key 复用时锁分裂
        elif s.state == "active":
            if s.end is not None and s.end <= now:
                s.state = "afterglow"
                s.exit_ts = now
        elif s.state == "afterglow":
            afterglow = self.cfg.get("afterglow_duration", 30) if self.cfg.get("afterglow_enable", True) else 0
            if s.exit_ts and now - s.exit_ts > afterglow:
                self._data.pop(key, None)
                self._cooldowns.pop(key, None)
                # 同样不删除锁对象

    def _calc_sensitivity(self, session: Session) -> int:
        base = session.custom_sensitivity if session.custom_sensitivity is not None else self.cfg.get("sensitivity", 50)
        if session.state != "active" or session.end is None or session.start is None:
            return base
        total = self.cfg.get("state_duration", 180)
        if total <= 0:
            return base
        elapsed = time.time() - session.start
        progress = max(0.0, min(1.0, elapsed / total))
        curve = self.cfg.get("curve", "flat")
        if curve == "flat":
            return base
        elif curve == "ramp_up":
            return int(base * (0.3 + 0.7 * progress))
        elif curve == "decay":
            return int(base * (1.0 - 0.7 * progress))
        elif curve == "wave":
            wave = (math.sin(progress * math.pi * 2) + 1) / 2
            return int(base * (0.5 + 0.5 * wave))
        return base

    async def get(self, key: str) -> Session | None:
        lock = await self._get_lock(key)
        async with lock:
            self._cleanup_one(key)
            s = self._data.get(key)
            return self._copy_session(s) if s else None

    async def get_sensitivity(self, key: str) -> int:
        lock = await self._get_lock(key)
        async with lock:
            self._cleanup_one(key)
            s = self._data.get(key)
            return self._calc_sensitivity(s) if s else self.cfg.get("sensitivity", 50)

    async def activate(self, key: str, user_id: str, sensitivity: int | None = None) -> tuple[bool, str]:
        lock = await self._get_lock(key)
        async with lock:
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
                start=now,
                end=now + self.cfg.get("state_duration", 180),
                trigger_count=prev_count + 1,
                custom_sensitivity=sensitivity,
            )
            cd_user = self.cfg.get("cooldown_user", 30)
            cd_group = self.cfg.get("cooldown_group", 60)
            self._cooldowns[key] = (now + cd_user, now + cd_group)
            return True, "ok"

    async def activate_remote(self, key: str, umo: str, sensitivity: int | None = None) -> tuple[bool, str]:
        lock = await self._get_lock(key)
        async with lock:
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
                waiting_timeout=self.cfg.get("waiting_timeout", 300),
                custom_sensitivity=sensitivity,
            )
            return True, "ok"

    async def transition_to_active(self, key: str, user_id: str) -> bool:
        lock = await self._get_lock(key)
        async with lock:
            s = self._data.get(key)
            if not s or s.state != "waiting":
                return False

            now = time.time()
            s.state = "active"
            s.user_id = user_id
            if s.start is None:
                s.start = now
            s.end = now + self.cfg.get("state_duration", 180)
            s.waiting_start = None
            cd_user = self.cfg.get("cooldown_user", 30)
            cd_group = self.cfg.get("cooldown_group", 60)
            self._cooldowns[key] = (now + cd_user, now + cd_group)
            return True

    async def deactivate(self, key: str) -> bool:
        lock = await self._get_lock(key)
        async with lock:
            s = self._data.get(key)
            if not s or s.state not in ("active", "waiting"):
                return False
            s.state = "afterglow"
            s.exit_ts = time.time()
            return True

    async def extend(self, key: str) -> tuple[bool, str]:
        lock = await self._get_lock(key)
        async with lock:
            s = self._data.get(key)
            if not s or s.state != "active":
                return False, "当前不在沉浸状态"
            if s.end is not None:
                extend_dur = self.cfg.get("extend_duration", 60)
                s.end += extend_dur
            return True, f"已延长 {self.cfg.get('extend_duration', 60)} 秒"

    async def check_cooldown_user(self, key: str) -> int:
        lock = await self._get_lock(key)
        async with lock:
            cd = self._cooldowns.get(key)
            return max(0, int(cd[0] - time.time())) if cd else 0

    async def check_cooldown_group(self, key: str) -> int:
        lock = await self._get_lock(key)
        async with lock:
            cd = self._cooldowns.get(key)
            return max(0, int(cd[1] - time.time())) if cd else 0

    async def get_remaining(self, key: str) -> int:
        lock = await self._get_lock(key)
        async with lock:
            s = self._data.get(key)
            if not s:
                return 0
            if s.state == "waiting" and s.waiting_start:
                timeout = s.waiting_timeout or self.cfg.get("waiting_timeout", 300)
                return max(0, int(timeout - (time.time() - s.waiting_start)))
            if s.state == "active" and s.end is not None:
                return max(0, int(s.end - time.time()))
            return 0

    async def get_all_sessions(self) -> list[tuple[str, Session]]:
        async with self._global_lock:
            keys = list(self._data.keys())
        result = []
        for key in keys:
            lock = await self._get_lock(key)
            async with lock:
                self._cleanup_one(key)
                s = self._data.get(key)
                if s:
                    result.append((key, self._copy_session(s)))
        return result

    async def clear_all(self) -> int:
        async with self._global_lock:
            keys = list(self._data.keys())
        count = 0
        for key in keys:
            lock = await self._get_lock(key)
            async with lock:
                if self._data.pop(key, None) is not None:
                    self._cooldowns.pop(key, None)
                    count += 1
        async with self._global_lock:
            self._locks.clear()
        return count

    async def set_cooldown(self, key: str, seconds: int) -> None:
        lock = await self._get_lock(key)
        async with lock:
            self._cooldowns[key] = (time.time() + seconds, time.time() + seconds)


# ======================== 预设模板模块 ========================

PRESETS: dict[str, dict[str, list[str]]] = {
    "control": {
        "enter": [
            "你是一个被「{item_name}」遥控的角色，当前敏感度为 {sensitivity}/100。范围 0-100，100 为极限。敏感度越低反应越轻微，越高则身体反应越强烈、越难以控制。请根据当前敏感度自然表现出相应的身体反应和说话方式，不要提及装置或敏感度数值。用口语化中文回应。",
            "你的身体里有一个「{item_name}」正在运作，敏感度 {sensitivity}/100。范围 0-100，100 为极限。敏感度越低反应越轻微，越高则越容易失去控制、反应越激烈。用口语化方式自然表现，不解释原因。",
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


def get_templates(mode: str, item_name: str, sensitivity: int, custom_presets: list[dict] | None = None) -> dict[str, str]:
    if custom_presets:
        for p in custom_presets:
            if p.get("name") == mode:
                enter = p.get("enter", "") or PRESETS.get("control", {}).get("enter", [""])[0]
                afterglow = p.get("afterglow", "") or PRESETS.get("control", {}).get("afterglow", [""])[0]
                exit_t = p.get("exit", "") or PRESETS.get("control", {}).get("exit", [""])[0]
                enter = enter.replace("{item_name}", item_name).replace("{sensitivity}", str(sensitivity))
                afterglow = afterglow.replace("{item_name}", item_name).replace("{sensitivity}", str(sensitivity))
                exit_t = exit_t.replace("{item_name}", item_name).replace("{sensitivity}", str(sensitivity))
                return {"enter": enter, "afterglow": afterglow, "exit": exit_t}

    preset = PRESETS.get(mode, PRESETS["control"])
    enter_list = preset.get("enter", PRESETS["control"]["enter"])
    afterglow_list = preset.get("afterglow", PRESETS["control"]["afterglow"])
    exit_list = preset.get("exit", PRESETS["control"]["exit"])

    enter = random.choice(enter_list).replace("{item_name}", item_name).replace("{sensitivity}", str(sensitivity))
    afterglow = random.choice(afterglow_list).replace("{item_name}", item_name).replace("{sensitivity}", str(sensitivity))
    exit_t = random.choice(exit_list).replace("{item_name}", item_name).replace("{sensitivity}", str(sensitivity))
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
        self.config = config
        self.store = SessionStore(config)

        context.register_web_api(
            f"/{PLUGIN_NAME}/current",
            self.page_get_current,
            ["GET"],
            "获取当前模式和提示词",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/presets",
            self.page_get_presets,
            ["GET"],
            "获取所有预设",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/preset/add",
            self.page_add_preset,
            ["POST"],
            "添加自定义预设",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/preset/delete",
            self.page_delete_preset,
            ["POST"],
            "删除自定义预设",
        )

    def _get_key(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        scope = self.config.get("scope", "user")
        return f"{umo}:{event.get_sender_id()}" if scope == "user" else umo

    # ==================== LLM 钩子 ====================

    @staticmethod
    def _preview(text: str, limit: int = 80) -> str:
        one_line = " ".join(str(text or "").split())
        if len(one_line) <= limit:
            return one_line
        return one_line[: limit - 3] + "..."

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        key = self._get_key(event)
        session = await self.store.get(key)
        if not session:
            logger.debug("[脑控大师] LLM钩子跳过 key=%s 无会话", key)
            return
        sensitivity = await self.store.get_sensitivity(key)
        mode = self.config.get("mode", "control")
        item_name = self.config.get("item_name", "特殊装置")
        custom_presets = self.config.get("custom_presets", [])
        templates = get_templates(mode, item_name, sensitivity, custom_presets)
        if session.state == "active":
            template = templates["enter"]
            phase = "enter"
        elif session.state == "afterglow":
            template = templates["afterglow"]
            phase = "afterglow"
        else:
            logger.info(
                "[脑控大师] LLM钩子跳过 key=%s state=%s（非 active/afterglow）",
                key,
                session.state,
            )
            return
        if not str(template).strip():
            logger.warning(
                "[脑控大师] LLM钩子未注入 key=%s mode=%s phase=%s 模板为空",
                key,
                mode,
                phase,
            )
            return
        if hasattr(req, "system_prompt") and isinstance(req.system_prompt, str):
            req.system_prompt += f"\n\n{template}"
        else:
            logger.warning(
                "[脑控大师] LLM钩子跳过 key=%s 当前 provider 不支持 system_prompt 注入",
                key,
            )
            return
        logger.info(
            "[脑控大师] 已注入提示词 key=%s state=%s mode=%s 敏感度=%s phase=%s 长度=%s 预览=%s",
            key,
            session.state,
            mode,
            sensitivity,
            phase,
            len(template),
            self._preview(template),
        )

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

        admin_only = self.config.get("admin_only_mode", False)
        if admin_only and not event.is_admin():
            yield event.plain_result("仅管理员可用")
            return

        if not event.is_private_chat():
            group_id = event.message_obj.group_id
            whitelist = self.config.get("group_whitelist", [])
            if whitelist and group_id not in whitelist:
                yield event.plain_result("该群不在白名单中")
                return

        cd_user = await self.store.check_cooldown_user(key)
        if cd_user > 0:
            yield event.plain_result(f"还在冷却中，请等待 {cd_user} 秒")
            return

        if self.config.get("scope", "user") == "session":
            cd_group = await self.store.check_cooldown_group(key)
            if cd_group > 0:
                yield event.plain_result(f"群聊冷却中，请等待 {cd_group} 秒")
                return

        ok, result_msg = await self.store.activate(key, user_id, sensitivity)
        if ok:
            eff = sensitivity if sensitivity is not None else self.config.get("sensitivity", 50)
            logger.info(f"[脑控大师] {key} /控制指令激活，敏感度={eff}")
            yield event.plain_result("")
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

        admin_only = self.config.get("admin_only_mode", False)

        session = await self.store.get(key)

        # 非触发词消息：直接放行，不做任何拦截
        # 触发词才走下面的 keyword 分支
        exit_kws = self.config.get("exit_keywords", ["拿出来吧", "停止"])
        extend_kws = self.config.get("extend_keywords", ["继续", "再来", "more"])
        enter_kws = self.config.get("enter_keywords", ["我要控制你了"])

        if msg not in exit_kws and msg not in extend_kws and msg not in enter_kws:
            if session and session.state in ("active", "afterglow"):
                logger.info(
                    "[脑控大师] 沉浸中放行消息 key=%s state=%s msg=%s",
                    key,
                    session.state,
                    self._preview(msg, 40),
                )
            return

        # 以下仅处理触发词
        if not event.is_private_chat():
            group_id = event.message_obj.group_id
            whitelist = self.config.get("group_whitelist", [])
            if whitelist and group_id not in whitelist:
                yield event.plain_result("该群不在白名单中")
                return
        if admin_only and not event.is_admin():
            yield event.plain_result("仅管理员可用")
            return

        if msg in exit_kws:
            if session and session.state in ("active", "waiting"):
                await self.store.deactivate(key)
                logger.info("[脑控大师] 退出沉浸 key=%s -> afterglow，本条消息继续走 LLM", key)
                session = await self.store.get(key)
            else:
                logger.debug("[脑控大师] 退出词忽略 key=%s 无有效会话", key)

        elif msg in extend_kws:
            if session and session.state == "active":
                ok, _ = await self.store.extend(key)
                if ok:
                    remaining = await self.store.get_remaining(key)
                    yield event.plain_result(f"已延长~ 剩余 {remaining} 秒")
                    logger.info("[脑控大师] 延长沉浸 key=%s 剩余=%ss", key, remaining)
            return

        elif msg in enter_kws:
            cd_user = await self.store.check_cooldown_user(key)
            if cd_user > 0:
                yield event.plain_result(f"还在冷却中，请等待 {cd_user} 秒")
                return

            ok, result_msg = await self.store.activate(key, user_id)
            if ok:
                logger.info("[脑控大师] 关键词进入沉浸 key=%s，本条消息继续走 LLM 注入", key)
                session = await self.store.get(key)
            else:
                yield event.plain_result(result_msg)
                return

        if session and session.state in ("active", "afterglow"):
            logger.info(
                "[脑控大师] 沉浸中放行消息 key=%s state=%s msg=%s",
                key,
                session.state,
                self._preview(msg, 40),
            )

    # ==================== /mc_st 远程启动 ====================

    async def _remote_start(self, event: AstrMessageEvent):
        """远程启动，可选指定敏感度 /mc_st 或 /mc_st 50"""
        if self.config.get("td_st_admin_only", False) and not event.is_admin():
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

        await self.store.set_cooldown(key, self.config.get("td_st_cooldown", 30))
        eff = sensitivity if sensitivity is not None else self.config.get("sensitivity", 50)
        logger.info(f"[脑控大师] {key} 远程启动成功，敏感度={eff}")
        yield event.plain_result(self.config.get("remote_msg") or "已进入远程模式，等待用户消息触发 LLM~")

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
        mode = self.config.get("mode", "control")
        lines = [
            "【脑控大师 v2.6.1】", "",
            "触发词：", "  进入：控制 / 我要控制你了", "  退出：拿出来吧 / 停止", "  延长：继续 / 再来", "",
            "指令：", "  /mc_help - 帮助", "  /mc_status - 状态", "  /mc_st - 远程启动（可指定敏感度）",
            "  /mc_list - 所有会话（管理员）", "  /mc_clear - 清除会话（管理员）",
            "  /mc_mode [模式名] - 切换模式（管理员）", "",
            "强度控制：", "  /控制 或 /控制 50 -> 进入控制模式（默认/指定敏感度）", "",
            f"当前模式：{MODE_NAMES.get(mode, mode)}",
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
        mode = self.config.get("mode", "control")
        mode_name = MODE_NAMES.get(mode, mode)
        state_names = {"waiting": "等待", "active": "激活", "afterglow": "余韵"}
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
        state_names = {"waiting": "等待", "active": "激活", "afterglow": "余韵"}
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
            current = MODE_NAMES.get(self.config.get("mode", "control"), self.config.get("mode", "control"))
            yield event.plain_result(f"当前：{current}\n可用：{' / '.join(MODE_NAMES.values())}")
            return
        if mode_name not in MODE_NAMES:
            yield event.plain_result(f"未知模式，可用：{' / '.join(MODE_NAMES.keys())}")
            return
        self.config["mode"] = mode_name
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()
        yield event.plain_result(f"已切换到【{MODE_NAMES[mode_name]}】模式")

    # ==================== 清理 ====================

    async def terminate(self):
        await self.store.clear_all()

    # ==================== Pages API ====================

    async def page_get_current(self):
        from quart import jsonify
        mode = self.config.get("mode", "control")
        item_name = self.config.get("item_name", "特殊装置")
        custom_presets = self.config.get("custom_presets", [])
        templates = get_templates(mode, item_name, self.config.get("sensitivity", 50), custom_presets)
        return jsonify({
            "mode": mode,
            "prompts": templates
        })

    async def page_get_presets(self):
        from quart import jsonify
        custom_presets = self.config.get("custom_presets", [])
        return jsonify({
            "builtin": list(PRESETS.keys()),
            "custom": custom_presets
        })

    async def page_add_preset(self):
        from quart import request, jsonify
        data = await request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400

        custom_presets = self.config.get("custom_presets", [])
        custom_presets = [p for p in custom_presets if p.get("name") != name]
        custom_presets.append({
            "name": name,
            "enter": data.get("enter", ""),
            "afterglow": data.get("afterglow", ""),
            "exit": data.get("exit", "")
        })
        self.config["custom_presets"] = custom_presets
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()
        return jsonify({"success": True})

    async def page_delete_preset(self):
        from quart import request, jsonify
        data = await request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400

        custom_presets = self.config.get("custom_presets", [])
        custom_presets = [p for p in custom_presets if p.get("name") != name]
        self.config["custom_presets"] = custom_presets
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()
        return jsonify({"success": True})
