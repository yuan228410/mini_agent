# Team Collaboration

mini_ai 提供两套独立的 Agent 协作机制：**子代理**（一次性、并行）和 **Team 队友**（持久、角色化），以及 DAG 工作流编排。

## 子代理系统

派遣独立的一次性子代理执行并行任务，上下文完全隔离。

### 内置子代理

| 子代理 | 可用工具 | 用途 |
|--------|----------|------|
| researcher | run_command, web_fetch, load_skill | 信息搜索与分析 |
| coder | run_command, load_skill | 代码编写与修改 |

### 特点

- **工具白名单**：子代理只能使用定义中列出的工具
- **轮次限制**：每个子代理有 `max_turns` 上限，防止无限循环
- **并行执行**：多个 `dispatch_subagent` 通过 ThreadPoolExecutor 并行运行
- **上下文隔离**：子代理内部对话历史不回传，只返回最终结果
- **安全阀**：`prompt_tokens` 超过 `context_length × 88%` 时自动终止

### 新增子代理

在 `subagents/` 下创建 `xxx.md` 文件即可：

```markdown
---
name: my-agent
description: 我的子代理
tools: run_command, web_fetch
max_turns: 10
---
你是一个...（system prompt）
```

## Team 协作系统

`team/` 子包提供多 Agent 编排，支持持久队友、共享黑板、DAG 工作流、P2P 通信。

### Team 工具

| 工具 | 用途 |
|------|------|
| spawn_teammate | 召入/唤醒队友，指定名字、职司、首项任务 |
| list_teammates | 列出所有队友及状态（idle/working/offline） |
| send_message | 给队友或 lead 发 inbox 消息（支持 P2P） |
| read_inbox | 读取并清空自己的 inbox（仅队友内部自动调用，lead 不暴露） |
| broadcast | 向所有队友广播消息 |
| dismiss_team | 主动解散所有活跃队友 |
| blackboard_write | 向共享黑板写入数据 |
| blackboard_read | 从共享黑板读取数据 |
| blackboard_list | 列出黑板上的 key |
| run_workflow | 提交 DAG 工作流并执行 |
| workflow_status | 查看工作流执行状态 |
| load_workflow | 加载预定义 YAML 工作流模板 |

### 核心组件

| 组件 | 职责 |
|------|------|
| **MessageBus** (`team/bus.py`) | 基于文件 JSONL 的邮箱系统 + Condition 唤醒 + inbox 容量限制 |
| **TeammateManager** (`team/manager.py`) | 队友生命周期管理（spawn / idle 超时 / P2P 通信） |
| **Team 轮询** (`team/loop.py`) | lead 侧回禀等待、消息过滤、inbox 清理 |
| **Blackboard** (`team/blackboard.py`) | Agent 间共享 KV 存储（线程安全 + 可选文件持久化） |
| **TaskGraph** (`team/task_graph.py`) | 轻量 DAG 调度器（依赖解析 + 条件分支 + 重试） |
| **Orchestrator** (`team/orchestrator.py`) | DAG 驱动编排循环（并行派遣 + Condition 唤醒 + 结果汇总） |

### 消息流转

```
Lead (主循环)                          Teammate (独立线程)
    │                                        │
    ├─ spawn_teammate ────────────────────→ 启动线程
    │                                        │
    ├─ send_message → bus.send() ──→ inbox/name.jsonl
    │                         └─→ Event.set() (唤醒收件人)
    │                                        │
    │                                        ├─ loop top: bus.read_inbox(name)
    │                                        ├─ run_agent (执行任务)
    │                                        └─ send_message → lead inbox
    │                                               └─→ lead_event.set()
    │                                        │
    └─ lead_event.wait(timeout=2s) ←────── 0ms 唤醒
       └─ poll_inbox → 过滤 → 注入对话 → LLM
```

### 队友生命周期

```
init (offline) → spawn → working → idle → ...
                          │
                          └── shutdown_request → shutdown (exit)
```

- **创建**：Lead 调用 `spawn_teammate`。同名队友已存在则直接发新任务
- **运行**：执行完任务后进入 idle，收到新消息再次 working，支持跨多轮对话
- **销毁**：idle 超时（默认 300s）/ `dismiss_team` 主动解散 / 收到 shutdown_request

### 特点

