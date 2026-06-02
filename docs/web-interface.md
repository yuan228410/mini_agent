# mini_ai Web 界面

## 概述

通过 `mini-ai --web` 启动 Web 对话界面，默认监听 `http://localhost:8765`。

技术栈：FastAPI + WebSocket 后端，Vue 3 + Vite 前端。同一套 LLM/工具/记忆逻辑，仅 Display 层不同。

## 启动方式

```bash
# 生产模式（需先构建前端）
cd web && pnpm install && pnpm build
mini-ai --web                          # 默认 http://localhost:8765
mini-ai --web --port 3000              # 自定义端口

# 开发模式（前后端分别启动，支持热更新）
# 终端 1
PYTHONPATH=src uvicorn mini_ai.web.app:create_app --factory --port 8765
# 终端 2
cd web && pnpm dev                     # Vite dev server，自动代理 /api → 后端
```

## 设计风格

**Editorial 杂志编辑风** — 排版驱动的对话界面，不是千篇一律的气泡聊天。

- 消息用细线分隔，大量留白，大字号行文
- 思维链是"编者注"折叠区，琥珀色竖线标记
- 工具调用是"脚注"内联块，虚线竖线标记
- 背景叠加极细微噪点纹理（SVG feTurbulence），避免纯色平面感

### 字体

| 用途 | 字体 | 说明 |
|------|------|------|
| 标题/品牌 | Playfair Display | 衬线，编辑感 |
| 正文 | Source Sans 3 | 无衬线，阅读舒适 |
| 代码 | JetBrains Mono | 等宽，代码高亮 |

### 色板

| 元素 | 亮色 | 暗色 |
|------|------|------|
| 背景 | `#FAFAF8` | `#16161A` |
| 文字 | `#1A1A1A` | `#E8E4DF` |
| 强调 | `#E8912D` | `#F0A030` |
| 卡片 | `#F0EDE8` | `#1E1E24` |
| 代码块 | `#EDEAE4` | `#22222A` |
| 边框 | `#E0DCD6` | `#2A2A32` |

默认亮色主题，一键切换暗色，选择持久化到 localStorage，首次访问跟随 `prefers-color-scheme`。

### 动效

- 消息出现：淡入上移（`opacity 0→1, translateY 8px→0`），staggered 100ms
- 思维链展开：`max-height` 过渡 + `opacity`
- 打字流式：末尾脉动光标 `▍`
- 主题切换：全局 0.3s 颜色过渡
- 滚动到底部：`scrollIntoView({ behavior: 'smooth' })`

## 功能

