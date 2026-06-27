# 脑控大师 (astrbot_plugin_mind_control)

多模式沉浸式互动插件，支持渐进强度、余韵、随机变体。

[English](#english)

## 功能

### 多种预设模式

| 模式 | 说明 |
|------|------|
| **control**（控制） | 经典控制模式，Bot 被遥控 |
| **pet**（宠物化） | Bot 变成毛茸茸的小动物 |
| **teacher**（师徒） | Bot 变成严厉的老师 |
| **shy**（害羞） | Bot 性格极度害羞 |
| **tsundere**（傲娇） | Bot 进入傲娇模式 |

### 自定义预设（v2.1.2+）

你可以在 WebUI 的插件配置中添加自己的预设模式。每个预设需要填写：
- 预设名称（用于在「预设模式」中选择）
- enter（进入时的提示词模板）
- afterglow（余韵阶段的提示词模板）
- exit（退出后的提示词模板）

提示词支持 `{item_name}` 和 `{sensitivity}` 占位符。

### 核心特性

- **作用范围开关**：可选 `user`（仅触发者）或 `session`（全群）
- **随机反应变体**：每个模式有多个模板，每次随机抽取
- **渐进强度曲线**：flat（固定）/ ramp_up（增强）/ decay（消退）/ wave（波动）
- **余韵阶段**：退出后 Bot 慢慢恢复，而不是立即结束
- **按键延长**：在沉浸状态中发送"继续"可延长持续时间
- **双维度冷却**：每用户冷却 + 每群冷却
- **群白名单**：限制哪些群可触发
- **i18n 中英双语**

## 安装

### 方式一：通过 AstrBot WebUI 安装（推荐）

前往 [AstrBot 插件市场](https://plugins.astrbot.app) 搜索"脑控大师"安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/hckerelauk-git/astrbot_plugin_mind_control
```

重启 AstrBot 或在 WebUI 点击"重载插件"。

## 指令

| 命令 | 权限 | 说明 |
|------|------|------|
| `/mc_help` | 所有人 | 查看帮助和当前设置 |
| `/mc_status` | 所有人 | 查看当前状态 |
| `/控制` 或 `/控制 50` | 所有人 | 进入控制模式（默认/指定敏感度） |
| `/mc_st` | 可配置 | 远程启动，等待用户下一条消息触发 |
| `/mc_list` | 管理员 | 查看所有活跃会话 |
| `/mc_clear` | 管理员 | 清除所有会话 |
| `/mc_mode` | 管理员 | 切换预设模式 |

## 配置

在 AstrBot WebUI -> 插件管理 -> 脑控大师 中配置。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| mode | 预设模式 | control |
| scope | 作用范围 | user |
| enter_keywords | 进入关键词 | ["控制", "我要控制你了"] |
| exit_keywords | 退出关键词 | ["拿出来吧", "停止"] |
| extend_keywords | 延长关键词 | ["继续", "再来"] |
| state_duration | 持续时间（秒） | 180 |
| extend_duration | 延长时间（秒） | 60 |
| cooldown_user | 每用户冷却（秒） | 30 |
| cooldown_group | 每群冷却（秒） | 60 |
| sensitivity | 敏感度（0-100） | 50 |
| curve | 强度曲线 | flat |
| afterglow_enable | 启用余韵 | true |
| afterglow_duration | 余韵时长（秒） | 30 |
| item_name | 装置名称 | 特殊装置 |
| group_whitelist | 群白名单 | []（空=不限制） |
| admin_only_mode | 仅管理员 | false |
| waiting_timeout | 远程启动等待超时（秒） | 300 |
| remote_msg | 远程启动提示消息 | 空 |
| td_st_admin_only | /mc_st 仅管理员 | false |
| td_st_cooldown | /mc_st 冷却时间（秒） | 30 |

## 使用示例

1. 配置进入关键词为 `["控制"]`
2. 在群聊发送"控制" → Bot 进入沉浸模式
3. Bot 会以预设模式的角色状态回应消息
4. 发送"继续"可延长沉浸时间
5. 发送"拿出来吧"退出，或等待超时自动退出
6. 退出后进入余韵阶段，Bot 会慢慢恢复

## 技术细节

- 基于 AstrBot 插件系统 `@filter.on_llm_request()` 钩子注入 system prompt
- 预设模板存储在 `core/preset.py`，每个模式有多个随机变体
- 会话状态使用内存缓存 + asyncio.Lock 保证线程安全
- 支持渐进强度曲线（正弦波、线性增减等）
- 完整的 `terminate()` 清理逻辑

## License

AGPL-3.0

---

# English

## AstrBot Mind Control Plugin

Multi-mode immersive interaction plugin with progressive intensity, afterglow, and random variants.

### Features

- **5 Preset Modes**: control / pet / teacher / shy / tsundere
- **Scope Toggle**: per-user or per-session
- **Random Variants**: multiple templates per mode, randomly selected
- **Intensity Curves**: flat / ramp_up / decay / wave
- **Afterglow Phase**: gradual recovery after exit
- **Duration Extension**: extend with keywords mid-session
- **Dual Cooldown**: per-user + per-group
- **Group Whitelist**: restrict to specific groups

### Commands

| Command | Permission | Description |
|---------|-----------|-------------|
| `/mc_help` | Everyone | View help |
| `/mc_status` | Everyone | View current status |
| `/mc_list` | Admin | List all active sessions |
| `/mc_clear` | Admin | Clear all sessions |
| `/mc_mode` | Admin | Switch preset mode |

### Installation

```bash
cd AstrBot/data/plugins
git clone https://github.com/hckerelauk-git/astrbot_plugin_mind_control
```
