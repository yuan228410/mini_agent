# mini_ai 设计文档

## 概述

mini_ai 是一个基于 OpenAI / Anthropic Chat API 的智能对话 Agent，支持多模型切换、流式输出、工具调用、三层记忆压缩、子代理派遣、Team 协作、会话管理。

## 架构全景

```
┌──────────────────────────────────────────────────────────────┐
│                         main.py                              │
│  初始化 → 加载上下文 → 恢复历史/会话 → 主循环                    │
│     │                                                         │
│     ├── config.py (多模型切换 + 统一配置)                       │
│     ├── context.py (SOUL + 记忆 + 技能 + RULES)                │
│     ├── memory.py (三层存储)                                   │
│     ├── compactor.py (压缩归档)                                │
│     ├── session.py (会话保存/恢复)                              │
│     ├── skills.py (技能加载)                                   │
│     ├── subagents/ (子代理定义)                                │
│     ├── team_bus.py (队友邮箱 + Event 唤醒)                    │
│     ├── team_manager.py (队友管理)                             │
│     └── team_loop.py (回禀等待 + 自动 shutdown + 清理)          │
│                                                               │
│  主循环:                                                       │
│    User Input → /save /load /sessions?                        │
│              → _run_tool_loop(LLM, 过滤后工具)                 │
│                  ├─ tool_calls → tools/__init__.py 分发        │
│                  │   ├── run_command.py                        │
│                  │   ├── web_fetch.py (智能 HTML 清洗)         │
│                  │   ├── read_file.py / write_file.py          │
│                  │   ├── update_todos.py                       │
│                  │   ├── list_skills.py / load_skill.py        │
│                  │   ├── dispatch_subagent.py ──→ runner.py    │
│                  │   └── team_tools.py (5个team工具)           │
│                  └─ no tool_calls → wait_for_teammates?        │
│                      ├─ 有活跃队友 → Event 等待 → 回禀注入     │
│                      └─ 无 → 输出 → 存储 → shutdown → 压缩     │
└──────────────────────────────────────────────────────────────┘
```

---

## 模块设计

### 1. LLM 通信 (llm.py + anthropic.py)

**职责：** 对 OpenAI / Anthropic Chat API 的统一封装，支持流式输出。

**设计决策：**
- 使用 `requests.Session()` 维护长连接，避免每次请求重新 TLS 握手
- `threading.local()` 隔离各线程 token 用量，`_get_usage()` 获取当前线程数据，解决 lead/队友并发统计竞态
- `tools` 参数三态：`True`=全部工具，`list[dict]`=指定工具定义列表，`False`=无工具
- 失败重试：`llm_retries` 次，间隔 `llm_retry_delay` 秒
- `RequestException` 和 JSON 解析失败统一返回 None

**双协议适配：**
- `llm.py` — OpenAI Chat Completions API（`/v1/chat/completions`）
- `anthropic.py` — Anthropic Messages API（`/v1/messages`），签名对齐 `chat()` / `chat_stream()`
- `config.yaml` 的 `api_mode: openai | anthropic` 自动选择协议层

```python
def chat(messages, tools=True) -> dict | None          # 批量模式
def chat_stream(messages, tools=True) -> Generator     # 流式模式，yield chunk dict
```

### 2. 工具系统 (tools/)

**注册模式：** 模块级 `definition` + `execute(args)` 接口，`_ALL_TOOLS` 统一管理。

**注册流程：**
```
main.py
  ├── register(skill_loader)      → skill 工具
  ├── register_subagents(loader)   → dispatch_subagent
  └── register_team(bus, manager)  → 5个 team 工具
```

**并行策略：**
- `_PARALLEL_TOOLS` 集合标记可并行工具（dispatch_subagent, spawn_teammate）
- 连续的可并行调用通过 `ThreadPoolExecutor` + `as_completed` 并发执行
- 单个任务异常不影响同组其他任务（try/except 包裹）
- 并行线程中通过 `_cv.copy_context()` 保持 `team_caller` contextvars 身份

