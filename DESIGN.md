# yzx_agent 设计文档

## 概述

yzx_agent 是一个基于 OpenAI Chat Completions API 的智能对话 Agent，支持工具调用、三层记忆压缩、子代理派遣、Team 协作。

## 架构全景

```
┌─────────────────────────────────────────────────────────┐
│                      main.py                            │
│  初始化 → 加载上下文 → 恢复历史 → 主循环                  │
│     │                                                    │
│     ├── context.py (SOUL + 记忆 + 技能 + RULES)           │
│     ├── memory.py (三层存储)                              │
│     ├── compactor.py (压缩归档)                           │
│     ├── skills.py (技能加载)                              │
│     ├── subagents/ (子代理定义)                           │
│     ├── team_bus.py (队友邮箱)                            │
│     ├── team_manager.py (队友管理)                        │
│     └── team_loop.py (队友轮询与回禀)                     │
│                                                          │
│  主循环:                                                  │
│    User Input → chat(LLM)                                │
│      ├─ tool_calls → tools/__init__.py 分发              │
│      │   ├── run_command.py                              │
│      │   ├── web_fetch.py                                │
│      │   ├── update_todos.py                             │
│      │   ├── list_skills.py / load_skill.py              │
│      │   ├── dispatch_subagent.py ──→ runner.py (子代理) │
│      │   └── team_tools.py (5个team工具)                 │
│      └─ no tool_calls → 输出 → store → 检查压缩          │
└─────────────────────────────────────────────────────────┘
```

---

## 模块设计

### 1. LLM 通信 (llm.py)

**职责：** 对 OpenAI Chat Completions API 的统一封装。

**设计决策：**
- 使用 `requests.Session()` 维护长连接，避免每次请求重新 TLS 握手
- 统一注入 `comate_custom_header` 用于百度内部网关认证
- `tools` 参数三态：`True`=全部工具，`list[dict]`=指定工具定义列表，`False`=无工具
- 120 秒超时，`RequestException` 和 JSON 解析失败统一返回 None

```python
def chat(messages, tools=True) -> dict | None
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

**工具清单：**

| 工具 | 模块 | 并行 | 用途 |
|------|------|------|------|
| run_command | run_command.py | ❌ | Shell 命令执行 |
| web_fetch | web_fetch.py | ❌ | 网页内容抓取 |
| update_todos | update_todos.py | ❌ | 任务规划三态推进 |
| list_skills | list_skills.py | ❌ | 列出可用技能 |
| load_skill | load_skill.py | ❌ | 按需加载技能 |
| dispatch_subagent | dispatch_subagent.py | ✅ | 派遣子代理 |
| spawn_teammate | team_tools.py | ✅ | 召入持久队友 |
| list_teammates | team_tools.py | ❌ | 列出队友状态 |
| send_message | team_tools.py | ❌ | 发 inbox 消息 |
| read_inbox | team_tools.py | ❌ | 读取并清空 inbox（队友内部自动调用，lead 不暴露） |
| broadcast | team_tools.py | ❌ | 广播给所有队友 |

### 3. 子代理系统 (subagents/ + dispatch_subagent.py)

**定位：** 一次性、无状态的独立任务执行器。

**执行流程：**
```
dispatch_subagent.execute(type, task)
  → 查找 subagent spec
  → 构建 [system(spec.prompt), user(task)]
  → run_agent(messages, max_turns, tool_names=whitelist)
  → 返回最终文本结果
```

**关键设计：**
- 工具白名单：子代理只能使用 `spec.tool_names` 中列出的工具
- 上下文隔离：子代理内部对话历史不回传，只返回结果摘要
- 并行执行：多个 dispatch_subagent 通过 ThreadPoolExecutor 并发
- 轮次上限：`spec.max_turns` 防止无限循环

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
- **MessageBus（team_bus.py）：** 基于文件 JSONL 的邮箱系统，队友间通过 inbox 通信
- **TeammateManager（team_manager.py）：** 队友生命周期管理（spawn / 状态追踪 / 线程循环）
- **Team 轮询（team_loop.py）：** lead 侧收件箱轮询、回禀注入、队友关停与清理（从 main.py 抽出）
- **Team Tools（tools/team_tools.py）：** 5 个工具暴露给 LLM 操作 team

**消息流转：**
```
Lead (主循环)                        Teammate (独立线程)
    │                                      │
    ├─ spawn_teammate ──────────────────→ 启动线程
    │                                      │
    ├─ send_message → bus.send ──→ inbox/name.jsonl
    │                                      │
    │                                      ├─ loop top: bus.read_inbox (取消息)
    │                                      ├─ run_agent (执行任务)
    │                                      └─ send_message → lead inbox
    │                                      │
    └─ _poll_inbox (2s轮询) ←────────── 收到回禀，注入对话
```

**队友生命周期：**
```
init (offline) → spawn → working → idle → ...
                          │
                          └── shutdown_request → shutdown (exit)
