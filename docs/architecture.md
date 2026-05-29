# Architecture

## 主循环 (main.py)

```
用户输入 → 斜杠命令? → 处理命令
         → run_tool_loop(LLM, 工具) → 有 tool_calls? → 并行/串行执行工具 → 再次 chat
                                      ↓ 无
                                wait_for_teammates? → Event 等待回禀 → 收到 → 注入对话 → 再次 chat
                                ↓ 无活跃队友
                             输出回复 → 持久化 → 检查压缩
```

主循环位于 `src/mini_ai/main.py`，是 Agent 的顶层编排器。每轮用户输入后：

1. **斜杠命令预处理** — `/save`、`/load`、`/model`、`/workspace` 等命令优先处理
2. **工具循环 (run_tool_loop)** — 用户消息 + 工具列表送入 LLM，解析响应
3. **工具调用分发** — 有 `tool_calls` 则并行/串行执行，结果再次送入 LLM
4. **队友等待** — 无工具调用但有活跃队友时，`threading.Event` 等待回禀
5. **输出回复** — 无工具调用且无队友时，渲染输出
6. **实时持久化** — `persist_fn` 回调，每条消息生成即写入 HistoryDB
7. **压缩检查** — prompt token 超阈值则触发记忆压缩

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
- LLM 通信层位于 `src/mini_ai/llm/`：`openai.py`（OpenAI 协议）、`anthropic.py`（Claude 协议）、`base.py`（共享基础设施）
- 流式输出时文本逐字打印，完成后重渲为 Rich Markdown，工具调用仍走批量模式
- **Anthropic 协议**：`thinking` content block + `thinking_delta` 流式块
- **OpenAI 协议**：`reasoning_content` 字段 + 流式 `delta.reasoning_content`

### 设计决策

| 决策 | 说明 |
|------|------|
| `requests.Session()` 长连接 | 复用 HTTP 连接，避免每次 TLS 握手 |
| `tools` 参数三态 | `True`=全部工具，`list[dict]`=指定列表，`False`=无工具 |
| 失败重试 | `llm_retries` 次，递增延迟 × attempt |
| token 估算 | API 未返回 usage 时按内容 CJK-aware 估算（CJK 1:1，其他 4:1），见 `llm/base.py` `estimate_tokens()` |
| `RequestContext` | 每请求独立 model_config/display/http_session，多用户并发隔离 |

---

## Agent 执行器 (runner.py)

`run_tool_loop()` 是统一的 Agent 执行循环，被主循环、子代理、队友、Web 端复用。

```python
def run_tool_loop(messages, tools, *, streaming=False, display=None,
                  inject_fn=None, persist_fn=None, abort_event=None,
                  max_turns=20, context_length=None, ctx=None) -> tuple:
```

**关键机制：**

- **流式/非流式统一** — 同一路径处理两种模式
- **abort 中断** — 每轮检查 `abort_event.is_set()`，支持 Web 端中断
- **上下文安全阀** — `prompt_tokens > context_length × 88%` 提前退出
- **错误熔断** — 连续 3 次工具 Error → 提前退出，避免空循环
- **轮次上限** — `max_turns`（默认 20）强制退出
- **实时持久化** — `persist_fn(msg)` 回调，每条消息生成即写入

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
SOUL.md (核心身份)
---
长期记忆 (MemoryStore)
---
用户画像 (MemoryStore)
---
可用技能 (SkillLoader — global/user/workspace 三层覆盖)
---
CLAUDE.md / AGENTS.md (项目规范，自动读取当前目录)
---
RULES.md (行为规范)
```

- SOUL.md 和 RULES.md 位于 `character/` 目录，修改即可改变 Agent 角色
- CLAUDE.md 或 AGENTS.md 自动注入到 system prompt
- `ContextBuilder.build()` 支持文件缓存（mtime 检查），高频调用不重复读盘

---

## 关键设计原则

1. **模块化** — 一个文件一个职责，接口简单（`definition` + `execute`）
2. **工具白名单** — 子代理和队友有独立的工具权限
3. **上下文隔离** — 子代理/队友的对话历史不回传主循环
4. **容错优先** — 并行工具单点异常不传染，LLM 请求自动重试
5. **文件持久化** — 邮箱和记忆基于文件，零外部依赖
6. **依赖注入** — 工具通过 `configure(**kwargs)` 注入依赖
7. **LLM 驱动压缩** — 模型自身智能提取记忆
8. **Event 驱动唤醒** — `threading.Event` 替代 sleep 轮询
9. **Per-session 隔离** — Web 端每个会话独立 MemoryStore/HistoryDB/Compactor

---

## 项目结构

```
src/mini_ai/
├── main.py              # 主循环编排
├── config.py            # 配置加载（DATA_DIR / PACKAGE_DIR 分离）
├── runner.py            # 统一 Agent 执行循环
├── context.py           # 系统提示词组装
├── workspace.py         # 工作空间管理
├── logger.py            # 日志模块（终端 WARNING+ / 文件 DEBUG）
├── llm/                 # LLM 通信层（base + openai + anthropic）
├── cli/                 # CLI 交互层（display + commands）
├── memory/              # 记忆系统（store + compactor + history_db + session）
├── tools/               # 工具系统（ToolRegistry + 25+ 工具模块）
├── team/                # 多 Agent 编排（bus + manager + blackboard + task_graph + orchestrator）
├── subagents/           # 子代理定义（coder/researcher/reviewer/tester/planner）
├── web/                 # Web 界面（FastAPI + 路由）
└── character/           # Agent 人设（SOUL.md + RULES.md）
```

完整文档索引：
- [CLI 命令参考](cli-commands.md)
- [工具系统](tools.md)
- [配置参考](configuration.md)
- [记忆系统](memory-system.md)
- [多 Agent 编排](team-collaboration.md)
- [Web 界面](web-interface.md)