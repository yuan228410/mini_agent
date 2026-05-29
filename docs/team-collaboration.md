# Team Collaboration

mini_ai 提供两套独立的 Agent 协作机制：**子代理**（一次性、并行）和 **Team 队友**（持久、角色化），以及 **DAG 工作流**编排。

---

## 子代理系统

派遣独立的一次性子代理执行并行任务，上下文完全隔离。子代理执行完成后自动销毁，不保留状态。

### 内置子代理

| 子代理 | 可用工具 | 用途 |
|--------|----------|------|
| researcher | run_command, web_fetch, load_skill | 信息搜索与分析 |
| coder | run_command, load_skill, read_file, write_file, edit_file, search_files, list_dir | 代码编写与修改 |
| reviewer | read_file, search_files, run_command, load_skill | 代码审查，发现潜在问题 |
| tester | read_file, write_file, edit_file, run_command, search_files, load_skill | 编写和执行测试 |
| planner | read_file, search_files, web_fetch, run_command, load_skill | 需求分析，方案设计 |

### 基本用法

```
你: 帮我查一下 FastAPI 的文档

→ 模型调用 dispatch_subagent(type="researcher", task="搜索 FastAPI 官方文档，总结核心特性")
→ researcher 独立运行，返回结果
```

多个子代理**自动并行**执行：

```
你: 同时搜索 arxiv 和 GitHub 上关于 RAG 的最新内容

→ 模型同时调用两个 dispatch_subagent（ThreadPoolExecutor 并行）：
   dispatch_subagent(type="researcher", task="搜索 arxiv 上 RAG 最新论文")
   dispatch_subagent(type="researcher", task="搜索 GitHub 上 RAG 热门项目")
→ 两个 researcher 同时执行，互不干扰
```

### 结果链式传递（inputs）

子代理之间**不会自动传结果**，每次 dispatch 都是独立的。`inputs` 参数让主 Agent 把上一步的结果手动传递给下一步。

**场景：先调研后编码**

```
你一条指令完成：
你: 调研 FastAPI 和 Flask 的区别，然后根据调研结果写个 demo
```

模型内部实际执行两步：

```
第一步 → dispatch_subagent(type="researcher", task="调研 FastAPI 和 Flask 的区别")
  返回结果："FastAPI 支持异步、性能好; Flask 轻量、生态成熟..."

第二步 → dispatch_subagent(
  type="coder",
  task="根据调研结果实现一个 FastAPI demo: {research}",
  inputs={"research": "FastAPI 支持异步、性能好; Flask 轻量、生态成熟..."}
)
  coder 实际收到的 task:
  "根据调研结果实现一个 FastAPI demo: FastAPI 支持异步、性能好; Flask 轻量、生态成熟..."
```

**三步链：搜索 → 方案设计 → 编码**

```
第一步 → researcher 搜索 WebSocket 技术
  返回: "WebSocket 是双向通信协议，基于 TCP..."

第二步 → planner 设计聊天室架构
  dispatch_subagent(
    type="planner",
    task="基于调研设计聊天室架构: {research}",
    inputs={"research": "WebSocket 是双向通信协议，基于 TCP..."}
  )
  返回: "前端用 WS API，后端用 FastAPI WebSocket..."

第三步 → coder 实现代码
  dispatch_subagent(
    type="coder",
    task="按方案实现: {plan}",
    inputs={"plan": "前端用 WS API，后端用 FastAPI WebSocket..."}
  )
```

**并行搜索后汇总**

```
并行 dispatch 两个 researcher（A 搜百度，B 搜 GitHub）：
  A → 返回: "百度云函数文档..."
  B → 返回: "GitHub star 数量..."

再派 planner 汇总：
  dispatch_subagent(
    type="planner",
    task="综合以下两个来源的信息做技术选型:
      百度结果: {baidu}
      GitHub 结果: {github}",
    inputs={
      "baidu": "百度云函数文档...",
      "github": "GitHub star 数量..."
    }
  )
```

