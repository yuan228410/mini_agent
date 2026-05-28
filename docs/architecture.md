# Architecture

## 主循环 (main.py)

```
用户输入 → /save /load /sessions? → 处理会话命令
         → run_tool_loop(LLM, 过滤后工具) → 有 tool_calls? → 并行/串行执行工具 → 再次 chat
                                          ↓ 无
                                    wait_for_teammates? → Event 等待队友回禀 → 收到 → 注入对话 → 再次 chat
                                    ↓ 无活跃队友
                               输出回复 → 存储 → 检查压缩
```

主循环位于 `src/mini_ai/main.py`，是 Agent 的顶层编排器。每轮用户输入后：

1. **会话命令预处理** — 检查输入是否为 `/save`、`/load`、`/sessions` 等会话管理命令，优先处理
2. **工具循环 (run_tool_loop)** — 将用户消息和过滤后的工具列表送入 LLM，解析响应
3. **工具调用分发** — 如果 LLM 返回 `tool_calls`，根据工具依赖关系并行或串行执行，执行结果再次送入 LLM 继续对话
4. **队友等待** — 如果没有工具调用但存在活跃队友 (Team)，通过 `threading.Condition` Event 等待队友回禀，收到后注入对话并再次进入工具循环
5. **输出回复** — 无工具调用且无活跃队友时，将 LLM 回复渲染输出到终端/Web，存入历史记录
6. **压缩检查** — 检查当前 prompt token 是否超过阈值，若超过则触发记忆压缩

---

## 多模型支持

通过 `config.yaml` 中的 `active_model` 字段切换模型，每个模型独立配置 API 地址、密钥、上下文长度和协议模式：

```yaml
active_model: claude        # 切换为 glm 即可用另一个模型
models:
  claude:
    api_mode: anthropic     # 支持 openai / anthropic 两种协议
    api_url: https://...
    api_key: sk-...
    model: Claude Opus 4.7
    context_length: 200000
    temperature: 0.3        # 可选：采样温度，越低越确定，越高越随机
    # max_tokens: 8192      # 可选：单次回复最大 token 数
    # top_p: 0.9            # 可选：核采样概率阈值
  glm:
    api_mode: openai
    api_url: https://...
    model: glm-5.1
    context_length: 200000
    reasoning_effort: high  # 可选：推理等级（OpenAI o 系列：low/medium/high）
```

- 运行时可通过 `/model <名称>` 命令切换模型，立即生效并持久化到 `config.yaml`
- `/model` 命令列出所有可用模型，补全列表自动包含模型名
- LLM 通信层位于 `src/mini_ai/llm/`，分 `openai.py`（OpenAI 协议）和 `anthropic.py`（Anthropic Claude 协议），共享基础设施在 `base.py`

### 设计决策

| 决策 | 说明 |
|------|------|
| `requests.Session()` 长连接 | 复用 HTTP 连接，避免每次请求重新 TLS 握手 |
| `threading.local()` token 统计 | 各线程独立 `_local.last_usage`，解决 lead/队友并发统计竞态 |
| `tools` 参数三态 | `True`=全部工具，`list[dict]`=指定工具列表，`False`=无工具 |
| 失败重试 | `llm_retries` 次，递增延迟 `llm_retry_delay` × attempt |
| 容错 | `RequestException` 和 JSON 解析失败统一返回 `None`，不抛异常 |
| token 估算 | API 未返回 `usage` 时按内容长度自动估算（字符数 / 3） |

---

## 流式输出

在 `config.yaml` 中配置 `streaming: true` 后，LLM 文本回复逐字输出到终端，而工具调用仍走批量模式（一次性返回所有 tool_calls）。

- **Anthropic 协议**：通过 `thinking` content block + `thinking_delta` 流式块支持思维链流式输出
- **OpenAI 协议**（含 DeepSeek 等兼容模型）：通过 `reasoning_content` 字段 + 流式 `delta.reasoning_content` 支持

流式输出时，文本逐字打印，完成后清除纯文本并重新渲染为 Rich Markdown 格式（标题、列表、代码块、粗体等）。

---

## 终端 UI

基于 Rich + prompt_toolkit 的终端渲染层，位于 `src/mini_ai/cli/display.py`，统一管理所有用户可见输出。

### Markdown 渲染

流式输出时逐字打印纯文本，完成后清除并重渲为 Rich Markdown（支持标题、列表、代码块、粗体等）。

### 思维链三种模式

通过 `/thinking <mode>` 命令切换：

