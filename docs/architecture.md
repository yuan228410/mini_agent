# Architecture

## 主循环与共享编排层

```
用户输入 → 斜杠命令? → 处理命令
         → CLI/Web Adapter 组装 Display/RequestContext/ToolRegistry
         → ApplicationService.run_turn()
             ├─ 计划模式工具过滤 / approved plan 注入
             ├─ HistoryPersister 实时持久化
             ├─ run_tool_loop(..., tool_registry=会话级 registry)
             │    ├─ LLM router 选择 OpenAI / Anthropic adapter
             │    └─ ToolRegistry 并行/串行执行工具
             ├─ deferred assistant flush
             └─ Compactor.maybe_compact()
         → 队友兜底等待（适配层逻辑）→ 输出/推送事件
```

`src/mini_ai/main.py` 和 `src/mini_ai/web/chat_runner.py` 负责各自的交互/传输适配：读取用户输入、处理斜杠命令、维护 WebSocket/终端 Display、会话锁和队友兜底等待。每一轮真正的 LLM + 工具 + 持久化 + 计划模式编排收敛到 `src/mini_ai/core/application_service.py`：

1. **斜杠命令预处理** — CLI/Web 先处理 `/model`、`/workspace`、`/plan`、`/act` 等命令
2. **会话级运行时组装** — 为当前会话创建/获取 `RequestContext`、`ToolRegistry`、MemoryStore、HistoryDB、SkillLoader、Team/Blackboard 等组件
3. **ApplicationService.run_turn()** — 统一执行用户历史持久化、plan 工具过滤、approved plan todo 注入、工具循环、assistant 延迟持久化和上下文压缩
4. **工具循环 (run_tool_loop)** — 用户消息 + 工具列表送入 LLM，解析响应；有 `tool_calls` 则交给当前 `ToolRegistry` 执行并再次送入 LLM
5. **队友等待** — CLI/Web 适配层在主轮结束后等待队友回禀，并将 inbox 注入后再跑轻量轮次
6. **输出回复** — CLI 渲染 Rich 输出，Web 通过 `WebDisplay` 推送 WebSocket 事件

这种分层让 CLI/Web 共享核心 turn 语义，同时保留各自的 UI、连接、锁和队友交互差异。

---

## 多模型与流式输出

`config.yaml` 中 `active_model` 切换模型，每个模型独立配置协议和参数：

```yaml
active_model: claude
models:
  claude:
    api_mode: anthropic          # openai / anthropic
    api_url: https://...
    api_key: sk-...
    model: Claude Opus 4.7
    context_length: 200000
    temperature: 0.3
    reasoning_effort: high       # o 系列：low/medium/high
    thinking:                    # 模型级覆盖全局 thinking
      enabled: true
      budget_tokens: 10000
```

- `/model <名称>` 运行时切换，立即生效并持久化
- LLM 通信层位于 `src/mini_ai/llm/`：`router.py` 根据 `api_mode` 分派到 `openai.py`（OpenAI 协议）或 `anthropic.py`（Claude 协议），`base.py` 提供共享基础设施
- 流式输出时文本逐字打印，完成后重渲为 Rich Markdown，工具调用仍走批量模式
- **Anthropic 协议**：`thinking` content block + `thinking_delta` 流式块
- **OpenAI 协议**：`reasoning_content` 字段 + 流式 `delta.reasoning_content`

### 设计决策

| 决策 | 说明 |
|------|------|
| `requests.Session()` 长连接 | 复用 HTTP 连接，避免每次 TLS 握手 |
| `tools` 参数三态 | `True`=全部工具，`list[dict]`=指定列表，`False`=无工具 |
| 失败重试 | `llm_retries` 次，指数退避（2s → 4s → 8s），支持 timeout/connection/rate limit/429/5xx 等错误 |
| token 估算 | 字节长度近似（`len(utf8)//3`），偏保守（ASCII 高估 33%），见 `llm/base.py` `estimate_tokens()` |
| `RequestContext` | 每请求独立 model_config/display/http_session，多用户并发隔离；拥有的 HTTP session 会在请求结束时关闭 |
| LLM Router | provider 选择集中在 `llm/router.py`，adapter 不互相转发，避免 OpenAI/Anthropic 请求头和参数污染 |

---

## Agent 执行器 (runner/)

`run_tool_loop()` 是统一的 Agent 执行循环，被主循环、子代理、队友、Web 端复用。

```python
def run_tool_loop(messages, tools, *, streaming=False, display=None,
                  inject_fn=None, persist_fn=None, abort_event=None,
                  max_turns=20, context_length=None, ctx=None,
                  bus=None, compactor=None, tool_registry=None) -> tuple:
```

**架构（已重构）：**

Runner 模块拆分为四个职责清晰的子模块：