- **流式输出** — 逐字打字效果 + 脉动光标
- **思维链展示** — 可折叠"编者注"风格，显示字数和耗时，展开后斜体渲染
- **工具调用展示** — 可折叠"脚注"风格，工具名等宽字体，参数浅色，结果折叠
- **Markdown 渲染** — 标题 Playfair Display，正文 Source Sans 3，代码 JetBrains Mono + highlight.js 高亮
- **亮色/暗色主题** — 默认亮色，一键切换，localStorage 持久化
- **状态栏** — 底部极简小字号显示：模型 | 上下文占比 | token | 消息数
- **模型切换** — 顶部下拉框选择模型，实时切换
- **斜杠命令** — 输入 `/` 弹出命令补全列表，支持 `/clear` `/compact` `/skill` `/genskill` `/model` `/thinking`
- **技能面板** — 右侧抽屉式技能列表，点击技能名自动激活
- **多会话并行** — 左侧侧边栏管理多个会话，可同时并行 LLM 生成，互不干扰
- **会话状态指示** — 侧边栏会话项旁显示"生成中"绿点脉动动画
- **多用户支持** — 用户名认证，按用户隔离数据目录，首次访问输入用户名
- **会话持久化** — 消息实时写入 HistoryDB（SQLite），每条消息（用户/助手/工具调用/工具结果）生成即持久化，后端重启自动恢复历史
- **中断生成** — 可中断 LLM 生成（LLM 流式层检查 abort_event），停止按钮合并到发送按钮位置
- **WebSocket 自动重连** — 断线后自动重连（指数退避：1s → 2s → 4s → ... → max 30s），重连成功后自动恢复登录状态
- **连接状态事件** — `connected`/`disconnected`/`reconnected` 事件通知前端，便于 UI 状态管理
- **工作空间管理** — 侧边栏顶部显示当前工作空间，支持创建/切换/删除/移除工作空间
- **文件浏览** — 工作空间关联的项目目录文件列表预览
- **计划模式** — /plan 进入只规划不执行模式，/act 切回执行模式，状态栏显示当前模式
- **任务计划面板** — 右侧独立 TodosPanel 显示 update_todos 工具更新的任务列表，可收起
- **多轮消息拆分** — 每轮 LLM 输出（思考→文本→工具调用）独立显示，不合并覆盖
- **实时状态栏** — token/上下文信息随每次工具调用和 LLM 响应实时更新
- **流式重试** — LLM 429 限流等错误自动重试 3 次，递增延迟
- **日志配置** — `logging.level` 控制控制台级别（默认 WARNING），`logging.file_level` 控制文件级别（默认 DEBUG）
- **工具参数安全** — LLM 传参缺失时返回错误信息而非抛异常
- **配置面板** — 右侧 ⚙ 设置面板，可配置模型参数、thinking、显示、运行等
- **模型管理** — 添加/删除模型，支持 OpenAI 和 Anthropic 两种协议
- **工作空间恢复** — 已移除的工作空间可恢复，也可彻底删除
- **批量删除会话** — 批量模式多选会话，一键删除
- **消息时间戳** — 每条消息显示本地时间（精确到秒），历史加载时只显示文本内容不显示工具调用和思考过程
- **滚动到底部按钮** — 消息滚动离开底部时显示，点击快速滚动到底部，位于输入框上方
- **多模态消息历史** — 历史消息中的图片可正确加载显示，支持 OpenAI 多模态格式
- **图片上传** — 输入框支持上传图片（最多 10MB），支持 PNG/JPEG/GIF/WebP 格式，大图自动压缩
- **MCP 服务器管理** — 设置面板添加/删除 MCP 服务器（stdio/streamable_http），工具面板查看连接状态和工具列表
- **工作流可视化面板** — 实时展示 DAG 工作流执行状态，支持并行任务进度追踪、耗时统计、错误展示

## 前端组件

```
web/src/
├── App.vue              # 根组件：标题栏 + SessionSidebar + ChatView + StatusBar
├── api.ts               # API 封装（fetch + WebSocket + session 管理）
├── theme.ts             # 主题管理（init/toggle/apply + localStorage）
├── style.css            # CSS 变量色板 + 噪点纹理 + 全局排版 + 动画
└── components/
    ├── ChatView.vue     # 主聊天界面：消息列表 + 输入框，WebSocket 通信 + 中断 + session 持久化
    ├── SettingsPanel.vue # 设置面板：模型参数、thinking、显示、运行等配置
    ├── MessageItem.vue  # 单条消息：Markdown 渲染 + 思维链 + 工具调用
    ├── SessionSidebar.vue # 左侧会话列表面板：工作空间切换 + 会话管理 + 收起/展开
    ├── ThinkingBlock.vue# 思维链折叠区：琥珀色竖线，点击展开/收起
    ├── ToolCallBlock.vue# 工具调用折叠区：虚线竖线，工具名等宽
    ├── InputBar.vue     # 底部输入区：textarea + 斜杠命令补全
    ├── SlashCommands.vue# 斜杠命令补全面板：输入 / 弹出，方向键/Tab 选择
    ├── ModelSelector.vue# 模型切换下拉框：显示当前模型，点击切换
    ├── SkillPanel.vue   # 技能面板：右侧抽屉，点击技能名激活
    ├── StatusBar.vue    # 底部状态栏：等宽小字号，靠右对齐
    ├── ThemeToggle.vue  # 主题切换按钮：☀/🌙，旋转过渡动画
    ├── FileBrowserPanel.vue # 工作空间文件浏览面板
    └── WorkflowPanel.vue # 工作流可视化面板：实时展示 DAG 工作流执行状态
```

## 后端架构