| 模式 | 终端显示 | 查看详情 |
|------|----------|----------|
| `collapsed`（默认） | `💭 已思考 N 字 (Xs)` | `/thinking` 展开查看 |
| `expanded` | 实时输出思考内容 + `💭 思考完毕 (Xs)` | 直接可见 |
| `hidden` | 不显示 | `/thinking` 仍可查看 |

**多协议支持：**
- Anthropic 协议：`thinking` content block + `thinking_delta` 流式块
- OpenAI 协议（DeepSeek 等）：`reasoning_content` 字段 + 流式 `delta.reasoning_content`

### 工具调用三种粒度

`display.tool_detail` 配置：

| 粒度 | 显示内容 |
|------|----------|
| `summary`（默认） | 工具名 + 参数摘要(80字) + 结果预览(200字) + 耗时 |
| `minimal` | 工具名 + 耗时 |
| `full` | 工具名 + 完整参数 + 完整结果 + 耗时 |

### 命令补全

输入 `/` 自动弹出命令菜单，方向键选择，Tab 确认。特定命令支持二级补全：
- `/skill ` 弹出技能名列表
- `/model ` 弹出模型名列表

### 状态栏

每轮对话结束后右对齐显示：

```
⚙ 模型 │ 上下文用量% │ ↑输入 ↓输出 token │ 系统提示词大小 │ 消息数
```

- 上下文用量超过 70% 变黄，超过 85% 变红
- Web 端状态栏实时更新
- Token 统计优先使用 API 返回值，API 未返回时按内容长度自动估算

### 启动界面

启动时显示 `mini ai` ASCII banner（bold green + bold yellow），输入提示符为 `mini-ai> `。

### 自动加载项目规范

如果当前工作目录存在 `CLAUDE.md` 或 `AGENTS.md`，自动读取并加入系统提示词作为项目规范（优先加载 `CLAUDE.md`）。

---

## 工具系统

位于 `src/mini_ai/tools/`，采用 `ToolRegistry` 类统一管理注册、分发、并行执行和结果截断。

### ToolRegistry 注册模式

每个工具是一个独立模块，导出三个接口：

- `definition` — OpenAI Function Calling 的工具定义（JSON Schema）
- `execute(args)` — 工具执行函数
- `configure(**kwargs)` — （可选）注入外部依赖，避免模块级可变赋值

添加新工具只需在 `tools/` 目录下创建新模块，通过 `ToolRegistry` 注册即可。

### 每个内置工具

| 工具 | 说明 |
|------|------|
| `run_command` | 执行 Shell 命令，返回 stdout/stderr，支持 timeout 和 cwd |
| `web_fetch` | 抓取网页内容，自动清洗 HTML（跳过 style/script/svg/head，压缩空白） |
| `read_file` | 读取文件内容，支持行号范围筛选 |
| `write_file` | 写入文件，自动创建父目录，支持 append 模式 |
| `edit_file` | 部分编辑（search-and-replace 模式） |
| `search_files` | 文件搜索（grep + glob） |
| `list_dir` | 目录列表 |
| `list_skills` | 列出可用技能 |
| `load_skill` | 加载技能内容 |
| `install_skill` | 安装技能（压缩包 URL/本地路径 或 内联内容） |
| `update_todos` | 任务规划与状态跟踪 |
| `dispatch_subagent` | 派遣子代理执行任务 |
| `remember` | 主动写入长期记忆 |
| `recall` | 检索长期记忆 |
| `forget` | 删除过期记忆 |
| `search_history` | 历史搜索（SQLite FTS5 全文搜索） |
| `spawn_teammate` | 召入/唤醒持久队友 |
| `send_message` | 给队友或 lead 发送 inbox 消息（支持 P2P） |
| `broadcast` | 向所有队友广播消息 |
| `dismiss_team` | 解散所有活跃队友 |
| `blackboard_write` | 向共享黑板写入数据 |
| `blackboard_read` | 从共享黑板读取数据 |
| `blackboard_list` | 列出黑板上的 key |
| `run_workflow` | 提交 DAG 工作流并执行 |
| `workflow_status` | 查看工作流执行状态 |
| `load_workflow` | 加载预定义 YAML 工作流模板 |

### 关键机制

**结果截断**：工具输出超过 `max_result_chars` 时自动截断，防止上下文膨胀。

**并行执行**：无依赖的工具调用通过 `ThreadPoolExecutor` 并行运行，提升执行效率。

**contextvars 传递**：并行线程中通过 `copy_context()` 保持 `team_caller` 身份，确保多 Agent 场景下调用链路正确。