- **持久队友**：spawn 后持续运行，空闲超时（`idle_timeout`）自动退出
- **P2P 通信**：队友可通过 `send_message` 直接互通，`list_teammates` 发现彼此
- **共享黑板**：线程安全的 KV 存储，可选持久化到文件
- **DAG 编排**：定义任务依赖图，Orchestrator 自动调度（并行/串行/条件分支）
- **条件分支**：DAG 节点支持 `condition` 表达式，不满足时跳过
- **错误重试**：DAG 节点支持 `max_retry`，失败后自动重试
- **工作流模板**：`~/.mini_ai/workflows/` 目录存放 YAML 模板
- **Event 唤醒**：`threading.Condition` 精确唤醒，零延迟
- **工具白名单**：队友可使用 `run_command`、`web_fetch`、`load_skill`、`send_message`、`list_teammates`、`blackboard_read/write/list`
- **上下文安全阀**：队友 `prompt_tokens > context_length × 88%` 自动终止并回禀
- **inbox 容量限制**：单个 inbox 上限 100KB，防止无限膨胀
- **上下文重置**：每轮任务完成后 `messages = [messages[0]]`，防止无限增长

### DAG 工作流示例

```json
{
  "tasks": [
    {"id": "search", "agent": "subagent:researcher", "prompt": "搜索 RAG 技术"},
    {"id": "design", "agent": "subagent:coder", "prompt": "设计架构: {search}", "depends_on": ["search"]},
    {"id": "code", "agent": "subagent:coder", "prompt": "实现: {design}", "depends_on": ["design"]}
  ]
}
```

### 子代理 vs 队友 选型

| 维度 | dispatch_subagent | spawn_teammate |
|------|-------------------|----------------|
| 生命周期 | 一次性，完即销毁 | 持久，可多轮交互 |
| 通信 | 无，只返回最终结果 | 双向 inbox 通信 |
| 并行 | ✅ | ✅ |
| 上下文 | 隔离 | 隔离（每轮重置） |
| 适用场景 | 并行搜索、独立分析 | 编码+审查接力、多角色协作 |

> 一次性 = subagent，持久角色 = teammate

## 多 Agent 编排

所有编排功能通过自然语言触发，模型自动选择合适的执行方式：

```bash
# 并行搜索
你: 同时搜索 arxiv 和 GitHub 上关于 RAG 的最新内容

# DAG 工作流（自动按依赖顺序执行）
你: 先研究 WebSocket 技术，再设计聊天室架构，最后写代码实现

# 条件分支
你: 运行测试，如果失败就修复，通过就部署

# 共享黑板
你: 把搜索结果存到黑板，让 coder 读取后编码

# 持久队友（跨多轮对话保持）
你: 召入一个 coder 待命
你: 让 coder 实现 JSON parser    ← 第二轮，coder 还在

# P2P 协作
你: 让 coder 写完后直接发给 reviewer 审查

# 预定义工作流模板
你: 用 research_and_code 模板，topic 是向量数据库
```

| 需求 | 模型自动使用 |
|------|-------------|
| 简单搜索/分析 | subagent（同步一次性） |
| 并行多任务 | spawn_teammate 并行 |
| 有依赖的多步骤 | run_workflow（DAG 编排） |
| 多角色配合 | spawn + P2P 通信 |
| 跨 Agent 传递数据 | blackboard 共享黑板 |
| 失败自动重试 | DAG max_retry |

## 工作流使用说明

DAG 工作流让你定义有依赖关系的多步任务，系统自动编排执行：

```
你: 帮我先调研 RAG 技术，然后设计架构，最后写代码
```

模型自动生成并执行依赖图：`[research] → [design] → [code]`

**核心机制：**

- 无依赖的任务**自动并行**
- `{task_id}` 占位符被替换为前置任务结果
- 每个任务完成后结果自动写入共享黑板
- 支持 `condition` 条件分支（不满足则跳过）
- 支持 `max_retry` 失败自动重试

**预定义模板**：将 YAML 放入 `~/.mini_ai/workflows/` 即可复用：

```yaml
# ~/.mini_ai/workflows/research_and_code.yaml
tasks:
  - id: research
    agent: subagent:researcher
    prompt: "搜索 {topic}"
    depends_on: []
  - id: code
    agent: subagent:coder
    prompt: "根据调研实现: {research}"
    depends_on: [research]
```

使用：`你: 用 research_and_code 模板，topic 是向量数据库`

## Workflow vs Team 选型

| 场景 | 推荐 | 原因 |
|------|------|------|
| A 结果传给 B | workflow | 自动传递 |
| A 和 B 来回对话 | team | P2P 多轮通信 |
| 固定流程复用 | workflow YAML | 一次定义多次用 |
| 条件分支/重试 | workflow | 内置支持 |
| 长期驻守的助手 | team | 跨轮保持 |

详细使用说明、YAML 模板格式、condition 语法见 [examples/team/](../examples/team/)。