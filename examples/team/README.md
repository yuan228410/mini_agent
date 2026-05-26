# Team 多 Agent 编排 — 示例与快速上手

## 如何使用

所有功能通过**自然语言**触发，模型自动选择合适的工具。你不需要记住 JSON 格式或工具名。

---

## 场景 1：简单并行搜索

```
你: 帮我同时搜索 arxiv 和 GitHub 上关于 RAG 的最新内容
```

模型自动 spawn 两个队友并行搜索，完成后汇总回复。

---

## 场景 2：DAG 工作流（研究→设计→编码）

```
你: 帮我研究 WebSocket 技术，设计一个聊天室架构，然后写代码实现
```

模型自动调用 `run_workflow`，定义依赖链：
```
[search] → [design] → [code]
```

Orchestrator 按顺序执行，前一步结果自动传递给下一步。

---

## 场景 3：并行 fan-out + 汇总

```
你: 分别从论文、GitHub、技术博客三个来源调研 LLM Agent，最后写一份综合报告
```

模型定义 DAG：
```
[search_arxiv]  ─┐
[search_github] ─┼→ [summary]
[search_blog]   ─┘
```

三路并行搜索，全部完成后自动触发汇总。

---

## 场景 4：条件分支

```
你: 运行测试，如果失败就修复，通过就部署
```

模型定义带条件的 DAG：
```
[test] → 失败? → [fix] (max_retry=3)
       → 通过? → [deploy]
```

---

## 场景 5：共享黑板

```
你: 把刚才的搜索结果存到黑板 key=rag_research
你: 让 coder 从黑板读取 rag_research 来写代码
```

黑板数据所有 Agent 可见，跨任务持久保留。

---

## 场景 6：持久队友（跨轮对话）

```
你: 召入一个 coder 队友待命
   (coder 被 spawn，保持活跃)

你: 让 coder 实现一个 JSON parser
   (直接给已有的 coder 发任务，无需重新 spawn)

你: 让 coder 加个错误处理
   (coder 继续接收新任务)

你: 解散团队
   (dismiss_team)
```

队友空闲超时（默认 300 秒）后自动退出，也可随时手动解散。

### 队友生命周期

```
spawn_teammate(name, role, prompt)
        │
        ▼
  ┌──────────┐
  │ working  │ ← 执行 prompt 任务（LLM + 工具循环）
  └────┬─────┘
       │ 任务完成，回禀 lead
       ▼
  ┌──────────┐     收到新 inbox 消息
  │  idle    │ ──────────────────────→ working（循环）
  └────┬─────┘
       │
       ├── idle 超时 (300s) ──→ 自动退出
       ├── 收到 shutdown_request ──→ 退出
       └── dismiss_team ──→ 退出
```

**何时创建**：Lead LLM 调用 `spawn_teammate` 时。如果同名队友已存在且线程活着，直接发新任务到 inbox，不重建。

**何时运行**：
- 首次 spawn 的 `prompt` 作为第一个任务立即执行
- 执行完后进入 idle 等待
- 收到 inbox 消息 → 再次进入 working → 循环往复
- 支持跨多轮用户对话持续存在

**何时销毁**（三种方式）：
1. `idle_timeout` 超时（默认 300 秒，可在 config.yaml 设为 0 禁用）
2. Lead 调用 `dismiss_team` 工具主动解散
3. 收到 `shutdown_request` 消息（其他 Agent 可发）

---

## 场景 7：P2P 直接协作

```
你: 让 coder 写代码，写完后直接发给 reviewer 审查，不用经过我
```

coder 完成后通过 `send_message` 直接发给 reviewer，reviewer 审查后才回禀 lead。

---

## 场景 8：加载预定义工作流模板

将 YAML 放入 `~/.mini_ai/workflows/`：

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

然后：
```
你: 用 research_and_code 模板，topic 是向量数据库
```

---

## 选型速查

| 你的需求 | 说法示例 | 模型自动使用 |
|----------|----------|-------------|
| 简单问答/搜索 | "搜索 X" | subagent（同步一次性） |
| 并行处理 | "同时搜索 A 和 B" | 多个 spawn_teammate 并行 |
| 有依赖的多步任务 | "先研究再设计再编码" | run_workflow（DAG） |
| 多角色协作 | "coder 写代码，reviewer 审查" | spawn + P2P 通信 |
| 保存中间结果 | "把结果存起来" | blackboard_write |
| 条件判断 | "如果失败就重试" | DAG condition + max_retry |
| 复用流程 | "用 XX 模板" | load_workflow |

