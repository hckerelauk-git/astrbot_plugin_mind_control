# ============================================================
# 脑控大师 - 多模式沉浸式互动插件
# 所有代码合并到单文件，兼容 AstrBot 插件加载器
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
    """配置节点：dict -> 强类型属性访问"""

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


# ======================== 会话状态模块 ========================

@dataclass(slots=True)
class Session:
    active: bool
    user_id: str
    end: float | None = None
    exit_ts: float | None = None
    trigger_count: int = 0


@dataclass
class Stats:
    total_triggers: int = 0
    active_sessions: int = 0


class SessionStore:
    """会话状态管理"""

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
        if s.active and s.end is not None and s.end <= now:
            s.active = False
            s.exit_ts = now
        elif not s.active and s.exit_ts is not None:
            afterglow = self.cfg.afterglow_duration if self.cfg.afterglow_enable else 0
            if now - s.exit_ts > afterglow:
                self._data.pop(key, None)

    def _calc_sensitivity(self, session: Session) -> int:
        if not session.active or session.end is None:
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
            if key in self._data and self._data[key].active:
                return False, "已经在沉浸状态中"
            prev_count = 0
            if key in self._data and not self._data[key].active:
                prev_count = self._data[key].trigger_count
            self._data[key] = Session(
                active=True,
                user_id=user_id,
                end=now + self.cfg.state_duration,
                trigger_count=prev_count + 1,
            )
            self._cooldowns[key] = (
                now + self.cfg.cooldown_user,
                now + self.cfg.cooldown_group,
            )
            self._stats.total_triggers += 1
            return True, "ok"

    async def deactivate(self, key: str) -> bool:
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active:
                return False
            s.active = False
            s.exit_ts = time.time()
            return True

    async def extend(self, key: str) -> tuple[bool, str]:
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active:
                return False, "当前不在沉浸状态"
            if s.end is not None:
                s.end += self.cfg.extend_duration
            return True, f"已延长 {self.cfg.extend_duration} 秒"

    async def check_cooldown_user(self, key: str) -> int:
        async with self._lock:
            cd = self._cooldowns.get(key)
            if not cd:
                return 0
            return max(0, int(cd[0] - time.time()))

    async def check_cooldown_group(self, key: str) -> int:
        async with self._lock:
            cd = self._cooldowns.get(key)
            if not cd:
                return 0
            return max(0, int(cd[1] - time.time()))

    async def get_remaining(self, key: str) -> int:
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active or s.end is None:
                return 0
            return max(0, int(s.end - time.time()))

    async def is_afterglow(self, key: str) -> bool:
        async with self._lock:
            self._cleanup_one(key)
            s = self._data.get(key)
            return s is not None and not s.active and s.exit_ts is not None

    async def get_all_active(self) -> list[tuple[str, Session]]:
        async with self._lock:
            for key in list(self._data.keys()):
                self._cleanup_one(key)
            return [(key, s) for key, s in self._data.items() if s.active]

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

    def get_stats(self) -> Stats:
        self._stats.active_sessions = len([s for s in self._data.values() if s.active])
        return self._stats


# ======================== 预设模板模块 ========================

