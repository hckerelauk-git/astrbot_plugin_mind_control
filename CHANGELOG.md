# 脑控大师 更新日志

## [v2.6.0] - 2026-06-21
### 新增
- 配置页 UI 全面现代化：暗色侧边栏 + 卡片布局 + 左侧导航
- 自定义预设列表展示完整提示词预览，带 badge 和 hover 动效
- 更精致的表单样式、按钮交互和状态提示

### 修复
- 修复 deactivate/extend/check_cooldown/get_remaining/get_all_sessions/clear_all/set_cooldown
  所有方法用了不存在的 self._lock，统一改为 self._get_lock(key) 异步锁
- 修复 clear_all 返回数量不准的问题，现正确返回实际清除的会话数
- 修复 _remote_start 中 set_cooldown 未 await 的问题
- 修复 control_cmd 激活成功后不返回消息的问题，现 yield 空字符串防止事件泄漏给 LLM
- 修复 control_cmd 中 admin_only/whitelist 检查使用裸 return 的问题
- 修复退出关键词处理中 else 分支 return 阻断消息流到 LLM 的问题
- 退出关键词触发后 session 变量重新获取，确保 on_llm_request 正确读到 afterglow 状态
- 移除进入沉浸模式后产生的额外机器回复消息，让 LLM 自然响应
- 移除了并发限制功能（max_concurrent），恢复为纯 per-key 并发处理
- 给 Session 加 start 字段，修复 _calc_sensitivity 在延长后曲线计算错误的问题
- 提示词模板补充敏感度范围说明（0-100，100 为极限），让 LLM 正确理解敏感度差异
- on_llm_request 注入前增加 system_prompt 字段存在性检查，避免某些 provider 下静默失败
- page_get_current 使用配置默认敏感度而非硬编码 50
- 版本号更新到 v2.6.0，metadata.yaml 同步，mc_help 帮助文本更新

## [v2.6.1] - 2026-06-25
### 修复
- 修复 admin_only 模式下非管理员发送普通消息也被拦截并回复「仅管理员可用」的问题
- 现在 admin_only 仅在触发关键词（进入/退出/延长）时才生效，普通消息直接放行到 LLM

## [v2.5.5] - 2026-06-21
### 修复
- 修复 deactivate/extend/check_cooldown/get_remaining/get_all_sessions/clear_all/set_cooldown
  所有方法用了不存在的 self._lock，统一改为 self._get_lock(key) 异步锁
- 修复 clear_all 返回数量不准的问题，现正确返回实际清除的会话数
- 修复 _remote_start 中 set_cooldown 未 await 的问题
- 修复 control_cmd 激活成功后不返回消息的问题，现 yield 空字符串防止事件泄漏给 LLM
- 修复 control_cmd 中 admin_only/whitelist 检查使用裸 return 的问题
- 修复退出关键词处理中 else 分支 return 阻断消息流到 LLM 的问题
- 退出关键词触发后 session 变量重新获取，确保 on_llm_request 正确读到 afterglow 状态
- 移除进入沉浸模式后产生的额外机器回复消息，让 LLM 自然响应
- 移除了并发限制功能（max_concurrent），恢复为纯 per-key 并发处理
- 给 Session 加 start 字段，修复 _calc_sensitivity 在延长后曲线计算错误的问题
- 提示词模板补充敏感度范围说明（0-100，100 为极限），让 LLM 正确理解敏感度差异
- on_llm_request 注入前增加 system_prompt 字段存在性检查，避免某些 provider 下静默失败
- page_get_current 使用配置默认敏感度而非硬编码 50
- 更新版本号到 v2.5.5，metadata.yaml 同步，mc_help 帮助文本更新

## [v2.5.2] - 2026-06-14
### 优化
- 增加 LLM 提示词注入日志：会话状态、模式、敏感度、注入阶段、模板长度与预览
- 增加消息处理日志：进入/退出/延长/放行到 LLM 的关键路径
- 无会话或 waiting 状态跳过注入时写入 debug/info 日志，便于排查

## [v2.5.1] - 2026-06-14
### 修复
- 修复进入沉浸后消息被 `return` 截断，导致 `on_llm_request` 无法注入提示词的问题
- 修复 `/控制` 指令进入成功后同样阻断 LLM 流程的问题
- 修复 `message_handler` 对非进入关键词直接 `return`，导致已激活会话普通聊天无法注入提示词的问题
- 修复退出关键词在已进入 afterglow 时仍 `return`，余韵提示词无法注入的问题
- 修复插件 Page 前端 API 路径重复拼接插件名导致配置页加载失败的问题

## [v2.1.5] - 2026-06-14
### 新增
- 添加插件配置页面（pages/config/），支持可视化管理自定义预设
- 页面展示当前模式的完整提示词内容
- 支持在页面内添加/删除自定义预设
- 后端新增 4 个 Pages API：/current、/presets、/preset/add、/preset/delete

## [v2.1.2] - 2026-06-13
### 新增
- 支持用户自定义预设模式和提示词（通过 `_conf_schema.json` 的 `custom_presets` 配置）
- 用户可以在 WebUI 中添加自己的预设（名称 + enter/afterglow/exit 提示词模板）
- 自定义预设支持 `{item_name}` 和 `{sensitivity}` 占位符
- `mode` 配置项现在支持直接输入自定义预设名称，不再限制只能选内置模式

### 优化
- 自定义预设未填写提示词时自动 fallback 到 control 模式，避免注入空提示词

## [v2.1.1] - 2026-06-13
### 修复
- 修复配置访问bug，彻底移除旧的 `ConfigNode` 封装
- 统一使用 `AstrBotConfig.get("key", default)` 读取配置
- 修复 `name 'session' is not defined` 错误
- 修复所有 `self.cfg.xxx` 访问方式导致的兼容问题

