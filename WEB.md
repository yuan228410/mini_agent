# mini_ai Web 界面

## 概述

通过 `mini-ai --web` 启动 Web 对话界面，默认监听 `http://localhost:8765`。

技术栈：FastAPI + WebSocket/SSE 双模式后端，Vue 3 + Vite 前端。同一套 LLM/工具/记忆逻辑，仅 Display 层不同。

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
- **多会话隔离** — 每个浏览器标签页独立 session，互不干扰
- **多用户支持** — 用户名认证，按用户隔离会话目录，首次访问输入用户名
- **会话文件持久化** — 消息写入 JSONL 文件，后端重启自动恢复历史
- **中断生成** — WS 模式下可中断 LLM 生成（服务端真正停止）；SSE 模式不显示停止按钮
- **会话持久化** — 刷新页面自动恢复历史消息（session_id 存 localStorage）

## 前端组件

```
web/src/
├── App.vue              # 根组件：标题栏 + ChatView + StatusBar + SkillPanel
├── api.ts               # API 封装（fetch + SSE + WebSocket + session 管理）
├── theme.ts             # 主题管理（init/toggle/apply + localStorage）
├── style.css            # CSS 变量色板 + 噪点纹理 + 全局排版 + 动画
└── components/
    ├── ChatView.vue     # 主聊天界面：消息列表 + 输入框，WS/SSE 双模式 + 中断 + session 持久化
    ├── MessageItem.vue  # 单条消息：Markdown 渲染 + 思维链 + 工具调用
    ├── ThinkingBlock.vue# 思维链折叠区：琥珀色竖线，点击展开/收起
    ├── ToolCallBlock.vue# 工具调用折叠区：虚线竖线，工具名等宽
    ├── InputBar.vue     # 底部输入区：textarea + 斜杠命令补全
    ├── SlashCommands.vue# 斜杠命令补全面板：输入 / 弹出，方向键/Tab 选择
    ├── ModelSelector.vue# 模型切换下拉框：显示当前模型，点击切换
    ├── SkillPanel.vue   # 技能面板：右侧抽屉，点击技能名激活
    ├── StatusBar.vue    # 底部状态栏：等宽小字号，靠右对齐
    └── ThemeToggle.vue  # 主题切换按钮：☀/🌙，旋转过渡动画
```

## 后端架构

```
src/mini_ai/web/
├── __init__.py
├── app.py               # FastAPI 应用：lifespan 初始化，CORS，静态文件挂载
├── deps.py              # 共享组件初始化（SkillLoader / MemoryStore / Compactor 等）
├── display.py           # WebDisplay 适配器：线程安全推入 asyncio.Queue
└── routes/
    ├── __init__.py
    ├── chat.py           # 会话管理 + SSE/WS 双模式聊天 + 中断生成 + 历史查询 + 重置
    ├── models.py         # GET /api/models + POST /api/models/switch
    ├── skills.py         # GET /api/skills
    ├── commands.py       # GET /api/commands（斜杠命令列表 + 参数选项）
    └── config.py         # GET /api/config（状态信息，支持 session_id）
```

### 线程安全设计

LLM 流式输出（`chat_stream()`）是同步生成器，直接调用会阻塞 asyncio 事件循环。

解决方案：
1. `_run_tool_loop_sync()` 在 `loop.run_in_executor(None, ...)` 中执行，不阻塞事件循环
2. `WebDisplay` 通过 `loop.call_soon_threadsafe()` + `queue.put_nowait()` 线程安全推入 asyncio.Queue
3. SSE `event_generator` 在事件循环中 `await queue.get()` 消费队列，实时推送给前端
4. `register_display(disp)` 注入 WebDisplay 实例，工具调用结果自动通过同一通道推送

### SSE 事件协议

`POST /api/chat` 返回 `text/event-stream`，事件格式：