### 动态注册子代理

`register_subagent` 工具让你在对话中直接创建新子代理，无需手动写文件。

```
你: 帮我创建一个叫 data-analyzer 的子代理，用来分析数据，工具用 web_fetch 和 load_skill

→ 模型调用 register_subagent(
    name="data-analyzer",
    description="数据分析专家，负责分析和提取数据中的模式",
    prompt="你是一个数据分析专家。收到数据后分析其中的模式、趋势和异常...",
    tools=["web_fetch", "load_skill"],
    max_turns=10
  )
→ 文件写入 subagents/data-analyzer.md
→ dispatch_subagent 工具描述刷新
→ 立即可用
```

也可以用来自定义编码流程：

```
你: 创建一个叫 frontend-coder 的子代理，专门负责写 Vue 组件

→ register_subagent(
    name="frontend-coder",
    description="前端工程师，负责编写 Vue 组件",
    prompt="你是一个 Vue 前端工程师...",
    tools=["run_command", "read_file", "write_file", "edit_file", "search_files"],
    max_turns=15
  )
```

### 特点

- **工具白名单**：子代理只能使用定义中列出的工具
- **轮次限制**：每个子代理有 `max_turns` 上限，防止无限循环
- **并行执行**：多个 `dispatch_subagent` 通过 ThreadPoolExecutor 并行运行
- **上下文隔离**：子代理内部对话历史不回传，只返回最终结果
- **安全阀**：`prompt_tokens` 超过 `context_length × 88%` 时自动终止

### 新增子代理（传统方式）

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

---

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

### 基本用法

**召入队友并派发任务：**

```
你: 召入一个 coder，让他实现一个 JSON parser

→ spawn_teammate(name="coder", role="coder", prompt="请实现一个 JSON parser 库，支持基本的 JSON 解析功能")
→ coder 在独立线程中开始工作
→ 完成后自动回禀 lead
```

**队友持续存活，跨多轮对话：**

```
你: 召入一个 coder 待命
你: 让 coder 实现 JSON parser        ← 第一轮
你: 再让 coder 写个单元测试           ← 第二轮，coder 还在
你: 让 coder 把测试结果发给 reviewer  ← 第三轮，P2P 通信
```

**队友之间 P2P 通信：**

```
你: 让 coder 写完代码后直接发给 reviewer 审查

→ coder 完成后自动调用 send_message(to="reviewer", content="代码已完成，请审查...")
→ reviewer 收到消息，开始审查
→ reviewer 审查完成后回禀 lead
```

**广播消息给所有队友：**

```
你: 通知所有队友项目规范有更新
→ broadcast(content="项目规范已更新，请重新加载 AGENTS.md")
```

### 消息流转

```
Lead (主循环)                          Teammate (独立线程)
    │                                        │
    ├─ spawn_teammate ────────────────────→ 启动线程
    │                                        │
    ├─ send_message → bus.send() ──→ inbox/name.jsonl
    │                         └─→ Event.set() (唤醒收件人)
    │                                        │
    │                                        ├─ 读取 inbox
    │                                        ├─ 执行任务
    │                                        └─ send_message → lead inbox
    │                                               └─→ lead_event.set()
    │                                        │
    └─ lead_event.wait() ←─────────────── 立即唤醒
       └─ 读取 inbox → 注入对话 → LLM 处理
```

队友收到消息后自动唤醒（`threading.Event`），无需轮询。有消息 0ms 响应，无消息低功耗等待。

### 队友生命周期

```
spawn_teammate → working（执行任务）→ idle（等待新消息）→ working → ...
                                        │
                                        ├── idle 超时 (默认 300s) → 自动退出
                                        └── dismiss_team → 退出
```