---

## 日志系统

位于 `src/mini_ai/logger.py`，采用双输出策略：

| 输出目标 | 级别 | 内容 |
|----------|------|------|
| **终端** | WARNING+ | 仅显示 WARNING 及以上级别消息和 `print` 输出（回禀通知、Assistant 回复等） |
| **文件** | DEBUG | 全量记录 `logs/YYYYMMDD.log`（按日期轮转），格式含进程/线程 ID (`[PID/TID]`) |

**记录内容：** LLM 请求响应、工具调用与返回、MSG 通信、队友状态等所有事件。

**防膨胀机制：** 工具结果只记录名称和长度，不打印内容，避免日志文件过度膨胀。

---

## 上下文组装

位于 `src/mini_ai/context.py`，按以下优先级拼接系统提示词：

```
SOUL.md (核心身份)
---
长期记忆 (MemoryStore)
---
用户画像 (MemoryStore)
---
可用技能 (SkillLoader)
---
CLAUDE.md / AGENTS.md (项目规范，自动读取当前目录)
---
RULES.md (行为规范)
```

组装后的 system prompt 包含了 Agent 人设、跨对话记忆、用户画像、可用技能列表、项目规范和行为约束，确保 Agent 在每轮对话中拥有完整的上下文认知。

---

## 工作空间

工作空间按项目隔离记忆、会话、历史数据。

### CLI / Web 隔离方式

| 模式 | 工作空间绑定方式 |
|------|------------------|
| **CLI** | 自动绑定：在哪个目录运行 `mini-ai`，该目录名即为工作空间名（自动创建） |
| **Web** | 手动管理：通过顶栏 📂 按钮打开工作空间面板进行操作 |

### 存储结构

每个工作空间独立存储在 `~/.mini_ai/workspaces/<name>/`：

```
workspaces/<name>/
├── workspace.yaml        # 元数据（关联项目路径）
├── memory_data/          # 记忆 + 历史 DB + 会话
│   ├── history.db        #   SQLite 历史（FTS5 全文搜索）
│   ├── MEMORY.md         #   长期记忆
│   ├── USER.md           #   用户画像
│   └── sessions/         #   命名会话
└── .team/                #   Team 协作数据
```

### 支持的操作

| 命令 | 说明 |
|------|------|
| `/workspace` | 列出所有工作空间 |
| `/workspace new <名称> [路径]` | 创建新工作空间 |
| `/workspace add <路径>` | 添加现有文件夹为工作空间 |
| `/workspace remove <名称>` | 移除工作空间（保留数据） |
| `/workspace delete <名称>` | 删除工作空间（含数据） |

---

## 任务规划

通过 `update_todos` 工具实现。模型收到复杂任务后自动拆解为待办列表：

### 三态推进

```
pending → in_progress → completed
```

- 同一时间最多一个任务处于 `in_progress` 状态
- 每次调用全量覆盖更新待办列表
- 状态信息注入系统提示词，压缩不会丢失

---

## 自定义 Agent 人设

位于 `src/mini_ai/character/` 目录，包含两个核心文件：

| 文件 | 用途 |
|------|------|
| `SOUL.md` | 核心身份、能力、工作流程定义 |
| `RULES.md` | 行为规范约束 |

这两个文件在上下文组装时被注入 system prompt 的顶部和底部，形成 Agent 人格的基础框架。用户可通过修改这两个文件自定义 Agent 的行为风格和规则约束。

---

## 技能系统

位于 `src/mini_ai/skills.py`，负责技能的加载、搜索和管理。

### 技能存储格式

技能存放在 `skills/<名称>/SKILL.md`，使用 YAML frontmatter 定义元数据：

```markdown
---
name: skill-name
description: 简短描述
tags: tag1,tag2
---
技能正文（Markdown 格式）
```

### 加载与搜索路径

- **默认搜索路径**：`~/.mini_ai/skills/`
- **额外搜索路径**：可在 `config.yaml` 中配置额外只读路径
- **同名优先**：主目录 `~/.mini_ai/skills/` 中的技能优先级最高
- **安装路径**：通过 `install_skill` 安装的技能始终存入主目录

### 技能相关命令

| 命令 | 说明 |
|------|------|
| `/skill` | 列出所有可用技能 |
| `/skill <名称>` | 加载并使用指定技能 |
| `/genskill <名称>` | 从当前对话总结生成技能 |

模型通过 `load_skill` 工具按需加载技能内容，通过 `install_skill` 安装新技能。LLM 也会在对话中产生可复用经验时主动建议保存为技能。