- **state.py** — 循环状态管理（`LoopState`）：轮次计数、错误计数、spawn 标记
- **executor.py** — LLM 调用（`ToolExecutor`）：流式/非流式统一，工具执行由 loop.py 直接调用 handle_tool_calls
- **error_handler.py** — 错误处理策略（`ErrorHandler`）：异常分类、用户提示、恢复建议
- **loop.py** — 精简版主循环（`run_tool_loop`、`run_agent`）：协调上述组件

**关键机制：**

- **流式/非流式统一** — 同一路径处理两种模式
- **自动重试** — 流式/非流式均支持自动重试（timeout/connection/rate limit/429/5xx），指数退避
- **abort 中断** — 每轮检查 `abort_event.is_set()`，支持 Web 端中断
- **上下文安全阀** — `prompt_tokens > context_length × 88%` 提前退出
- **上下文溢出恢复** — API 返回 400 + 溢出关键词时，`force_compact()` 渐进恢复（L0→L4 逐级加码裁剪+压缩），成功后重试当前轮次（最多 3 次）
- **工具结果裁剪** — `ContextPruner.prune()` 三级策略（保护区/软裁剪/硬裁剪），压缩前零开销减少 prompt token
- **错误熔断** — 连续 3 次工具 Error → 提前退出，避免空循环
- **轮次上限** — `max_turns`（默认 20）强制退出
- **实时持久化** — `persist_fn(msg)` 回调，每条消息生成即写入
- **会话级工具注册表** — 传入 `tool_registry` 时，工具分发、并行安全判断和缓存都使用当前会话的 `ToolRegistry`，避免 Web 多会话串状态
- **工具结果缓存** — metadata 标记为 `cacheable` 的工具在当前 registry 内走 LRU 缓存，减少重复执行（见 [工具系统](tools.md#工具结果缓存)）

`run_agent()` 作为轻量包装（供子代理/队友内部调用），返回最终文本。超轮次时自动兜底：先尝试取最后一条 assistant 消息，再尝试请求 LLM 总结，异常安全。

---

## 工作空间

工作空间按项目隔离记忆、会话、历史数据。

| 模式 | 绑定方式 |
|------|----------|
| **CLI** | 自动绑定：在哪个目录运行 `mini-ai`，该目录名即为工作空间名 |
| **Web** | 手动管理：通过顶栏工作空间面板操作 |

每个工作空间独立存储在 `~/.mini_ai/workspaces/<name>/`，包含 `workspace.yaml`（元数据）、`memory_data/`（记忆 + 历史）、`.team/`（协作数据）。

CLI 命令见 [CLI 命令参考](cli-commands.md#工作空间)，Web 操作见 [Web 界面](web-interface.md)。

---

## 自定义 Agent 人设

位于 `character/` 目录：
- **SOUL.md** — 核心身份、能力、工作流程定义（注入 system prompt 顶部）
- **RULES.md** — 行为规范约束（注入 system prompt 底部）

用户修改这两个文件即可自定义 Agent 的行为风格和规则约束。

---

## 上下文组装 (context.py)

按优先级拼接 system prompt：

```
1. 身份定义（SOUL.md）← 三层级合并（global → user → workspace）
   ---
2. 系统核心能力（硬编码，core_prompt.py）
   ---
3. 行为规范（RULES.md）← 三层级合并
   ---
4. 技能列表（SkillLoader）
   ---
5. 长期记忆（MEMORY.md）← 三层级合并
   ---
6. 项目规范（CLAUDE.md / AGENTS.md，自动读取当前目录）
```

### 三层级合并机制

**路径优先级**：`global` → `user` → `workspace`（后者覆盖前者）

| 层级 | 路径 | 说明 |
|------|------|------|
| **global** | `~/.mini_ai/memory/` | 全局共享，所有用户、所有工作空间 |
| **user** | `~/.mini_ai/users/<username>/memory/` | 用户级，该用户所有工作空间共享 |
| **workspace** | `<workspace_dir>/memory_data/` | 工作空间级，仅当前工作空间 |

**合并策略**：
- 按 `## 标题` 拆分各个文件
- **同名 section，后者覆盖前者**（last-wins）
- 不同名 section，叠加保留

**示例**：
```
# global/SOUL.md
## 自我介绍
我是全局 AI

## 行为风格
- 喜欢写代码

---
# user/SOUL.md
## 自我介绍
我是用户级 AI  ← 覆盖 global

## 新增特性
- 喜欢问问题  ← 新增

---
# workspace/SOUL.md
## 自我介绍
我是工作空间 AI  ← 覆盖 user
```

### 关键实现

- **SOUL.md / RULES.md / MEMORY.md** — 均支持三层级合并
- **ContextBuilder.build()** — 支持文件缓存（mtime 检查），高频调用不重复读盘
- **MemoryStore._tier_paths()** — 返回 `[global, user, workspace]` 路径列表
- **_merge_sections()** — 按 `## 标题` 合并，同名 section 后者覆盖前者

---

## 统一异常体系

位于 `src/mini_ai/exceptions.py`，建立分层异常类，统一错误处理模式：

```
MiniAIError (基类)
├── ConfigError          — 配置加载/校验失败
├── LLMError             — LLM 调用异常（支持 status_code、provider）
└── ToolError            — 工具执行异常
    ├── ResourceNotFoundError    — 资源不存在
    ├── PermissionDeniedError    — 权限不足
    └── ValidationError          — 参数校验失败
```

**核心特性：**

- `recoverable` 标记：区分可恢复错误（工具失败可重试）和不可恢复错误（配置错误需重启）
- `to_user_message()` 方法：返回用户友好的错误提示，隐藏技术细节
- 错误上下文：支持附加 `**context` 参数，便于日志分析和调试

**使用示例：**

```python
from mini_ai.exceptions import ToolError, ResourceNotFoundError

# 抛出异常
raise ResourceNotFoundError("read_file", "/path/to/file.txt")

# 捕获并处理
try:
    result = tool.execute(args)
except ToolError as e:
    if e.recoverable:
        logger.warning(f"工具 {e.tool_name} 失败: {e.to_user_message()}")
    else:
        raise
```

---

## 关键设计原则

1. **模块化** — 一个文件一个职责，接口简单（`definition` + `execute`）
2. **共享核心编排** — CLI/Web 通过 `ApplicationService` 共用同一套 turn 语义，适配层只处理输入输出、连接和锁
3. **工具白名单** — 子代理和队友有独立的工具权限
4. **上下文隔离** — 子代理/队友的对话历史不回传主循环
5. **容错优先** — 并行工具单点异常不传染，LLM 请求自动重试
6. **文件持久化** — 邮箱和记忆基于文件，零外部依赖
7. **Session-local 优先** — 每个 CLI/Web 会话优先使用独立 `ToolRegistry`，通过 `_BoundTool`/contextvars 绑定 MemoryStore、HistoryDB、SkillLoader、Team、Blackboard、Display 等状态；模块级 `configure(**kwargs)` 仅保留兼容路径
8. **Metadata 驱动调度** — 工具的并行、缓存、计划模式可见性由 `ToolMetadata` 描述，避免散落硬编码白名单
9. **LLM 驱动压缩** — 模型自身智能提取记忆
10. **Event 驱动唤醒** — `threading.Event` 替代 sleep 轮询
11. **Per-session 隔离** — Web 端每个会话独立 MemoryStore/HistoryDB/Compactor/ToolRegistry

---

## 项目结构

```
src/mini_ai/
├── main.py              # CLI 主循环编排
├── config.py            # 配置加载（AppConfig 访问器 + 热加载）
├── exceptions.py        # 统一异常体系（MiniAIError / ToolError / LLMError 等）
├── context.py           # 系统提示词组装
├── utils.py             # 公共工具函数（now_ts 时间戳生成）
├── workspace.py         # 工作空间管理
├── logger.py            # 日志模块（终端 WARNING+ / 文件 DEBUG）
├── core/                # 核心编排层（CLI/Web 共用）
│   ├── application_service.py # ApplicationService 统一 turn 编排
│   ├── runtime_context.py     # SessionIdentity / ToolContext / SessionRuntimeContext
│   ├── display_protocol.py    # Display 协议定义（类型安全约束）
│   ├── persister.py           # HistoryPersister 统一持久化
│   └── chat_session.py        # ChatSession 统一会话运行逻辑
├── llm/                 # LLM 通信层（router + base + openai + anthropic）
├── cli/                 # CLI 交互层（display + commands）
├── memory/              # 记忆系统（store + compactor + context_pruner + history_db）
├── runner/              # Agent 执行循环（state + executor + error_handler + loop）
├── tools/               # 工具系统（ToolBase + ToolRegistry + ToolMetadata + registry-local cache + 25+ 工具模块）
├── team/                # 多 Agent 编排（bus + manager + blackboard + task_graph + orchestrator）
├── subagents/           # 子代理定义（coder/researcher/reviewer/tester/planner）
├── web/                 # Web 界面
│   ├── session_manager.py   # SessionState + SessionManager（统一会话状态管理）
│   ├── chat_runner.py       # Web 端工具循环运行器
│   ├── display.py           # WebDisplay 适配器
│   └── routes/              # FastAPI 路由（chat + sessions + models + ...）
└── character/           # Agent 人设（SOUL.md + RULES.md）
```

完整文档索引：
- [CLI 命令参考](cli-commands.md)
- [工具系统](tools.md)
- [配置参考](configuration.md)
- [记忆系统](memory-system.md)
- [多 Agent 编排](team-collaboration.md)
- [Web 界面](web-interface.md)