**结果截断：**
- `_truncate()` 函数统一处理，超过 `TOOL["max_result_chars"]` 时截断并追加 `[已截断，原长 N 字符]`
- 串行（`_execute_one`）和并行（`_execute_parallel`）均经过截断
- 防止 web_fetch、run_command 等工具的大输出膨胀上下文

**工具清单：**

| 工具 | 模块 | 并行 | 用途 |
|------|------|------|------|
| run_command | run_command.py | ❌ | Shell 命令执行 |
| web_fetch | web_fetch.py | ❌ | 网页内容抓取（智能 HTML 清洗） |
| read_file | read_file.py | ❌ | 读取文件内容（支持行号范围） |
| write_file | write_file.py | ❌ | 写入文件（自动创建父目录） |
| update_todos | update_todos.py | ❌ | 任务规划三态推进 |
| list_skills | list_skills.py | ❌ | 列出可用技能 |
| load_skill | load_skill.py | ❌ | 按需加载技能 |
| dispatch_subagent | dispatch_subagent.py | ✅ | 派遣子代理 |
| spawn_teammate | team_tools.py | ✅ | 召入持久队友 |
| list_teammates | team_tools.py | ❌ | 列出队友状态 |
| send_message | team_tools.py | ❌ | 发 inbox 消息 |
| read_inbox | team_tools.py | ❌ | 读取并清空 inbox（队友内部自动调用，lead 不暴露） |
| broadcast | team_tools.py | ❌ | 广播给所有队友 |

**Lead 工具过滤：**
- `_lead_tool_defs()` 缓存过滤后的工具列表，排除 `read_inbox` 和 `list_teammates`
- 防止 LLM 使用 read_inbox 空轮询或用 list_teammates 替代等待

### 3. 子代理系统 (subagents/ + dispatch_subagent.py)

**定位：** 一次性、无状态的独立任务执行器。

**执行流程：**
```
dispatch_subagent.execute(type, task)
  → 查找 subagent spec
  → 构建 [system(spec.prompt), user(task)]
  → rumini_ai(messages, max_turns, tool_names=whitelist, context_length=...)
  → 返回最终文本结果
```

**关键设计：**
- 工具白名单：子代理只能使用 `spec.tool_names` 中列出的工具
- 上下文隔离：子代理内部对话历史不回传，只返回结果摘要
- 并行执行：多个 dispatch_subagent 通过 ThreadPoolExecutor 并发
- 轮次上限：`spec.max_turns` 防止无限循环
- 上下文安全阀：`prompt_tokens > context_length × context_usage_limit` 时提前退出并返回 None

