import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig

from .core.config import PluginConfig
from .core.store import SessionStore
from .core.preset import get_templates, MODE_NAMES


class Main(Star):
    """脑控大师 - 多模式沉浸式互动插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config)
        self.store = SessionStore(self.cfg)

    def _get_key(self, event: AstrMessageEvent) -> str:
        """根据 scope 配置生成会话 key"""
        umo = event.unified_msg_origin
        if self.cfg.scope == "user":
            return f"{umo}:{event.get_sender_id()}"
        return umo

    # ================= LLM 钩子 =================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在 LLM 请求前注入沉浸式 prompt"""
        key = self._get_key(event)
        session = await self.store.get(key)
        if not session:
            return

        sensitivity = await self.store.get_sensitivity(key)
        mode = self.cfg.mode
        templates = get_templates(mode, self.cfg.item_name, sensitivity)

        if session.active:
            template = templates["enter"]
        elif session.exit_ts is not None:
            template = templates["afterglow"]
        else:
            return

        req.system_prompt += f"\n\n{template}"

    # ================= 消息处理 =================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        """处理进入/退出/延长关键词"""
        if not event.message_str:
            return

        msg = event.message_str.strip()
        key = self._get_key(event)
        umo = event.unified_msg_origin
        user_id = event.get_sender_id()

        # 权限检查
        if self.cfg.admin_only_mode and not event.is_admin():
            return

        # 群白名单
        if not event.is_private_chat():
            group_id = event.message_obj.group_id
            if self.cfg.group_whitelist and group_id not in self.cfg.group_whitelist:
                return

        # 退出关键词
        if msg in self.cfg.exit_keywords:
            session = await self.store.get(key)
            if session and session.active:
                await self.store.deactivate(key)
                remaining = await self.store.get_remaining(key)
                yield event.plain_result("已退出沉浸模式~")
            return

        # 延长关键词
        if msg in self.cfg.extend_keywords:
            session = await self.store.get(key)
            if session and session.active:
                ok, result_msg = await self.store.extend(key)
                if ok:
                    remaining = await self.store.get_remaining(key)
                    yield event.plain_result(f"已延长~ 剩余 {remaining} 秒")
                return

        # 进入关键词
        if msg not in self.cfg.enter_keywords:
            return

        # 冷却检查
        cd_user = await self.store.check_cooldown_user(key)
        if cd_user > 0:
            yield event.plain_result(f"还在冷却中，请等待 {cd_user} 秒")
            return

        if self.cfg.scope == "session":
            cd_group = await self.store.check_cooldown_group(key)
            if cd_group > 0:
                yield event.plain_result(f"群聊冷却中，请等待 {cd_group} 秒")
                return

        # 激活
        ok, result_msg = await self.store.activate(key, user_id)
        if ok:
            mode_name = MODE_NAMES.get(self.cfg.mode, self.cfg.mode)
            yield event.plain_result(
                f"已进入【{mode_name}】模式，持续 {self.cfg.state_duration} 秒~"
            )
        else:
            yield event.plain_result(result_msg)

    # ================= 管理命令 =================

    @filter.command("mc_help")
    async def mc_help(self, event: AstrMessageEvent):
        """查看帮助"""
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
        """查看当前状态"""
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
        """查看所有活跃会话（管理员）"""
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
        """清除所有会话（管理员）"""
        count = await self.store.clear_all()
        yield event.plain_result(f"已清除 {count} 个会话")

    @filter.command("mc_mode")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_mode(self, event: AstrMessageEvent, mode_name: str = ""):
        """切换预设模式（管理员）"""
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
        mode_display = MODE_NAMES[mode_name]
        yield event.plain_result(f"已切换到【{mode_display}】模式")

    # ================= 清理 =================

    async def terminate(self):
        await self.store.clear_all()