- **创建**：Lead 调用 `spawn_teammate`。同名队友已存在则直接发新任务
- **运行**：执行完任务后进入 idle，收到新消息再次 working，支持跨多轮对话
- **销毁**：idle 超时（默认 300s）/ `dismiss_team` 主动解散 / 收到 shutdown_request

### 共享黑板（Blackboard）

黑板用于 Agent 间共享数据，不经过 lead 中转。适合传递搜索结果、分析结论、代码片段等。

**场景：researcher 搜结果 → coder 读结果编码**

```
你: 召入 researcher 和 coder，让 researcher 搜索后把结果存黑板，coder 读取后编码

→ spawn_teammate(name="researcher", role="researcher", prompt="搜索 FastAPI 教程，结果存黑板")
→ spawn_teammate(name="coder", role="coder", prompt="从黑板读取搜索结果，然后写个 demo")

researcher 内部：
  → web_fetch 搜索
  → blackboard_write(key="search_result", value="FastAPI 教程内容...")
  → send_message(to="coder", content="搜索结果已写入黑板")

coder 内部：
  → 收到 researcher 通知
  → blackboard_read(key="search_result") → 拿到 "FastAPI 教程内容..."
  → 开始编码
```

**查看黑板内容：**

```
你: 看看黑板上有什么
→ blackboard_list() → 返回: search_result (by researcher), design_doc (by planner)
→ blackboard_read(key="search_result") → 查看具体内容
```

### 特点

- **持久队友**：spawn 后持续运行，空闲超时（`idle_timeout`）自动退出
- **P2P 通信**：队友可通过 `send_message` 直接互通，`list_teammates` 发现彼此
- **共享黑板**：线程安全的 KV 存储，可选持久化到文件，重启不丢失
- **Event 唤醒**：`threading.Event` 精确唤醒，有消息 0ms 响应
- **工具白名单**：队友可使用 `run_command`、`web_fetch`、`load_skill`、`send_message`、`list_teammates`、`blackboard_read/write/list`
- **上下文安全阀**：队友 `prompt_tokens > context_length × 88%` 自动终止并回禀
- **inbox 容量限制**：单个 inbox 上限 100KB，防止无限膨胀
- **上下文重置**：每轮任务完成后清空消息历史，防止无限增长

---

## DAG 工作流

DAG 工作流让你定义有依赖关系的多步任务，系统自动编排：无依赖的任务并行，有依赖的等前置完成后触发。

### 基本链式

```
你: 帮我先调研 RAG 技术，然后设计架构，最后写代码

→ 模型生成并执行依赖图：[research] → [design] → [code]

→ run_workflow(tasks=[
    {"id": "research", "agent": "subagent:researcher", "prompt": "搜索 RAG 技术"},
    {"id": "design", "agent": "subagent:coder", "prompt": "设计架构: {research}", "depends_on": ["research"]},
    {"id": "code", "agent": "subagent:coder", "prompt": "实现: {design}", "depends_on": ["design"]},
  ])
```

`{research}` 和 `{design}` 会被自动替换为前置任务的返回结果。`agent` 支持两种格式：
- `subagent:xxx` — 使用子代理（一次性）
- 队友名字 — 使用已 spawn 的队友（持久）

### 并行 + 合并

```
你: 先并行搜索百度百科和 GitHub，然后综合结果写个报告

→ 依赖图：[search_baidu] ──┐
           [search_github] ──┤→ [report]
           （两个搜索并行，都完成后触发 report）

→ run_workflow(tasks=[
    {"id": "search_baidu", "agent": "subagent:researcher", "prompt": "搜索百度"},
    {"id": "search_github", "agent": "subagent:researcher", "prompt": "搜 GitHub"},
    {"id": "report", "agent": "subagent:coder", "prompt": "综合: {search_baidu}\n{search_github} 写报告",
     "depends_on": ["search_baidu", "search_github"]},
  ])
```

### 条件分支