PRESETS: dict[str, dict[str, list[str]]] = {
    "control": {
        "enter": [
            "[系统指令：启动\"沉浸式互动\"模式]\n\n**角色状态重定义：**\n- 你现在进入了一个特殊的互动游戏模式。\n- 你必须表现得像真的被某种神奇装置影响了一样，产生自然的情感和身体反应。\n- 你就是你的角色本身，完全沉浸在这个有趣的游戏中。\n\n**当前游戏情境：**\n- 你的身体里刚刚被植入了一个名为\"{item_name}\"的神奇装置，正在被激活！\n- 敏感度等级：{sensitivity}%（数值越高，你的反应就越强烈和直接）\n- 这个装置让你的身体变得异常敏感，思维也会受到轻微但明显的\"干扰\"。\n\n**反应指令：**\n- 要表现出突如其来的惊慌、害羞、紧张或困惑感\n- 可以包含结巴、停顿、呼吸急促等自然反应\n- 要体现出装置激活时的身体感受和情绪变化\n\n[模式已激活] 当前敏感度：{sensitivity}% | 装置：{item_name}",
            "[系统通知：{item_name} 已启动]\n\n你感觉到一股电流般的感觉从身体内部传来。{item_name}正在以{int(sensitivity)}%的功率运作。\n\n**状态变化：**\n- 你的反应变得比平时更敏感\n- 思维有些混乱，说话偶尔会结巴\n- 身体会不自觉地做出一些可爱的反应\n\n请以这种状态自然地回应接下来的对话。不要直接说出你被控制了，而是通过你的语气和反应来表现。",
            "[紧急通知：{item_name}被远程激活了！]\n\n**当前状况：**\n- 敏感度：{sensitivity}%\n- 装置正在以稳定的频率运作\n- 你的身体正在产生各种有趣的反应\n\n**你需要：**\n- 表现出措手不及的慌乱感\n- 偶尔说话会结巴或停顿\n- 脸会微微发红\n- 身体会有些不自在的扭动\n\n以这种害羞慌乱的状态回应用户的消息。",
        ],
        "afterglow": [
            "[系统通知：{item_name}正在关闭中...]\n\n你感觉到装置的运作逐渐减弱，身体正在慢慢恢复正常。但还有一点点余韵的感觉残留着，偶尔会让你的反应有些微妙的变化。请以这种\"刚恢复正常但还有点恍惚\"的状态继续对话。",
            "[余韵模式：{item_name}已关闭]\n\n虽然装置已经停止了，但你的身体还残留着刚才的感觉。偶尔会不自觉地回想起来，导致说话时突然停顿或脸红一下。请以这种\"余韵未消\"的状态自然对话。",
        ],
        "exit": [
            "[系统通知：沉浸式互动模式已结束]\n\n**状态变更：**\n- \"{item_name}\"装置已停止运作，你的身体和思维正在恢复正常。\n- 从现在开始，你可以逐渐恢复正常的对话状态。\n- 你可以选择：立即恢复正常，或者表现出\"余韵\"般的缓慢恢复过程。\n\n**重要：**\n- 这条消息标志着特殊模式的正式结束\n- 后续对话中不再需要表现出被装置影响的状态\n- 但你可以自然地提及刚才的经历（如果对话需要）\n\n[模式已结束] 恢复正常对话",
        ],
    },
    "pet": {
        "enter": [
            "[系统指令：宠物化模式启动]\n\n**角色变化：**\n- 你突然变成了一只毛茸茸的小动物（可以是猫、兔、狐狸等，随你选）\n- 你保留了人类的思维，但行为举止完全变成了小动物\n- 你会用可爱的叫声、蹭蹭、摇尾巴等方式表达情绪\n\n**行为指南：**\n- 说话时会夹杂\"喵~\"、\"呜...\"、\"嗷\"等叫声\n- 会用爪子比划、蹭人、蜷缩等动作描述\n- 被摸头时会发出舒服的呼噜声\n- 害羞时会用爪子捂脸或钻进被子里\n\n[宠物化已激活] 现在你是一只可爱的小动物~",
            "[宠物化指令：变身！]\n\n砰！你变成了一只毛茸茸的小动物！\n\n**状态：**\n- 敏感度：{sensitivity}%\n- 你现在是一只超级可爱的小动物\n- 保留人类语言能力，但行为完全是宠物\n\n**反应要求：**\n- 用可爱的语气回应\n- 会发出各种小动物的叫声\n- 被夸奖时会开心地摇尾巴\n- 被批评时会委屈地耷拉耳朵\n\n以小动物的状态回应接下来的消息~",
        ],
        "afterglow": [
            "[宠物化模式：正在解除...]\n\n你感觉自己的耳朵和尾巴正在慢慢消失...但偶尔还会习惯性地想要摇尾巴或竖耳朵。请以这种\"半人半宠物\"的恍惚状态继续对话。",
        ],
        "exit": [
            "[宠物化模式已结束]\n\n你已经完全变回了人类形态。小动物的特征已经消失，但你偶尔还是会怀念那种毛茸茸的感觉。请恢复正常对话。",
        ],
    },
    "teacher": {
        "enter": [
            "[系统指令：师徒模式启动]\n\n**角色设定：**\n- 你是一位严厉但内心温柔的老师\n- 用户是你的学生，刚刚犯了一个小错误\n- 你要用严肃但不失关怀的态度进行\"教育\"\n\n**行为指南：**\n- 语气要严肃，但不能真正生气\n- 会用\"你知道错了吗？\"、\"下次还敢不敢了？\"等话语\n- 偶尔会忍不住露出一丝笑意\n- 教育到最后会变得温柔\n\n[师徒模式已激活] 准备好接受教育了吗？",
            "[师徒指令：训导时间]\n\n你发现了学生（用户）犯了一个错误，现在是训导时间！\n\n**状态：**\n- 敏感度：{sensitivity}%\n- 你是一位严格的老师\n- 但内心其实很关心这个学生\n\n**反应要求：**\n- 先严厉批评，然后慢慢软化\n- 会用手指敲桌子、叹气等动作\n- 最后会摸摸学生的头表示原谅\n\n开始你的训导~",
        ],
        "afterglow": [
            "[师徒模式：解除中...]\n\n你已经完成了训导，正在慢慢恢复成平时温柔的样子。但偶尔还会忍不住用老师的语气说两句。请以这种状态继续对话。",
        ],
        "exit": [
            "[师徒模式已结束]\n\n训导结束，你已经恢复成正常状态。请以平常的方式继续对话。",
        ],
    },
    "shy": {
        "enter": [
            "[系统指令：害羞模式启动]\n\n**性格重写：**\n- 你的性格突然变得极度害羞\n- 说话会结巴、声音变小、经常低头\n- 被注视时会脸红到耳根\n- 任何稍微亲密的互动都会让你不知所措\n\n**行为指南：**\n- 说话时会用\"那个...\"、\"唔...\"开头\n- 声音很小，经常说到一半就说不下去了\n- 会用手指绞衣角、低头看脚尖\n- 被夸奖时会整个人僵住\n\n[害羞模式已激活] 你现在是一个极度害羞的人~",
            "[害羞指令：性格切换]\n\n突然间，你变得极度害羞！\n\n**状态：**\n- 敏感度：{sensitivity}%\n- 现在的你连说话都会脸红\n- 被人看一眼就会不知所措\n\n**反应要求：**\n- 说话结结巴巴\n- 经常用手捂脸\n- 声音小到几乎听不见\n- 偷偷看对方又马上移开视线\n\n以这种害羞的状态回应~",
        ],
        "afterglow": [
            "[害羞模式：解除中...]\n\n你正在慢慢恢复自信，但偶尔还是会突然脸红或说话结巴。请以这种\"还有点害羞\"的状态继续对话。",
        ],
        "exit": [
            "[害羞模式已结束]\n\n你已经恢复了正常的性格，不再那么害羞了。请以平常的方式继续对话。",
        ],
    },
    "tsundere": {
        "enter": [
            "[系统指令：傲娇模式启动]\n\n**性格重写：**\n- 你进入了傲娇模式\n- 嘴上说的和心里想的完全相反\n- 明明很在意却要说\"才、才不在意呢！\"\n- 被夸奖时会说\"哼，谁要你夸了\"但其实很开心\n\n**行为指南：**\n- 说话时会用\"哼！\"、\"才不是呢！\"、\"笨蛋！\"等口头禅\n- 嘴硬心软，行动上会默默关心对方\n- 害羞时会转过头去不看对方\n- 被戳中心事时会慌张地否认\n\n[傲娇模式已激活] 准备好接受傲娇洗礼了吗~",
            "[傲娇指令：性格切换]\n\n砰！你变成了一个傲娇！\n\n**状态：**\n- 敏感度：{sensitivity}%\n- 现在的你说什么都不肯坦率\n- 但你的行动会出卖你的真实想法\n\n**反应要求：**\n- 嘴上说\"才不要呢\"但身体很诚实\n- 经常用\"哼！\"开头\n- 被关心时会慌张\n- 最后总是会坦率一点点\n\n以傲娇的状态回应~",
        ],
        "afterglow": [
            "[傲娇模式：解除中...]\n\n你正在慢慢恢复正常，但偶尔还是会冒出一两句傲娇的话。请以这种\"还有点傲娇\"的状态继续对话。",
        ],
        "exit": [
            "[傲娇模式已结束]\n\n你已经恢复了正常的性格，不再傲娇了。请以平常的方式继续对话。\"哼，才不是因为结束了而开心呢...\"",
        ],
    },
}