---

## 工作流 YAML 模板格式

```yaml
tasks:
  - id: 唯一标识
    agent: 执行者名称          # teammate 名 或 subagent:type
    prompt: "任务描述 {dep_id}" # {dep_id} 引用依赖任务结果
    depends_on: [依赖列表]
    condition: "可选条件表达式"  # 不满足则跳过
    max_retry: 1               # 失败重试次数
```

模板存放路径：`~/.mini_ai/workflows/`

参考示例：
- [workflow_basic.yaml](workflow_basic.yaml) — 链式 + 并行
- [workflow_conditional.yaml](workflow_conditional.yaml) — 条件分支

---

## 工作流详细说明

### 触发方式

工作流不需要手动调用 JSON API，有三种自然语言触发方式：

**1. 描述有依赖关系的任务（模型自动识别）：**
```
你: 帮我先调研 RAG 技术，然后设计架构，最后写代码实现
```

**2. 明确要求工作流模式：**
```
你: 用工作流模式：1)搜索论文 2)搜索 GitHub 3)汇总写报告
```

**3. 加载预定义模板：**
```
你: 用 code_review 工作流，requirement 是"实现 LRU Cache"
```

### 占位符语法

| 语法 | 含义 | 示例 |
|------|------|------|
| `{task_id}` | 引用依赖任务的执行结果 | `"基于调研编码: {research}"` |
| `{blackboard.key}` | 引用黑板上的数据 | `"参考: {blackboard.design_doc}"` |

占位符在任务执行前被替换为实际值。

### 执行规则

- **无依赖的任务自动并行**（如三个搜索同时进行）
- **有依赖的任务等前置全部完成后触发**
- **每个任务完成后结果自动写入 blackboard**（key = task_id）
- **失败的任务按 `max_retry` 自动重试**（默认 1 次不重试）
- **condition 不满足时任务被 skip**（不执行，不阻塞后续）
- **超时 30 分钟自动终止**

### condition 表达式

可引用依赖任务的状态：

```python
# 可用变量（每个依赖 task_id 都可访问）：
task_id['status']   # 'done' | 'failed' | 'skipped'
task_id['result']   # 任务结果文本
task_id['error']    # 错误信息（仅 failed 时有值）

# 示例：
"check['status'] == 'done'"                    # check 成功时执行
"'ERROR' in check['result']"                   # 结果包含 ERROR 时执行
"check['status'] == 'failed'"                  # check 失败时执行
```

### 完整 YAML 模板示例

```yaml
# ~/.mini_ai/workflows/code_review.yaml
tasks:
  - id: code
    agent: subagent:coder
    prompt: "实现以下需求: {requirement}"
    depends_on: []

  - id: test
    agent: subagent:coder
    prompt: "为以下代码编写单元测试:\n{code}"
    depends_on: [code]

  - id: review
    agent: subagent:researcher
    prompt: "审查代码质量和测试覆盖:\n代码: {code}\n测试: {test}"
    depends_on: [code, test]

  - id: fix
    agent: subagent:coder
    prompt: "根据审查意见修复: {review}"
    depends_on: [review]
    condition: "'问题' in review['result'] or '修复' in review['result']"
    max_retry: 2
```

### Workflow vs Team 选型

| 场景 | 推荐 | 原因 |
|------|------|------|
| A 的结果传给 B | workflow | DAG 自动传递，无需手动 |
| A 和 B 需要来回对话 | team spawn | 需要 P2P 多轮通信 |
| 固定流程反复使用 | workflow YAML 模板 | 一次定义多次复用 |
| 临时组队完成一件事 | team spawn | 灵活，无需预定义 |
| 需要条件判断/重试 | workflow | DAG 内置 condition + retry |
| 队友需要长期驻守 | team spawn (idle_timeout=0) | 跨多轮保持 |

### 查看执行状态

执行中或完成后：
```
你: 查看工作流状态
```

输出示例：
```
工作流状态:
  ✅ [research] researcher — done
  ▶️ [design] architect (依赖: research) — running
  ⏳ [code] coder (依赖: design) — pending
  进度: 1/3
```