```
src/mini_ai/web/
├── __init__.py
├── app.py              # FastAPI 应用：lifespan 初始化，CORS，静态文件挂载
├── deps.py             # 共享组件初始化（SkillLoader / MemoryStore / Compactor 等）
├── display.py          # WebDisplay 适配器：线程安全推入 asyncio.Queue
└── routes/
    ├── __init__.py
    ├── chat.py         # 会话管理 + WebSocket 聊天 + 中断生成 + 历史查询 + 重置
    ├── models.py       # 模型列表 + 切换
    ├── files.py        # 文件浏览接口
    ├── skills.py       # 技能列表
    ├── commands.py     # 斜杠命令列表
    ├── config.py       # 状态信息
    └── workspaces.py   # 工作空间管理 API（按用户隔离）
```

### WebSocket 事件格式

```
event: thinking_start
data: {}

event: thinking
data: {"content": "思考内容..."}

event: thinking_end
data: {"chars": 113, "elapsed": 0.6}

event: text
data: {"content": "增量文本..."}

event: tool_start
data: {"name": "web_fetch", "args": "{\"url\":\"...\"}"}

event: tool_result
data: {"name": "web_fetch", "result": "...", "elapsed": 1.8}

event: done
data: {"prompt_tokens": 4120, "completion_tokens": 49}

event: error
data: {"error": "错误信息"}

event: workflow_start
data: {"tasks": [...], "total": 3}

event: task_start
data: {"id": "search", "agent": "researcher", "prompt": "搜索 RAG 技术"}

event: task_end
data: {"id": "search", "status": "done", "result_preview": "搜索结果..."}

event: workflow_end
data: {"elapsed": 12.5, "completed": 3, "failed": 0, "total": 3}
```

### WebSocket 模式

前端建立持久 WebSocket 连接，支持中断生成（`{"type": "abort"}`）。

`WS /api/chat/ws` 消息协议：

**客户端 → 服务端：**

```json
{"type": "chat", "message": "你好", "session_id": "xxx", "username": "xxx"}
{"type": "abort", "session_id": "xxx", "username": "xxx"}
```

**服务端 → 客户端：** `{"event": "...", "data": {...}}`，事件类型包括 `thinking_start`/`thinking`/`thinking_end`/`text`/`tool_start`/`tool_result`/`done`/`aborted`/`error`。

### 中断机制

1. 客户端点击"⏹ 停止"按钮（仅 WS 模式显示）→ 发送 `{"type": "abort"}`
2. WebSocket 端收到后设置 `abort_event.set()`
3. `_run_tool_loop_sync` 每轮循环检查 `abort_event.is_set()`
4. 若已中断：推送 `{"event": "aborted"}`，丢弃当前不完整消息，不会进入下一轮工具调用
5. 当前轮的 LLM 流式响应会跑完（requests 库限制），但不会触发下一轮

### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/session` | POST | 创建新会话，body: `{"username": "xxx", "workspace": "xxx"}` |
| `/api/sessions` | GET | 获取用户会话列表，参数: `username`, `workspace` |
| `/api/session` | DELETE | 删除会话，body: `{"username": "xxx", "session_id": "xxx"}` |
| `/api/session/rename` | PATCH | 重命名会话，body: `{"username": "xxx", "session_id": "xxx", "name": "xxx"}` |
| `/api/chat/ws` | WS | WebSocket 持久连接，支持双向通信和中断生成 |
| `/api/chat/history` | GET | 获取会话历史，参数: `session_id`, `workspace` |
| `/api/chat/reset` | POST | 重置会话，body: `{"session_id": "xxx"}` |
| `/api/chat/export` | GET | 导出会话为 Markdown 文件下载，参数: `session_id`, `username`, `workspace` |
| `/api/files/list` | GET | 获取工作空间文件列表 |
| `/api/files/browse` | GET | 浏览目录结构 |
| `/api/models` | GET | 获取模型列表及当前激活模型 |
| `/api/models/switch` | POST | 切换模型，body: `{"name": "deepseek"}` |
| `/api/skills` | GET | 获取技能列表 |
| `/api/commands` | GET | 获取斜杠命令列表（含参数选项） |
| `/api/mcp` | GET | MCP 服务器连接状态和工具列表 |
| `/api/config` | GET | 获取状态信息，参数: `session_id`, `username` |
| `/api/workspaces` | GET | 获取工作空间列表 |
| `/api/workspaces` | POST | 创建工作空间 |
| `/api/workspaces/add` | POST | 添加已有目录为工作空间 |
| `/api/workspaces/switch` | POST | 切换工作空间 |
| `/api/workspaces/{name}` | DELETE | 删除/移除工作空间 |
| `/api/chat/search` | GET | 搜索历史消息，参数: `keyword`, `session_id`, `workspace`, `date_from`, `date_to`, `limit` |