```

**设计决策：**
- **文件邮箱：** 进程重启不丢消息，无需额外中间件
- **contextvars 身份识别：** 队友线程通过 `set_caller(name)` 标记身份，工具自动使用正确的发送者，避免为每个队友创建独立工具实例
- **Event 唤醒：** `bus.send` 写入后触发 `Event.set()`，队友 0ms 响应
- **工具白名单：** 队友只能使用 `run_command`、`web_fetch`、`load_skill`、`send_message`，不能 spawn 新队友或派遣子代理
- **回禀机制：** lead 不暴露 `read_inbox` 工具，由 `_has_active_teammates` + `_poll_inbox`（2s 轮询）自动等待队友回禀并注入对话，避免 LLM 空轮询浪费 token
- **队友 inbox 读取：** 队友不暴露 `read_inbox` 工具，在 `_teammate_loop` 顶部直接调用 `bus.read_inbox(name)` 获取消息，避免 LLM 无意义空轮询
- **上下文重置：** 每轮任务完成后 `messages = [messages[0]]`，防止无限增长
- **容错：** `run_agent` 返回 None 时主动通知 lead
- **数量限制：** 由 `config.yaml` 的 `teammate.max_teammates` 控制，默认 10

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
**压缩触发条件：**

`prompt_tokens > context_length × context_usage_threshold`

即：当 LLM 请求的 prompt_tokens 超过模型上下文窗口的指定比例时触发。由 `llm.py` 的 `last_usage` 提供最近一次请求的 token 用量。

**压缩后保留：**
- 最近 `keep_recent` 条消息（默认 100 条）
- 且保留消息总字符数不超过 `char_threshold`（超出则从头部继续裁剪）

**压缩流程：**
1. 根据上述条件确定归档区间和保留区间
2. 旧消息 + 当前记忆/画像/情景 → 发送给 LLM 提取结构化输出
3. 更新三层存储 + 在 history.jsonl 写入 compact_event 标记
4. 合并最新长期记忆/用户画像到 system prompt

**历史恢复：**
- `load_unarchived` 从最后一个 compact_event 之后读取未归档消息
- 启动时自动恢复上次会话的上下文

### 6. 上下文组装 (context.py)

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

### 7. 任务规划 (update_todos)

**TodoStore：** 独立于对话历史的持久状态存储。

**三态推进：**
```
pending → in_progress → completed
```

**约束：**
- 同一时间最多 1 个 in_progress
- 每次全量覆盖更新
- 状态跟随 TodoStore 实例，压缩不丢失

### 8. 配置系统 (config.py + config.yaml)

**统一配置加载：** `config.py` 读取 `config.yaml`，导出四个配置对象供各模块使用：

- `MODEL_CONFIG` — 模型 API 地址、密钥、模型名
- `TIMEOUTS` — 各类超时（LLM请求、队友等待、lead轮询、网页抓取）
- `COMPACTOR` — 压缩参数（上下文使用率阈值、保留消息数、保留字符上限）
- `TEAMMATE` — 队友参数（最大数量、轮次上限、基础工具白名单）

**配置项一览：**

| 分组 | 参数 | 默认值 | 消费模块 |
|------|------|--------|----------|
| timeouts | `llm` | 120 | llm.py |
| timeouts | `teammate_recv` | 5 | team_manager.py |
| timeouts | `lead_wait` | 1800 | main.py |
| timeouts | `lead_poll_interval` | 2 | main.py |
| timeouts | `web_fetch` | 60 | tools/web_fetch.py |
| compactor | `context_usage_threshold` | 0.8 | main.py → Compactor |
| compactor | `keep_recent` | 100 | main.py → Compactor |
| compactor | `char_threshold` | 50000 | main.py → Compactor |
| teammate | `max_teammates` | 10 | team_manager.py |
| teammate | `max_turns` | 20 | team_manager.py |
| teammate | `base_tools` | [3项] | team_manager.py |
| model | `api_url` | — | llm.py |
| model | `api_key` | — | llm.py |
| model | `model` | — | llm.py |
| model | `context_length` | 128000 | main.py → Compactor |

**设计决策：** 所有运行时参数集中在 `config.yaml`，避免硬编码散落各文件，修改无需改代码。

### 9. 日志系统 (logger.py)

**双输出策略：**
- 终端：WARNING+ — 仅错误和重要提示
- 文件 `logs/YYYYMMDD.log`：DEBUG — 全量记录，格式 `[时间 [级别] [PID/TID] 消息]`，含 LLM 请求响应、工具调用与返回、MSG 通信、队友状态、压缩归档等

### 10. Agent 执行器 (runner.py)

**可复用的对话循环：** 被主循环、子代理、队友三者复用。

```python
def run_agent(messages, max_turns=10, tool_names=None) -> str | None
    for _ in range(max_turns):
        msg = chat(messages, tools=filtered_defs)
        if not msg or "tool_calls" not in msg:
            return msg.content
        handle_tool_calls(msg, messages)
    return None
```

**设计决策：**
- 工具白名单通过 `tool_names` 参数传入，子代理和队友用不同的白名单
- 轮次上限防止无限循环
- 上下文安全阀：`prompt_tokens > context_length × context_usage_limit` 时提前退出，值从 `config.yaml` 的 `runner.context_usage_limit` 读取
- 返回 None 时调用方负责容错处理

---

## 关键设计原则

1. **模块化：** 一个文件一个职责，接口简单（`definition` + `execute`）
2. **工具白名单：** 子代理和队友有独立的工具访问权限，lead 按需过滤工具
3. **上下文隔离：** 子代理/队友的对话历史不回传主循环
4. **容错优先：** 并行工具单点异常不传染，队友超时有通知
5. **文件持久化：** 邮箱和记忆均基于文件，零外部依赖
6. **依赖注入：** 工具模块通过 `configure(**kwargs)` 注入外部依赖，避免模块级可变赋值
6. **LLM 驱动压缩：** 用模型自身智能提取记忆，基于 prompt_tokens 占上下文窗口比例精准触发压缩
7. **零浪费轮询：** inbox 读取由代码层自动处理，不暴露给 LLM 避免空轮询消耗 token