## [v2.1.0] - 2026-06-06
### 发布说明

## 🎯 插件概述
**脑控大师** (astrbot_plugin_mind_control) 是一个 AstrBot 多模式沉浸式互动插件。  
支持关键词/指令唤醒 Bot，唤醒后 Bot 进入沉浸式角色扮演状态，通过 LLM 注入模板实现自然的角色扮演回复。

---

## 🚀 核心功能

### 1. 多种预设模式
| 模式 | 说明 |
|------|------|
| **control**（控制） | 经典控制模式，Bot 被遥控 |
| **pet**（宠物化） | Bot 变成毛茸茸的小动物 |
| **teacher**（师徒） | 严厉但温柔的老师 |
| **shy**（害羞） | 极度害羞模式 |
| **tsundere**（傲娇） | 傲娇模式 |

### 2. 渐进强度曲线
- **flat** - 固定敏感度
- **ramp_up** - 逐步增强
- **decay** - 逐渐消退
- **wave** - 波动起伏

### 3. 余韵阶段
退出控制模式后，Bot 不会立即恢复正常，而是进入"余韵"状态，偶尔还会表现出角色特征，自然过渡。

### 4. 远程控制
- `/tp_st` - 远程启动控制模式（管理员可选）
- `/tp_st 50` - 远程启动并指定敏感度
- 支持 `waiting` 状态：启动后等待用户说话才开始计时

### 5. 指令控制
- `/控制` - 进入控制模式（默认敏感度）
- `/控制 50` - 进入控制模式（指定敏感度 0-100）
- 触发词：`我要控制你了`、`拿出来吧`、`继续` 等

---

## 📋 指令列表

| 指令 | 说明 |
|------|------|
| `/控制` | 进入控制模式（默认敏感度） |
| `/控制 50` | 进入控制模式（指定敏感度） |
| `/tp_st` | 远程启动（默认敏感度） |
| `/tp_st 50` | 远程启动（指定敏感度） |
| `/mc_help` | 查看帮助 |
| `/mc_status` | 查看当前状态 |
| `/mc_list` | 查看所有会话（管理员） |
| `/mc_clear` | 清除所有会话（管理员） |
| `/mc_mode` | 切换预设模式（管理员） |

### 触发词（默认）
- **进入**：`我要控制你了`
- **退出**：`拿出来吧` / `停止`
- **延长**：`继续` / `再来`

---

## ⚙️ 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| mode | string | control | 预设模式 |
| scope | string | user | 作用范围（user/session） |
| enter_keywords | list | ["我要控制你了"] | 进入关键词 |
| exit_keywords | list | ["拿出来吧", "停止"] | 退出关键词 |
| extend_keywords | list | ["继续", "再来"] | 延长关键词 |
| state_duration | int | 180 | 持续时间（秒） |
| extend_duration | int | 60 | 延长时间（秒） |
| cooldown_user | int | 30 | 每用户冷却（秒） |
| cooldown_group | int | 60 | 每群冷却（秒） |
| sensitivity | int | 50 | 默认敏感度（0-100） |
| curve | string | flat | 强度曲线 |
| afterglow_enable | bool | true | 启用余韵阶段 |
| afterglow_duration | int | 30 | 余韵持续时间（秒） |
| item_name | string | 特殊装置 | 装置名称 |
| group_whitelist | list | [] | 群聊白名单 |
| admin_only_mode | bool | false | 仅管理员可用 |
| waiting_timeout | int | 300 | 远程启动等待超时（秒） |
| td_st_admin_only | bool | false | /tp_st 仅管理员可用 |
| td_st_cooldown | int | 30 | /tp_st 冷却时间（秒） |

---

## 🔧 技术特性

### Session 状态机
```
waiting → active → afterglow → 移除
```
- **waiting**：远程启动后等待用户说话
- **active**：控制模式激活中，LLM 注入模板
- **afterglow**：退出后的余韵状态

### 渐进强度计算
根据 `curve` 配置和已过时间百分比，动态调整敏感度：
- `flat`：固定值
- `ramp_up`：`base * (0.3 + 0.7 * progress)`
- `decay`：`base * (1.0 - 0.7 * progress)`
- `wave`：`base * (0.5 + 0.5 * sin(progress * 2π))`

### 线程安全
所有 Session 操作使用 `asyncio.Lock()` 保护，支持并发访问。

---

## 📦 安装方式

### 方式一：手动安装
```bash
cd AstrBot/data/plugins
git clone https://github.com/hckerelauk-git/astrbot_plugin_mind_control
```

### 方式二：压缩包安装
下载 `astrbot_plugin_mind_control.zip`，解压到 `data/plugins/` 目录。

---

## 📁 文件结构
```
astrbot_plugin_mind_control/
├── main.py           (21.58 KB) - 核心逻辑
├── metadata.yaml     (1.47 KB)  - 插件元信息
├── _conf_schema.json (4.3 KB)   - 配置定义
├── README.md         (4.27 KB)  - 使用文档
└── .gitignore
```

---

## 🐛 已修复问题
- 修复 `Context` 对象无 `event_manager` 属性错误，改用 `MessageChain` API
- 修复退出时发送纯文本而非 LLM 回复的问题
- 修复 `/tp_st` 命令未正确拦截消息导致 LLM 收到原始文本
- 移除 WebUI Plugin Page（简化架构）
- 修复所有文件 UTF-8 BOM 编码问题

---

## 📝 作者
**ELAUK** (hckerelauk-git)

## 🔗 链接
- GitHub：https://github.com/hckerelauk-git/astrbot_plugin_mind_control
- 插件市场：https://plugins.astrbot.app

---

## 📄 许可证
AGPL-3.0