### 多用户 + 会话持久化 + 并发安全

后端维护 `_SESSIONS: dict[str, dict[str, list[dict]]]`，按 `username` → `session_id` 两级隔离。

**请求上下文隔离（RequestContext）：**
- 每个请求构建 `RequestContext`，封装独立的 `model_config`/`display`/`http_session`
- CLI/Web 统一使用 `RequestContext`，不再依赖全局 `_display`/`MODEL_CONFIG`/`_session`
- 多用户并发互不影响：用户 A 的工具调用结果显示到 A 的 WebDisplay，用户 B 的请求用 B 的 HTTP Session
- `_run_tool_loop_sync` 返回 `(msg, usage)` 元组，在 executor 线程内读取 `_get_usage()`，避免跨线程读到零值
- `_sessions_lock = threading.Lock()` 保护 `_SESSIONS` 字典并发读写

**Per-session 模型切换：**
- `_SESSION_MODELS` 记录每个会话的模型选择（`username:session_id` → model_name）
- 切换模型不修改全局 `MODEL_CONFIG`，不影响其他会话
- 请求时根据 session 查找模型名，深拷贝配置构建 `RequestContext`

**多用户隔离：**
- 首次访问 Web 时弹出用户名输入界面，存入 `localStorage`
- 后端按用户名隔离数据目录：`~/.mini_ai/users/<username>/`（default 用户用 `~/.mini_ai/` 向后兼容）
- 不同用户各自独立的会话列表、消息历史和工作空间
- 无密码认证，适合本地/内网自托管场景

**会话文件持久化：**
- 消息存入 HistoryDB（SQLite），后端重启后自动从数据库恢复
- 会话创建时初始化 HistoryDB，重置时清空重建
- 关联工作空间：`~/.mini_ai/users/<username>/workspaces/<ws>/web_sessions/<sid>/memory_data/history.db`

**工作空间管理：**
- 每个用户独立的工作空间列表，按 `~/.mini_ai/users/<username>/workspaces/` 隔离
- 工作空间可关联项目目录（project_path），支持创建/切换/删除/移除
- 添加工作空间：选择已有目录自动取目录名，或新建空工作空间
- 已移除的工作空间数据备份，可恢复或彻底删除
- 切换工作空间时重建系统提示词（含项目规范的 CLAUDE.md/AGENTS.md）
- 同一工作空间下可创建多个会话，会话并行生成互不干扰
- default 工作空间路径默认为 `<web启动目录>/<username>/`

**会话列表：**
- `GET /api/sessions?username=xxx&workspace=yyy` 返回该用户指定工作空间的会话及预览
- `POST /api/sessions/batch_delete` 批量删除会话（body: ）

**多会话并行：**
- 前端 `_states: Map<string, SessionState>` 管理每个会话的独立消息/流式状态
- 切换会话从 Map 恢复，不中断其他会话的 LLM 生成
- 后端 `_SESSION_LOCKS` 仅序列化同一会话的消息读写，不同会话不同线程并行
- WS 连接共用，事件按 `session_id` 路由到对应会话状态

### 错误处理与恢复

**LLM 请求错误**：
- 流式模式自动重试 3 次（429 限流/超时/网络错误），递增延迟（2s/4s/6s）
- 重试耗尽后推送 error 事件到前端，显示错误提示
- 非流式模式同样重试

**工具参数错误**：
- LLM 传参缺失或类型错误（如 `path: true`）时返回明确错误字符串
- 不抛异常，LLM 可看到错误并纠正
- 连续 3 次工具调用返回 Error → 提前退出循环

**LLM 无回复**：
- `run_tool_loop` 返回 None 时追加 "⚠ LLM 未返回有效回复" 消息
- 推送 error 事件，前端停止按钮恢复为发送按钮，用户可重新输入

**循环保护**：
- `runner.max_turns`（默认 20）轮强制退出
- 退出时调用 `display.text_end()` 确保流式状态正确关闭
- 推送 complete + done 事件，前端正常恢复

