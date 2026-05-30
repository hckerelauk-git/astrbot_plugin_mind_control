from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass

from .config import PluginConfig


@dataclass(slots=True)
class Session:
    active: bool
    """是否处于激活状态"""
    user_id: str
    """触发用户 ID"""
    end: float | None = None
    """激活结束时间戳"""
    exit_ts: float | None = None
    """进入 afterglow 的时间戳"""
    trigger_count: int = 0
    """触发次数"""


@dataclass
class Stats:
    total_triggers: int = 0
    total_duration: float = 0.0
    active_sessions: int = 0


class SessionStore:
    """会话状态管理"""

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self._data: dict[str, Session] = {}
        self._cooldowns: dict[str, tuple[float, float]] = {}  # key -> (user_end, group_end)
        self._lock = asyncio.Lock()
        self._stats = Stats()

    # ================= 内部 =================

    def _cleanup_one(self, key: str) -> None:
        """惰性清理：超时 → afterglow → 彻底移除"""
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
        """根据曲线计算当前敏感度"""
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

    # ================= API =================

    async def get(self, key: str) -> Session | None:
        """获取当前会话（触发惰性清理）"""
        async with self._lock:
            self._cleanup_one(key)
            return self._data.get(key)

    async def get_sensitivity(self, key: str) -> int:
        """获取当前会话的敏感度（含曲线计算）"""
        async with self._lock:
            self._cleanup_one(key)
            s = self._data.get(key)
            if not s:
                return self.cfg.sensitivity
            return self._calc_sensitivity(s)

    async def activate(self, key: str, user_id: str) -> tuple[bool, str]:
        """激活会话"""
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
        """手动退出 → 进入 afterglow"""
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active:
                return False
            s.active = False
            s.exit_ts = time.time()
            return True

    async def extend(self, key: str) -> tuple[bool, str]:
        """延长持续时间"""
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active:
                return False, "当前不在沉浸状态"
            if s.end is not None:
                s.end += self.cfg.extend_duration
            return True, f"已延长 {self.cfg.extend_duration} 秒"

    async def check_cooldown_user(self, key: str) -> int:
        """用户冷却剩余秒数"""
        async with self._lock:
            cd = self._cooldowns.get(key)
            if not cd:
                return 0
            return max(0, int(cd[0] - time.time()))

    async def check_cooldown_group(self, key: str) -> int:
        """群冷却剩余秒数"""
        async with self._lock:
            cd = self._cooldowns.get(key)
            if not cd:
                return 0
            return max(0, int(cd[1] - time.time()))

    async def get_remaining(self, key: str) -> int:
        """剩余时间秒数"""
        async with self._lock:
            s = self._data.get(key)
            if not s or not s.active or s.end is None:
                return 0
            return max(0, int(s.end - time.time()))

    async def is_afterglow(self, key: str) -> bool:
        """是否在余韵阶段"""
        async with self._lock:
            self._cleanup_one(key)
            s = self._data.get(key)
            return s is not None and not s.active and s.exit_ts is not None

    async def get_all_active(self) -> list[tuple[str, Session]]:
        """获取所有活跃会话"""
        async with self._lock:
            expired_keys = []
            for key in list(self._data.keys()):
                self._cleanup_one(key)
                if key not in self._data:
                    expired_keys.append(key)

            return [(key, s) for key, s in self._data.items() if s.active]

    async def clear_all(self) -> int:
        """清除所有会话"""
        async with self._lock:
            count = len(self._data)
            self._data.clear()
            self._cooldowns.clear()
            return count

    async def clear_one(self, key: str) -> bool:
        """清除指定会话"""
        async with self._lock:
            self._cooldowns.pop(key, None)
            return self._data.pop(key, None) is not None

    def get_stats(self) -> Stats:
        """获取统计信息"""
        self._stats.active_sessions = len([s for s in self._data.values() if s.active])
        return self._stats