---

## 计划模式

通过 `/plan` 和 `/act` 命令切换对话模式：

| 模式 | 说明 | 切换命令 |
|------|------|----------|
| **执行模式**（默认） | Agent 可自由调用工具、执行任务 | `/act` |
| **计划模式** | Agent 只输出分析和步骤规划，不调用任何工具 | `/plan` |

### 工作机制

- 计划模式下 Agent 收到空工具列表，自然只输出文本分析和步骤规划
- `plan.approval: true`（默认）：计划输出后暂停，用户确认 `/act` 后执行
- `plan.approval: false`：计划输出后自动切换执行模式

### UI 表现

| 元素 | 执行模式 | 计划模式 |
|------|----------|----------|
| CLI 提示符 | `mini-ai>` | `mini-ai 📋>` |
| Web 状态栏 | ⚡ 执行模式 | 📋 计划模式 |

### 配置

```yaml
plan:
  approval: true    # 计划模式下是否需要用户审批后才能执行
```

---

## MCP 协议支持

支持连接 MCP（Model Context Protocol）服务器，自动获取远程工具并注册到工具系统。

### 传输协议

- **stdio**：本地进程通信
- **streamable_http**：远程服务通信

### 工具注册

MCP 工具自动注册到 ToolRegistry，命名格式为 `mcp_<服务器名>_<工具名>`，与内置工具统一调度。

### 配置示例

```yaml
mcp:
  enabled: true
  connect_timeout: 10
  execute_timeout: 60
  sse_read_timeout: 120
  servers:
    memory:
      type: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-memory"]
    search:
      type: streamable_http
      url: https://mcp.example.com/sse
      headers:
        Authorization: Bearer xxx
      disabled: true  # 可选，跳过此服务器
```

### 关键机制

- **多服务器**：支持配置多个 MCP 服务器，每个独立连接，工具名自动加 `mcp_<服务器名>_` 前缀避免冲突
- **启动连接**：启动时连接所有已启用服务器，关闭时断开
- **容错**：连接失败跳过该服务器，不影响其他工具和服务器
- **超时处理**：调用超时返回错误信息，不阻塞主循环
- **优雅中断**：生成/工具执行中 Ctrl+C 优雅中断，回到输入提示符，不退出程序
- **异步桥接**：MCP SDK 异步调用通过后台 event loop 桥接到同步 ToolRegistry
- **查看状态**：`/mcp` 命令查看已连接服务器和工具列表
- **依赖**：需要安装 `mcp` 包（`pip install mcp`）

---

## web_fetch 智能清洗

位于 `src/mini_ai/tools/web_fetch.py`。

**问题：** 原始 HTML 包含大量 CSS/JS/SVG，传给 LLM 浪费大量 token。

**解决方案：** `_TextExtractor`（HTMLParser 子类）在解析时跳过无用标签：

- 跳过 `<style>`、`<script>`、`<noscript>`、`<svg>`、`<head>` 整个标签树
- `_skip_depth` 跟踪跳过深度，避免误恢复
- `_collapse_ws()` 将连续空白压缩为单个空格
- 效果：典型网页从数万字符压缩到几千字符

---

## Agent 执行器 (runner.py)

位于 `src/mini_ai/runner.py`。

**定位：** `run_tool_loop()` 是统一的 Agent 执行循环，被主循环、子代理、队友、Web 端复用。

```python
def run_tool_loop(
    messages, tools, *,
    streaming=False, display=None, inject_fn=None,
    abort_event=None, max_turns=20,
    context_length=None, context_usage_limit=0.88, ctx=None,
) -> tuple[dict | None, bool]:
```

**设计决策：**

- **流式/非流式统一**：同一条代码路径处理 `streaming=True/False`，仅在流式时逐 chunk yield 到 display
- **display 渲染**：流式 chunk 实时渲染到终端/Web，非流式等待完整返回后一次性渲染
- **abort 中断**：每轮循环检查 `abort_event.is_set()`，支持 Web 端中断生成
- **上下文安全阀**：`prompt_tokens > context_length × context_usage_limit` 时提前退出
- **错误熔断**：连续 3 次工具调用返回 Error → 提前退出，避免 LLM 空循环
- **轮次上限**：`max_turns`（默认 20）轮后强制退出，防止无限循环
- **返回元组**：`(final_msg, spawned)` — `spawned` 标记是否有新队友被 spawn，主循环据此进入队友等待

`run_agent()` 作为轻量包装，供子代理/队友内部调用，返回最终文本字符串。

---