```
event: thinking_start
data: {}

event: thinking
data: {"content": "增量思考文本"}

event: thinking_end
data: {"chars": 113, "elapsed": 0.6}

event: text
data: {"content": "增量回复文本"}

event: tool_start
data: {"name": "web_fetch", "args": "{\"url\":\"...\"}"}

event: tool_result
data: {"name": "web_fetch", "result": "...", "elapsed": 1.8}

event: done
data: {"prompt_tokens": 4120, "completion_tokens": 49}

event: error
data: {"error": "错误信息"}
```


### WebSocket 模式

`config.yaml` 配置 `web.transport: ws|sse`，默认 `ws`。

- WS 模式：前端建立持久 WebSocket 连接，支持中断生成（`{"type": "abort"}`）
- SSE 模式：纯 HTTP 流式，不支持中断，停止按钮不显示
- 前端 WS 连接失败时自动回退到 SSE

`WS /api/chat/ws` 消息协议：

**客户端 → 服务端：**

```json
{"type": "chat", "message": "你好", "session_id": "xxx", "username": "xxx"}
{"type": "abort"}
```

**服务端 → 客户端：** 事件格式与 SSE 相同（`{"event": "...", "data": {...}}`），新增 `aborted` 事件。

### 中断机制

1. 客户端点击"⏹ 停止"按钮（仅 WS 模式显示）→ 发送 `{"type": "abort"}`
2. WebSocket 端收到后设置 `abort_event.set()`
3. `_run_tool_loop_sync` 每轮循环检查 `abort_event.is_set()`
4. 若已中断：推送 `{"event": "aborted"}`，丢弃当前不完整消息，不会进入下一轮工具调用
5. 当前轮的 LLM 流式响应会跑完（requests 库限制），但不会触发下一轮

### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/session` | POST | 创建新会话，body: `{"username": "xxx"}` |
| `/api/sessions` | GET | 获取用户会话列表，参数: `username` |
| `/api/chat` | POST | SSE 流式对话，body: `{"message": "...", "session_id": "xxx"}` |
| `/api/chat/ws` | WS | WebSocket 持久连接，支持双向通信和中断生成 |
| `/api/chat/history` | GET | 获取会话历史，参数: `session_id` |
| `/api/chat/reset` | POST | 重置会话，body: `{"session_id": "xxx"}` |
| `/api/models` | GET | 获取模型列表及当前激活模型 |
| `/api/models/switch` | POST | 切换模型，body: `{"name": "deepseek"}` |
| `/api/skills` | GET | 获取技能列表 |
| `/api/commands` | GET | 获取斜杠命令列表（含参数选项） |
| `/api/config` | GET | 获取状态信息，参数: `session_id` |



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
- 后端按用户名隔离会话目录：`~/.mini_ai/web_sessions/<username>/<session_id>.jsonl`
- 不同用户各自独立的会话列表和消息历史
- 无密码认证，适合本地/内网自托管场景

**会话文件持久化：**
- 每条消息 append 写入用户对应的 JSONL 文件
- 后端重启后，前端请求时自动从文件加载到内存
- 会话创建时写首行 system prompt，重置时清空重建
- 存储路径：`~/.mini_ai/web_sessions/<username>/<session_id>.jsonl`

**会话列表：**
- `GET /api/sessions?username=xxx` 返回该用户所有会话及预览

### 斜杠命令

前端输入 `/` 时弹出命令补全列表，支持：

| 命令 | 参数 | 说明 |
|------|------|------|
| `/clear` | — | 清空当前会话消息 |
| `/compact` | — | 手动触发对话压缩 |
| `/genskill` | 技能名称 | 从对话生成技能 |
| `/skill` | 技能名称 | 使用指定技能 |
| `/model` | 模型名称 | 切换模型 |
| `/thinking` | collapsed/expanded/hidden | 设置思考展示模式 |

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
- [ ] 多用户并发安全 — 多用户同时会话可能互相影响（register_display 全局覆盖、MODEL_CONFIG 全局状态）
- [ ] 多模态输入 — 支持图片/PDF 作为输入