### 记忆/压缩/搜索（Per-session 隔离）

每个 Web 会话独立初始化 MemoryStore + HistoryDB + Compactor 实例：

- **MemoryStore** — 三层记忆（情景层/长期层/用户画像），存放在 `<session_dir>/<sid>/memory_data/`
- **HistoryDB** — SQLite 历史存储，支持全文搜索（`/api/chat/search`）
- **Compactor** — 上下文超阈值自动压缩 + 记忆更新，复用 `config.yaml` 的 `compactor` 配置
- **历史加载量** — `web.history_limit`（默认 200）控制前端展示的消息条数，`compactor.context_limit`（默认 50）控制 LLM 上下文加载量，`compactor.keep_recent` 控制压缩后保留的完整消息数，三者独立配置
- **ContextBuilder** — 系统提示词含记忆 + 技能 + 项目规范（CLAUDE.md/AGENTS.md per-workspace 共享）
- **工具绑定** — `remember`/`recall`/`forget`/`search_history` 工具在每轮 `_run_tool_loop_sync` 中动态绑定当前会话实例
- **项目规范共享** — 同一工作空间下所有会话共享 CLAUDE.md/AGENTS.md（通过 ContextBuilder + project_path 读取）

存储路径：
```
<session_dir>/<sid>/
├── meta.json           # 会话元信息（名称）
└── memory_data/
    ├── MEMORY.md        # 长期记忆
    ├── USER.md          # 用户画像
    ├── 2026-05-27.md    # 情景记忆
    └── history.db       # SQLite 历史搜索
```

压缩触发时机：每轮对话后检查 `prompt_tokens` 或本地字符数超阈值，自动压缩并更新三层记忆。

### 配置管理

**`GET /api/settings`** — 返回所有配置（模型参数不含 API key）
**`PUT /api/settings`** — 更新配置（支持 thinking/display/runner/plan/logging/streaming 等全局配置 + 单个模型参数覆盖）
**`POST /api/settings/add_model`** — 添加新模型（名称、协议、API URL/Key、模型 ID 等）
**`DELETE /api/settings/remove_model`** — 删除模型（至少保留一个）
**`GET /api/workspaces/removed`** — 列出已移除的工作空间
**`POST /api/workspaces/restore`** — 恢复已移除的工作空间
**`DELETE /api/workspaces/removed/{name}`** — 彻底删除已移除的工作空间数据
**`POST /api/settings/mcp/add`** — 添加 MCP 服务器（name, type, command/url, args, headers）
**`DELETE /api/settings/mcp/{name}`** — 删除 MCP 服务器

### 斜杠命令

前端输入 `/` 时弹出命令补全列表，支持：

| 命令 | 参数 | 说明 |
|------|------|------|
| `/clear` | — | 清空当前会话消息 |
| `/purge` | — | 彻底删除历史消息（不可恢复） |
| `/compact` | — | 手动触发对话压缩 |
| `/genskill` | 技能名称 | 从对话生成技能 |
| `/skill` | 技能名称 | 使用指定技能 |
| `/model` | 模型名称 | 切换模型 |
| `/thinking` | collapsed/expanded/hidden | 设置思考展示模式 |
| `/plan` | — | 进入计划模式（只规划不执行） |
| `/act` | — | 切换到执行模式 |
| `/mcp` | — | 查看 MCP 服务器状态 |

`/model` 和 `/skill` 的参数选项从后端 API 动态获取。方向键导航，Tab/Enter 确认，Escape 取消。

## 构建

```bash
# 安装前端依赖
cd web && pnpm install

# 开发（热更新）
pnpm dev

# 生产构建
pnpm build    # 输出到 web/dist/

# 预览生产构建
pnpm preview
```

Vite 开发模式自动代理 `/api` 请求到 `http://localhost:8765`（后端）。

## 待办

- [ ] highlight.js 按需加载 — 仅加载常用语言包，减少前端体积
- [ ] 多模态输入 — 支持图片/PDF 作为输入
- [ ] 计划任务机制 — 定时/周期性任务调度

## 最近更新

| 日期 | 功能 |
|------|------|
| 2026-06-01 | 工作流可视化面板、DAG 任务实时追踪 |
| 2026-06-01 | 滚动到底部按钮、多模态消息历史加载 |