## 关键设计原则

1. **模块化** — 一个文件一个职责，接口简单（`definition` + `execute`）
2. **工具白名单** — 子代理和队友有独立的工具访问权限，lead 按需过滤工具（排除 `read_inbox`/`list_teammates`）
3. **上下文隔离** — 子代理/队友的对话历史不回传主循环
4. **容错优先** — 并行工具单点异常不传染，队友超时有通知，LLM 请求自动重试
5. **文件持久化** — 邮箱和记忆均基于文件，零外部依赖
6. **依赖注入** — 工具通过 `configure(**kwargs)` 注入外部依赖，避免模块级可变赋值
7. **LLM 驱动压缩** — 用模型自身智能提取记忆，`prompt_tokens` 占比精准触发
8. **零浪费轮询** — inbox 读取由代码层自动处理，不暴露给 LLM 避免空轮询
9. **Event 驱动唤醒** — `threading.Event` 替代 sleep 轮询，有消息 0ms 响应
10. **多模型可插拔** — `active_model` 一键切换，`api_mode` 适配不同协议，零代码改动
11. **Web/CLI 双模式** — 同一套 LLM/工具/记忆逻辑，仅 Display 层不同；Web 模式同步代码在线程池运行，`RequestContext` 实现多用户并发隔离

---

> **子代理系统、Team 协作系统、记忆系统、会话管理的详细说明见各自的独立文档：**
> - [记忆系统](memory-system.md)
> - [多 Agent 编排](team-collaboration.md)

---

## 项目结构总览
├── pyproject.toml             # 项目配置（uv/pip 安装，入口 mini-ai）
├── config.example.yaml        # 配置模板
├── docs/                      # 设计文档
├── examples/                  # 功能示例
├── src/mini_ai/             # 包源码
│   ├── __init__.py            # 包定义
│   ├── __main__.py            # python -m mini_ai 入口
│   ├── main.py                # 主循环编排
│   ├── config.py              # 配置加载（DATA_DIR / PACKAGE_DIR 分离）
│   ├── runner.py              # 统一 Agent 执行循环（run_tool_loop）
│   ├── context.py             # 系统提示词组装
│   ├── workspace.py           # 工作空间管理
│   ├── skills.py              # 技能加载器
│   ├── logger.py              # 日志模块
│   ├── llm/                   # LLM 通信层
│   │   ├── base.py            # 共享基础设施
│   │   ├── openai.py          # OpenAI 协议
│   │   └── anthropic.py       # Anthropic 适配层
│   ├── cli/                   # CLI 交互层
│   │   ├── display.py         # 终端 UI 渲染
│   │   └── commands.py        # 斜杠命令处理
│   ├── memory/                # 记忆系统
│   │   ├── store.py           # 记忆存储
│   │   ├── compactor.py       # 对话压缩归档
│   │   ├── history_db.py      # 历史搜索
│   │   └── session.py         # 会话管理
│   ├── character/             # Agent 人设
│   │   ├── SOUL.md            # 核心身份
│   │   └── RULES.md           # 行为规范
│   ├── tools/                 # 工具系统
│   │   ├── __init__.py        # ToolRegistry
│   │   ├── run_command.py     # Shell 命令执行
│   │   ├── web_fetch.py       # 网页抓取
│   │   ├── read_file.py       # 文件读取
│   │   ├── write_file.py      # 文件写入
│   │   ├── edit_file.py       # 部分编辑
│   │   ├── search_files.py    # 文件搜索
│   │   ├── list_dir.py        # 目录列表
│   │   ├── list_skills.py     # 技能列表
│   │   ├── load_skill.py      # 技能加载
│   │   ├── install_skill.py   # 技能安装
│   │   ├── update_todos.py    # 任务规划
│   │   ├── dispatch_subagent.py # 子代理调度
│   │   ├── team_tools.py      # Team 工具
│   │   ├── blackboard_tools.py # 黑板工具
│   │   ├── workflow_tools.py  # 工作流工具
│   │   ├── memory_tools.py    # 主动记忆
│   │   └── history_tools.py   # 历史搜索
│   └── subagents/             # 子代理定义
│       ├── __init__.py        # SubagentLoader
│       ├── coder.md           # 代码工程师
│       └── researcher.md      # 信息检索员
└── ~/.mini_ai/                # 运行时数据目录
    ├── config.yaml            # 用户配置
    ├── skills/                # 用户技能
    ├── workflows/             # 工作流模板
    ├── logs/                  # 运行日志
    └── workspaces/            # 工作空间
```