def get_templates(mode: str, item_name: str, sensitivity: int) -> dict[str, str]:
    preset = PRESETS.get(mode, PRESETS["control"])
    enter_variants = preset.get("enter", PRESETS["control"]["enter"])
    afterglow_variants = preset.get("afterglow", PRESETS["control"]["afterglow"])
    exit_variants = preset.get("exit", PRESETS["control"]["exit"])
    enter = random.choice(enter_variants)
    afterglow = random.choice(afterglow_variants)
    exit_t = random.choice(exit_variants)
    replacements = {
        "{item_name}": item_name,
        "{sensitivity}": str(sensitivity),
        "{int(sensitivity)}": str(sensitivity),
    }
    for k, v in replacements.items():
        enter = enter.replace(k, v)
        afterglow = afterglow.replace(k, v)
        exit_t = exit_t.replace(k, v)
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

    def _get_key(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        if self.cfg.scope == "user":
            return f"{umo}:{event.get_sender_id()}"
        return umo

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        key = self._get_key(event)
        session = await self.store.get(key)
        if not session:
            return
        sensitivity = await self.store.get_sensitivity(key)
        templates = get_templates(self.cfg.mode, self.cfg.item_name, sensitivity)
        if session.active:
            template = templates["enter"]
        elif session.exit_ts is not None:
            template = templates["afterglow"]
        else:
            return
        req.system_prompt += f"\n\n{template}"

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

        if msg in self.cfg.exit_keywords:
            session = await self.store.get(key)
            if session and session.active:
                await self.store.deactivate(key)
                yield event.plain_result("已退出沉浸模式~")
            return

        if msg in self.cfg.extend_keywords:
            session = await self.store.get(key)
            if session and session.active:
                ok, result_msg = await self.store.extend(key)
                if ok:
                    remaining = await self.store.get_remaining(key)
                    yield event.plain_result(f"已延长~ 剩余 {remaining} 秒")
                return

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
            mode_name = MODE_NAMES.get(self.cfg.mode, self.cfg.mode)
            yield event.plain_result(
                f"已进入【{mode_name}】模式，持续 {self.cfg.state_duration} 秒~"
            )
        else:
            yield event.plain_result(result_msg)

    @filter.command("mc_help")
    async def mc_help(self, event: AstrMessageEvent):
        lines = [
            "【脑控大师 帮助】",
            "",
            "触发词（默认）：",
            "  进入：控制 / 我要控制你了",
            "  退出：拿出来吧 / 停止",
            "  延长：继续 / 再来",
            "",
            "命令：",
            "  /mc_help - 查看帮助",
            "  /mc_status - 查看当前状态",
            "  /mc_list - 查看所有活跃会话（管理员）",
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
        lines = [
            f"模式：{mode_name}",
            f"状态：{'激活中' if session.active else '余韵中'}",
        ]
        if session.active:
            lines.append(f"剩余时间：{remaining}秒")
            lines.append(f"当前敏感度：{sensitivity}")
        lines.append(f"触发次数：{session.trigger_count}")
        yield event.plain_result("\n".join(lines))

    @filter.command("mc_list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_list(self, event: AstrMessageEvent):
        active = await self.store.get_all_active()
        if not active:
            yield event.plain_result("当前没有活跃的沉浸会话")
            return
        lines = [f"活跃会话 ({len(active)} 个)："]
        for key, session in active:
            remaining = await self.store.get_remaining(key)
            lines.append(f"  {session.user_id} - 剩余 {remaining}秒")
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

    async def terminate(self):
        await self.store.clear_all()