根据前置任务的结果决定是否执行后续任务。条件表达式支持 `status`、`result`、`error` 和 `blackboard` 上下文。

```
你: 运行测试，如果失败就修复，通过就部署

→ run_workflow(tasks=[
    {"id": "test", "agent": "subagent:tester", "prompt": "运行测试"},
    {"id": "fix", "agent": "subagent:coder", "prompt": "修复测试失败: {test}",
     "depends_on": ["test"],
     "condition": "test.status == 'done' and 'FAIL' in test.result"},
    {"id": "deploy", "agent": "subagent:coder", "prompt": "部署",
     "depends_on": ["test"],
     "condition": "'FAIL' not in test.result"},
  ])
```

条件判断逻辑：
- `condition` 表达式在安全的 AST 求值器中执行（替代 `eval()`），仅支持比较、逻辑运算和字典属性访问，**禁止任意代码执行**
- 可用上下文：`{task_id}.status`、`{task_id}.result`、`{task_id}.error`、`blackboard`
- 表达式为 `False` 时任务被跳过（status=skipped），不阻塞下游
- 表达式求值异常时默认执行（保守策略）

### 错误重试

```
你: 从 GitHub 下载数据，失败则重试 3 次

→ run_workflow(tasks=[
    {"id": "fetch", "agent": "subagent:researcher", "prompt": "从 GitHub API 获取数据",
     "max_retry": 3},
    {"id": "process", "agent": "subagent:coder", "prompt": "处理数据: {fetch}",
     "depends_on": ["fetch"]},
  ])
```

- `max_retry` 控制失败后自动重试次数（默认 1，即不重试）
- 每次重试使用相同的 prompt
- 所有重试都失败后标记为 failed，下游任务仍可执行（通过 condition 判断是否跳过）

### 预定义模板

将 YAML 放入 `~/.mini_ai/workflows/` 即可复用：

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

使用方式：

```
你: 用 research_and_code 模板，topic 是向量数据库
→ load_workflow(name="research_and_code")
→ 替换 {topic} 为 "向量数据库"
→ run_workflow(tasks=[...])
```

### 查看工作流状态

```
你: 刚才的工作流执行得怎么样了？
→ workflow_status() → 返回每个节点的状态和结果摘要
```

---

## 选型指南

### 子代理 vs 队友

| 维度 | dispatch_subagent | spawn_teammate |
|------|-------------------|----------------|
| 生命周期 | 一次性，完即销毁 | 持久，可多轮交互 |
| 通信 | 无，只返回最终结果 | 双向 inbox 通信 |
| 并行 | ✅ 自动并行 | ✅ 自动并行 |
| 上下文 | 隔离，不保留 | 隔离，每轮重置 |
| 适用场景 | 并行搜索、独立分析 | 编码+审查接力、多角色协作 |

> 一次性 = subagent，持久角色 = teammate

### Workflow vs Team

| 场景 | 推荐 | 原因 |
|------|------|------|
| A 结果传给 B | workflow | 自动传递 `{task_id}` |
| A 和 B 来回对话 | team | P2P 多轮通信 |
| 固定流程复用 | workflow YAML | 一次定义多次用 |
| 条件分支/重试 | workflow | 内置支持 |
| 长期驻守的助手 | team | 跨轮保持 |

### 需求速查表

| 需求 | 系统自动使用 |
|------|-------------|
| 简单搜索/分析 | subagent（一次性，并行） |
| 搜索 → 编码（有依赖） | run_workflow（DAG 编排） |
| 并行多任务 | spawn_teammate 并行 |
| 多角色配合 | spawn + P2P 通信 |
| 跨 Agent 传数据 | blackboard 共享黑板 |
| 失败自动重试 | DAG max_retry |
| 条件判断 | DAG condition |
| 跨多轮对话的助手 | team（持久队友） |

---

详细使用说明、YAML 模板格式、condition 语法见 [examples/team/](../examples/team/)。