**子代理定义格式 (subagents/*.md)：**
```markdown
---
name: coder
description: 代码工程师
tools: run_command, load_skill
max_turns: 10
---
作为系统提示词的正文...
```

### 4. Team 协作系统 (team_bus.py + team_manager.py + team_loop.py + tools/team_tools.py)

**定位：** 持久、可交互的角色协作系统。

**核心组件：**
- **MessageBus（team_bus.py）：** 基于文件 JSONL 的邮箱系统 + Event 唤醒 + inbox 容量限制
- **TeammateManager（team_manager.py）：** 队友生命周期管理（spawn / 状态追踪 / 线程循环）
- **Team 轮询（team_loop.py）：** lead 侧回禀等待、消息过滤、自动 shutdown、inbox 清理
- **Team Tools（tools/team_tools.py）：** 5 个工具暴露给 LLM 操作 team

**消息流转：**
```
Lead (主循环)                          Teammate (独立线程)
    │                                        │
    ├─ spawn_teammate ────────────────────→ 启动线程
    │                                        │
    ├─ send_message → bus.send() ──→ inbox/name.jsonl
    │                         └─→ Event.set() (唤醒收件人)
    │                                        │
    │                                        ├─ loop top: bus.read_inbox(name)
    │                                        ├─ rumini_ai (执行任务)
    │                                        └─ send_message → lead inbox
    │                                               └─→ lead_event.set()
    │                                        │
    └─ lead_event.wait(timeout=2s) ←────── 0ms 唤醒
       └─ poll_inbox → 过滤 → 注入对话 → LLM
```

**队友生命周期：**
```
init (offline) → spawn → working → idle → ...
                          │
                          └── shutdown_request → shutdown (exit)
```

**设计决策：**

| 决策 | 原因 |
|------|------|
| 文件邮箱 | 进程重启不丢消息，零外部依赖 |
| Event 唤醒 | `bus.send()` 写入后 `Event.set()`，`wait(timeout)` 零延迟响应，替代 sleep 轮询 |
| contextvars 身份识别 | 队友线程 `set_caller(name)` 标记身份，工具自动使用正确发送者 |
| `copy_context()` 并行保序 | ThreadPoolExecutor 中 `_cv.copy_context().run()` 保持 team_caller 上下文 |
| Lead 工具过滤 | 排除 `read_inbox`/`list_teammates`，防止 LLM 空轮询浪费 token |
| 队友不暴露 read_inbox | `_teammate_loop` 顶部代码层读取 inbox，LLM 不感知 |
| 消息过滤 | shutdown_response 自动忽略；短消息（<30字符且无结果关键词）静默丢弃 |
| 自动 shutdown | 每轮对话结束 `shutdown_teammates()` 关闭所有 idle/working 队友 |
| inbox 清理 | `cleanup_inbox()` 延迟 0.5s 读取并丢弃残留的 shutdown_response |
| inbox 容量限制 | `bus.send()` 检查收件人 inbox 文件大小，超过 100KB 拒绝写入 |
| 上下文重置 | 每轮任务完成后 `messages = [messages[0]]`，防止无限增长 |
| 上下文安全阀 | 队友 `prompt_tokens > context_length × 88%` 时自动终止并回禀 |
| `threading.local()` | 各线程独立 token 统计，避免 lead/队友并发读写竞态 |
| 数量限制 | `config.yaml` 的 `teammate.max_teammates` 控制，默认 10 |

**回禀等待流程（team_loop.py）：**
```python
def wait_for_teammates(bus, team_mgr, lead_event, run_loop_fn, messages, tools, inject_fn, store):
    while waited < lead_wait:
        lead_event.clear()
        lead_event.wait(timeout=lead_poll_interval)    # Event 等待，0ms 或超时
        inbox_text = poll_inbox(bus)                    # 过滤后返回
        if inbox_text:
            messages.append(inbox_text)
            last_msg = run_loop_fn(...)                 # LLM 循环处理回禀
        if not has_active_teammates(team_mgr):          # 无论 inbox 是否有消息都检查
            break
        if inbox_text:
            waited = 0                                  # 有实质消息才重置计时器
    return last_msg
```

**子代理 vs 队友 选型：**

| 维度 | dispatch_subagent | spawn_teammate |
|------|-------------------|----------------|
| 生命周期 | 一次性，完即销毁 | 持久，可多轮交互 |
| 通信 | 无，只返回最终结果 | 双向 inbox 通信 |
| 并行 | ✅ `_PARALLEL_TOOLS` | ✅ `_PARALLEL_TOOLS` |
| 上下文 | 隔离 | 隔离（每轮重置） |
| 适用场景 | 并行搜索、独立分析 | 编码+审查接力、多角色协作 |

### 5. 记忆系统 (memory.py + compactor.py)

**三层存储模型：**

| 层级 | 文件 | 格式 | 更新方式 |
|------|------|------|----------|
| 原始层 | `history.jsonl` | JSONL | 每条对话实时 append |
| 情景层 | `YYYY-MM-DD.md` | Markdown | 压缩时模型提取 `<episode>` |
| 长期层 | `MEMORY.md` | Markdown | 压缩时模型提取 `<updated_memory>` |
| 用户画像 | `USER.md` | Markdown | 压缩时模型提取 `<updated_user>` |

**压缩触发条件：**

`prompt_tokens > context_length × context_usage_threshold`

即：当 LLM 请求的 prompt_tokens 超过模型上下文窗口的指定比例时触发。由 `_get_usage()` 获取当前线程的最近一次请求 token 用量。

**压缩后保留：**
- 最近 `keep_recent` 条消息（默认 50 条）
- 且保留消息总字符数不超过 `char_threshold`（超出则从头部继续裁剪）

**压缩流程：**
1. 根据上述条件确定归档区间和保留区间
2. 旧消息 + 当前记忆/画像/情景 → 发送给 LLM 提取结构化输出
3. 更新三层存储 + 在 history.jsonl 写入 compact_event 标记
4. 合并最新长期记忆/用户画像到 system prompt

**历史恢复：**
- `load_unarchived` 从最后一个 compact_event 之后读取未归档消息
- 启动时自动恢复上次会话的上下文

### 6. 会话管理 (session.py)

**职责：** 命名会话的保存、加载、列表。

**命令：**

| 命令 | 说明 |
|------|------|
| `/save <名称>` | 保存当前对话为命名会话（跳过 system 消息） |
| `/load <名称>` | 加载已保存的会话，恢复上下文（保留当前 system prompt） |
| `/sessions` | 列出所有已保存会话（标记当前） |

**存储格式：** `memory_data/sessions/<名称>.jsonl`，每行一条消息 JSON，首条含 `ts` 时间戳。

### 7. 上下文组装 (context.py)

**拼接顺序（优先级从高到低）：**
```
SOUL.md        (核心身份与能力)
  ---
长期记忆        (MemoryStore)
  ---
用户画像        (MemoryStore)
  ---
可用技能        (SkillLoader)
  ---
RULES.md       (行为规范)
  ---
当前任务计划    (TodoStore, 运行时注入)
```

### 8. 任务规划 (update_todos)

**TodoStore：** 独立于对话历史的持久状态存储。

**三态推进：**
```
pending → in_progress → completed
```

**约束：**
- 同一时间最多 1 个 in_progress
- 每次全量覆盖更新
- 状态跟随 TodoStore 实例，压缩不丢失
- `_inject_todos()` 将当前计划注入系统提示词末尾

### 9. 配置系统 (config.py + config.yaml)

**多模型切换：** `active_model` 指定当前模型，`models` 下定义多个模型配置，每个模型独立设置 API 地址、密钥、协议模式和上下文长度。

```yaml
active_model: claude
models:
  claude:
    api_mode: anthropic
    api_url: ...
    context_length: 200000
  glm:
    api_mode: openai
    api_url: ...
    context_length: 200000
```

**配置项一览：**

| 分组 | 参数 | 默认值 | 消费模块 |
|------|------|--------|----------|
| — | `streaming` | false | main.py |
| — | `active_model` | — | config.py |
| models.* | `api_mode` | openai | llm.py / anthropic.py |
| models.* | `api_url` | — | llm.py / anthropic.py |
| models.* | `api_key` | — | llm.py / anthropic.py |
| models.* | `model` | — | llm.py / anthropic.py |
| models.* | `context_length` | 128000 | compactor / runner / team_manager |
| timeouts | `llm` | 120 | llm.py / anthropic.py |
| timeouts | `llm_retries` | 3 | llm.py |
| timeouts | `llm_retry_delay` | 2 | llm.py |
| timeouts | `teammate_recv` | 5 | team_manager.py |
| timeouts | `lead_wait` | 1800 | team_loop.py |
| timeouts | `lead_poll_interval` | 2 | team_loop.py |
| timeouts | `web_fetch` | 30 | tools/web_fetch.py |
| compactor | `context_usage_threshold` | 0.8 | compactor.py |
| compactor | `keep_recent` | 50 | compactor.py |
| compactor | `char_threshold` | 20000 | compactor.py |
| teammate | `max_teammates` | 10 | team_manager.py |
| teammate | `max_turns` | 20 | team_manager.py |
| teammate | `base_tools` | [3项] | team_manager.py |
| tool | `max_result_chars` | 8000 | tools/__init__.py |
| runner | `context_usage_limit` | 0.88 | runner.py |

**设计决策：** 所有运行时参数集中在 `config.yaml`，避免硬编码散落各文件，修改无需改代码。

### 10. 日志系统 (logger.py)

**双输出策略：**
- 终端：WARNING+ — 仅错误和重要提示
- 文件 `logs/YYYYMMDD.log`：DEBUG — 全量记录，格式 `[时间 [级别] [PID/TID] 消息]`

**信息分级原则：**
- INFO — 工作流链路（LLM 请求/响应、工具调用、MSG 通信、队友状态变化）
- DEBUG — 详细数据（消息摘要、工具参数、inbox 内容）
- WARNING+ — 异常和边界情况
- `print` — 用户可见的 UI 输出（回禀通知、Assistant 回复、等待提示）

**防膨胀：**
- 工具结果只记录名称和长度，不打印内容
- LLM 请求日志只保留最近 2 条非工具消息摘要，跳过 `[tool]` 角色消息

### 11. Agent 执行器 (runner.py)

**可复用的对话循环：** 被主循环、子代理、队友三者复用。

```python
def rumini_ai(messages, max_turns=10, tool_names=None, context_length=None) -> str | None
    for _ in range(max_turns):
        if prompt_tokens > context_length * context_usage_limit:
            logger.warning("[上下文] 接近上限，提前退出")
            return None
        msg = chat(messages, tools=filtered_defs)
        if not msg or "tool_calls" not in msg:
            return msg.content
        handle_tool_calls(msg, messages)
    return None
```

**设计决策：**
- 工具白名单通过 `tool_names` 参数传入，子代理和队友用不同的白名单
- 轮次上限防止无限循环
- 上下文安全阀：`prompt_tokens > context_length × context_usage_limit` 时提前退出
- `context_length` 由调用方传入（从 `MODEL_CONFIG` 获取），不硬编码
- 返回 None 时调用方负责容错处理

### 12. web_fetch 智能清洗 (tools/web_fetch.py)

**问题：** 原始 HTML 包含大量 CSS/JS/SVG，传给 LLM 浪费大量 token。

**解决方案：** `_TextExtractor`（HTMLParser 子类）在解析时跳过无用标签：
- 跳过 `<style>`、`<script>`、`<noscript>`、`<svg>`、`<head>` 整个标签树
- `_skip_depth` 跟踪跳过深度，避免误恢复
- `_collapse_ws()` 将连续空白压缩为单个空格
- 效果：典型网页从数万字符压缩到几千字符

### 13. Web 界面 (web/)

FastAPI + SSE 后端，Vue 3 + Vite 前端，`mini-ai --web` 启动。同一套 LLM/工具/记忆逻辑，仅 Display 层不同。

**关键设计：**
- WebDisplay 适配器实现与 CLI Display 相同接口，通过 `loop.call_soon_threadsafe()` 线程安全推入 asyncio.Queue
- 同步工具循环在 `run_in_executor()` 中执行，不阻塞事件循环
- 前端 Editorial 杂志编辑风，亮暗主题切换，Markdown + highlight.js 渲染

详细设计、组件架构、API 接口、SSE 协议等见 [WEB.md](WEB.md)。

---

## 关键设计原则

1. **模块化：** 一个文件一个职责，接口简单（`definition` + `execute`）
2. **工具白名单：** 子代理和队友有独立的工具访问权限，lead 按需过滤工具
3. **上下文隔离：** 子代理/队友的对话历史不回传主循环
4. **容错优先：** 并行工具单点异常不传染，队友超时有通知，LLM 请求自动重试
5. **文件持久化：** 邮箱和记忆均基于文件，零外部依赖
6. **依赖注入：** 工具模块通过 `configure(**kwargs)` 注入外部依赖，避免模块级可变赋值
7. **LLM 驱动压缩：** 用模型自身智能提取记忆，基于 prompt_tokens 占上下文窗口比例精准触发
8. **零浪费轮询：** inbox 读取由代码层自动处理，不暴露给 LLM 避免空轮询消耗 token
9. **Event 驱动唤醒：** 用 `threading.Event` 替代 sleep 轮询，有消息 0ms 响应，无消息低功耗等待
10. **多模型可插拔：** `active_model` 一键切换，`api_mode` 适配不同协议，零代码改动
11. **Web/CLI 双模式：** `--web` 参数切换，同一套 LLM/工具/记忆逻辑，仅 Display 层不同；Web 模式同步代码在线程池运行，通过 `call_soon_threadsafe` 线程安全推送 SSE 事件
