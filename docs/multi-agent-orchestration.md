# Team Agent 多 Agent 编排 — 架构分析与演进方案

## 一、现状分析

### 1.1 当前架构

```
            USER
             │
             ▼
     ┌──────────────┐
     │   LEAD (主线程) │──── tools: spawn_teammate, send_message, broadcast
     └──────┬───────┘
            │ spawn (daemon threads)
     ┌──────┼──────────┐
     ▼      ▼          ▼
  Teammate_A  Teammate_B  Teammate_C
  (独立线程)  (独立线程)   (独立线程)
```

**双执行模式并存：**

| 模式 | 入口 | 生命周期 | 上下文 | 通信 |
|------|------|----------|--------|------|
| Subagent (`dispatch_subagent`) | 同步调用 `run_agent()` | 一次性，立即返回结果 | 完全隔离 | 无（call/return） |
| Teammate (`spawn_teammate`) | daemon 线程，inbox 驱动 | 持久，但每轮 auto-shutdown | 每任务重置 | 文件 JSONL 邮箱 |

### 1.2 消息系统（team_bus.py）

- **存储**：`~/.mini_ai/.team/inbox/{name}.jsonl`，追加写入
- **读取**：read_inbox 时一次性取出并清空
- **消息类型**：`message`、`broadcast`、`shutdown_request`、`shutdown_response`
- **容量限制**：100KB/inbox，超出报错
- **唤醒机制**：`threading.Event` + `set_wake_callback`，0ms 响应

### 1.3 当前能力边界

| 能力 | 支持情况 | 代码位置 |
|------|----------|----------|
| 并行 fan-out | ✅ 多个 spawn 并行执行 | `tools/__init__.py` `_PARALLEL_TOOLS` |
| 结果汇总（fan-in） | ✅ lead 阻塞等待，逐条注入 | `team_loop.py:wait_for_teammates` |
| teammate→lead 回禀 | ✅ send_message | `team_tools.py:_send` |
| teammate→teammate 通信 | ⚠️ 技术可行，未启用 | bus 支持任意 name，但提示词 + 白名单限制 |
| 任务链/DAG | ❌ | — |
| 共享状态/黑板 | ❌ | — |
| 跨轮持久化协作 | ❌ | `main.py` 每轮 `shutdown_teammates()` |
| 条件分支/路由 | ❌ | 依赖 lead LLM 判断 |
| 错误重试/fallback | ❌ | 异常只发一条错误消息 |
| 结构化输出合约 | ❌ | 结果为自由文本 |

---

## 二、痛点详解

### 2.1 Agent 间无法直接协作

**现状**：所有通信必须经过 lead 中转。Teammate A 想把结果传给 Teammate B，流程是：
```
A → send_message("lead", result) → lead 收到 → lead LLM 决策 → send_message("B", task) → B 收到
```
这带来 **2 次额外 LLM 调用** 的开销和延迟。

**根因**：
1. Teammate 系统提示词只指示回禀 lead（`team_manager.py:122`）
2. Teammate 不知道其他 teammate 的名字（`list_teammates` 不在其工具白名单中）
3. 没有"任务传递"语义——只有"消息"

### 2.2 无任务依赖/DAG

**现状**：lead 一次 spawn 多个 teammate，它们各自独立执行，无法表达：
- "A 完成后把结果给 B"
- "B 和 C 并行，都完成后 D 汇总"
- "如果 A 失败，换 E 重试"

**根因**：
1. `spawn_teammate` 没有 `depends_on`、`after` 等参数
2. 没有任务注册表——spawn 后 lead 只能被动等待全部完成
3. `team_loop.py:wait_for_teammates` 是"等全部 idle/完成"，无法按依赖触发下一步

### 2.3 无共享状态

**现状**：每个 teammate 的 messages 在任务完成后重置为 `[system_prompt]`（`team_manager.py:172`）。无法：
- 共享已发现的信息（搜索结果、分析结论）
- 读取其他 agent 的工作产物
- 维护跨 agent 的知识库

**根因**：
1. 硬重置 `messages = [messages[0]]`
2. 没有 shared memory / blackboard 机制
3. 工具白名单不含 `read_file`/`write_file`

### 2.4 Auto-shutdown 破坏持久协作

**现状**：`main.py` 每轮用户对话结束后无条件执行 `shutdown_teammates()`。
- 用户说"继续让 coder 改下这个 bug" → coder 已经被 shutdown
- 复杂项目需要反复交互 → 每次都重新 spawn，重新加载上下文

**根因**：`shutdown_teammates()` 无条件调用，没有"保持活跃"的选项

---

## 三、演进方案设计

### 3.1 方案总览：轻量 DAG + 黑板 + 持久化

不引入外部框架，在现有架构基础上渐进增强：

```
            USER
             │
             ▼
     ┌──────────────┐
     │   LEAD        │──── 可调用 run_workflow 定义 DAG
     └──────┬───────┘
            │
     ┌──────┴──────┐
     │  Orchestrator │──── DAG 调度器（纯 Python，非 LLM）
     └──────┬───────┘
            │ dispatch ready tasks
   ┌────────┼────────────┐
   ▼        ▼            ▼
 Agent_A  Agent_B      Agent_C     (teammate 或 subagent)
   │        │            │
   └────────┴────────────┘
            │ read/write
     ┌──────┴──────┐
     │  Blackboard  │  ← 共享状态（内存 dict + 可选文件持久化）
     └─────────────┘
```

### 3.2 模块设计

#### 模块 1：Blackboard（共享黑板）

**新文件**：`src/mini_ai/blackboard.py`

```python
class Blackboard:
    """Agent 间共享的键值状态存储，线程安全"""

    def __init__(self, persist_path: Path | None = None):
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path  # 可选 JSON 持久化

    def put(self, key: str, value: str, author: str = "") -> str:
        """写入状态，返回确认"""

    def get(self, key: str, default: str = "") -> str:
        """读取状态"""

    def list_keys(self, prefix: str = "") -> list[str]:
        """列出匹配前缀的 key"""

    def snapshot(self) -> dict[str, str]:
        """返回当前全量快照"""

    def clear(self):
        """清空"""
```

**工具暴露**（`tools/blackboard_tools.py`）：
- `blackboard_write(key, value)` — 写入共享状态
- `blackboard_read(key)` — 读取共享状态
- `blackboard_list(prefix?)` — 浏览可用 key

**加入 teammate 工具白名单**：让 agent 间通过黑板间接协作。

#### 模块 2：TaskGraph（轻量 DAG）

**新文件**：`src/mini_ai/task_graph.py`

```python
@dataclass
class TaskNode:
    id: str                     # 唯一标识
    agent: str                  # teammate name 或 "subagent:type"
    prompt: str                 # 任务描述，支持 {变量} 占位符
    depends_on: list[str]       # 依赖的 task id 列表
    status: str = "pending"     # pending → running → done → failed
    result: str | None = None
    retry_count: int = 0
    max_retry: int = 1

class TaskGraph:
    """轻量 DAG 调度器 — 纯代码，确定性逻辑"""

    def __init__(self, blackboard: Blackboard):
        self.nodes: dict[str, TaskNode] = {}
        self.blackboard = blackboard

    def add_task(self, node: TaskNode):
        """注册任务节点"""

    def get_ready(self) -> list[TaskNode]:
        """返回所有依赖已满足（done）、状态为 pending 的任务"""

    def mark_done(self, task_id: str, result: str):
        """标记完成，结果写入 blackboard[task_id]"""

    def mark_failed(self, task_id: str, error: str):
        """标记失败，若 retry_count < max_retry 则重置为 pending"""

    def is_complete(self) -> bool:
        """所有节点 done 或 failed（无 retry 余量）"""

    def render_status(self) -> str:
        """文本渲染 DAG 状态，供 LLM 和 UI 查看"""
```

#### 模块 3：Orchestrator（编排调度循环）

**新文件**：`src/mini_ai/orchestrator.py`

```python
class Orchestrator:
    """DAG 驱动的多 agent 编排器"""

    def __init__(self, graph: TaskGraph, bus: MessageBus, manager: TeammateManager):
        ...

    def run(self) -> str:
        """执行 DAG 直到完成，返回汇总结果"""
        while not self.graph.is_complete():
            ready = self.graph.get_ready()
            for task in ready:
                prompt = self._resolve_prompt(task)
                self._dispatch(task, prompt)
                task.status = "running"
            self._wait_for_any_completion()
        return self._summarize()

    def _resolve_prompt(self, task: TaskNode) -> str:
        """替换 {dep_id} 占位符为依赖任务的结果"""

    def _dispatch(self, task: TaskNode, prompt: str):
        """根据 agent 类型派遣（teammate spawn 或 subagent dispatch）"""

    def _wait_for_any_completion(self):
        """等待任意一个 running 任务完成"""

    def _summarize(self) -> str:
        """汇总所有 done 节点的结果"""
```

#### 模块 4：持久化队友改造

**修改文件**：`team_manager.py` + `main.py`

改动点：
1. **取消 auto-shutdown**：`main.py` 的 `shutdown_teammates()` 改为按条件执行
2. **idle 超时**：`config.yaml` 新增 `team.idle_timeout`（如 300s），空闲超时才 shutdown
3. **keep_context 选项**：`spawn_teammate` 新增参数，为 True 时不重置 messages
4. **dismiss_team 工具**：lead 可主动解散队友

#### 模块 5：Peer-to-Peer 通信增强

**修改文件**：`team_manager.py`（系统提示词） + `team_tools.py`（白名单）

改动点：
1. Teammate 系统提示词增加："你可以通过 send_message 与其他队友直接通信"
2. `list_teammates` 加入 teammate 工具白名单（发现彼此）
3. 新增消息类型 `task_handoff` — 语义化的任务传递
4. 消息新增可选 `reply_to` 字段

### 3.3 工具扩展汇总

| 新工具 | 暴露对象 | 用途 |
|--------|----------|------|
| `blackboard_write(key, value)` | lead + teammates | 写入共享状态 |
| `blackboard_read(key)` | lead + teammates | 读取共享状态 |
| `blackboard_list(prefix?)` | lead + teammates | 浏览可用 key |
| `run_workflow(tasks)` | lead | 提交 DAG 定义并执行 |
| `workflow_status()` | lead | 查看当前 DAG 执行状态 |
| `dismiss_team()` | lead | 主动解散所有队友 |

### 3.4 DAG 定义格式（run_workflow 参数）

```json
{
  "tasks": [
    {
      "id": "search",
      "agent": "researcher",
      "prompt": "搜索 RAG 技术的最新论文和工程实践",
      "depends_on": []
    },
    {
      "id": "design",
      "agent": "architect",
      "prompt": "基于搜索结果设计系统架构。搜索结果见: {search}",
      "depends_on": ["search"]
    },
    {
      "id": "code",
      "agent": "coder",
      "prompt": "根据架构设计实现 demo。设计文档: {design}",
      "depends_on": ["design"]
    },
    {
      "id": "review",
      "agent": "reviewer",
      "prompt": "审查代码实现，检查是否符合设计。代码: {code}",
      "depends_on": ["code"]
    }
  ]
}
```

`{task_id}` 占位符在运行时被替换为该 task 的结果（从 blackboard 读取）。

### 3.5 执行流程示例

```
用户: "帮我研究 RAG 技术并写个 demo"

Lead LLM 决策 → 调用 run_workflow(tasks=[...])

Orchestrator 启动:
  ┌─ [search] ready (无依赖) → spawn researcher
  │
  │  researcher 执行搜索...完成
  │  → blackboard["search"] = "RAG 最新进展..."
  │  → graph.mark_done("search")
  │
  ├─ [design] ready (search done) → spawn architect
  │  prompt 中 {search} 被替换为 blackboard["search"]
  │
  │  architect 设计完成
  │  → blackboard["design"] = "架构方案..."
  │
  ├─ [code] ready (design done) → spawn coder
  │  prompt 中 {design} 被替换为 blackboard["design"]
  │
  │  coder 实现完成
  │  → blackboard["code"] = "实现代码..."
  │
  └─ [review] ready (code done) → spawn reviewer
     prompt 中 {code} 被替换为 blackboard["code"]

     reviewer 审查完成
     → blackboard["review"] = "审查通过..."

全部完成 → Orchestrator 汇总返回 lead → lead 输出给用户
```

---

## 四、实现优先级建议

### Phase 1 — 最小可用（1-2 天）

| 任务 | 改动量 | 说明 |
|------|--------|------|
| Blackboard 模块 | 新建 1 文件 ~80 行 | 内存 dict + 线程锁 + 3 个工具 |
| 取消 auto-shutdown | 改 `main.py` 2 行 | 改为 `dismiss_team` 工具控制 |
| Teammate 白名单扩展 | 改 `team_manager.py` 1 行 | 加入 `blackboard_read`/`blackboard_write`/`list_teammates` |

**效果**：Teammate 可通过黑板间接协作，跨轮对话保持存在。

### Phase 2 — 核心编排（2-3 天）

| 任务 | 改动量 | 说明 |
|------|--------|------|
| TaskGraph DAG 调度器 | 新建 1 文件 ~120 行 | 依赖解析 + 状态追踪 |
| Orchestrator 循环 | 新建 1 文件 ~100 行 | DAG 驱动派遣 + 等待 + 汇总 |
| `run_workflow` 工具 | 新建 1 工具文件 | lead 提交 DAG 定义 |
| `workflow_status` 工具 | 同上合并 | 查看 DAG 进度 |

**效果**：支持任务链、并行分支、结果传递的完整 DAG 编排。

### Phase 3 — 体验增强（1-2 天）

| 任务 | 改动量 | 说明 |
|------|--------|------|
| Peer-to-peer 通信 | 改提示词 + 白名单 | Teammate 发现彼此并直接通信 |
| `keep_context` 选项 | 改 `team_manager.py` | 可选保留上下文 |
| Workflow YAML 模板 | 支持 `workflows/` 目录 | 预定义工作流可复用 |
| idle 超时 | 改 `team_manager.py` | 空闲 N 秒后自动 shutdown |

### Phase 4 — 进阶可选

| 任务 | 说明 |
|------|------|
| 条件分支 | DAG 边支持 `condition` 表达式 |
| 错误重试 | TaskNode 的 retry 逻辑 |
| Workflow checkpoint | 持久化 DAG 状态，支持断点恢复 |
| Web 端 DAG 可视化 | 状态面板展示任务流图 |

---

## 五、与现有代码的兼容性

| 现有模块 | 改动程度 | 说明 |
|----------|----------|------|
| `team_bus.py` | 微改 | 新增 `task_handoff` 消息类型到 `_VALID_TYPES` |
| `team_manager.py` | 中改 | idle 超时 + `keep_context` + 系统提示词调整 |
| `team_loop.py` | 小改 | Orchestrator 模式下跳过 `wait_for_teammates` |
| `tools/team_tools.py` | 扩展 | 新增工具模块引用 |
| `tools/__init__.py` | 小改 | 注册新工具 |
| `main.py` | 小改 | 去掉无条件 shutdown，改为条件判断 |
| `runner.py` | **不改** | 继续作为底层执行器 |
| `config.yaml` | 扩展 | 新增 `team.idle_timeout`、`blackboard.persist` |

**所有现有功能保持不变**——新模块作为增量添加，不破坏原有 spawn/send/broadcast 模式。

---

## 六、架构对比

| 维度 | 当前 | 改进后 |
|------|------|--------|
| 拓扑 | 星形（lead 中心） | 星形 + P2P + DAG |
| 通信 | 异步邮箱，仅 lead↔teammate | 邮箱 + 黑板 + 任务传递 |
| 调度 | LLM 即时决策 | LLM 定义 DAG → 代码自动编排 |
| 状态 | 完全隔离 | 黑板共享 + 可选上下文保留 |
| 生命周期 | 单轮（auto-shutdown） | 持久 + idle 超时 |
| 错误处理 | 仅报错消息 | 重试 + fallback（Phase 4） |
| 工作流定义 | 无 | JSON（动态）+ YAML（预定义） |

---

## 七、关键设计决策

### Q1: Orchestrator 是纯代码还是 LLM？

**推荐纯代码。** DAG 调度是确定性逻辑（检查依赖是否满足 → 派遣就绪任务），不需要 LLM 参与。LLM 的角色是：
- **定义 DAG**（通过 `run_workflow` 工具传入 tasks JSON）
- **执行单个节点**（每个 task 内部的 agent 仍用 LLM）

这样做的好处：
- 零额外 token 消耗用于调度
- 确定性保证——不会因为 LLM 幻觉导致调度错乱
- 速度快——纯 Python 检查依赖是微秒级

### Q2: Blackboard vs 文件系统共享？

**推荐 Blackboard。**
- 文件系统：太慢、没有语义、teammate 目前不能 write_file
- Blackboard：结构化 key-value、线程安全、可选持久化、有命名空间

### Q3: DAG 由 LLM 动态生成还是预定义？

**两者都支持：**
- `run_workflow(tasks=[...])` — LLM 根据用户需求动态生成 DAG
- `workflows/xxx.yaml` — 预定义的常用工作流模板（LLM 通过 `load_workflow` 加载）

### Q4: Teammate vs Subagent 统一？

**不统一，但互通：**
- Subagent 适合简单同步任务（搜索、文件分析）— 开销小
- Teammate 适合需要通信和迭代的复杂任务 — 有状态
- DAG 中两种都可作为执行节点（`agent` 字段支持 `"coder"` 或 `"subagent:researcher"`）

### Q5: Lead 如何知道什么时候用 DAG vs 普通 spawn？

**LLM 自行判断：**
- 简单并行任务（如"搜索 3 个网站"）→ 直接 spawn，无需 DAG
- 有依赖关系的复杂任务（如"研究→设计→编码→审查"）→ 用 `run_workflow`
- 通过 system prompt 中的工具描述和少量示例引导 LLM 选择

---

## 八、与主流 Multi-Agent 框架的对标

| 特性 | mini_ai（改进后） | CrewAI | LangGraph | OpenAI Swarm |
|------|-------------------|--------|-----------|--------------|
| DAG/任务链 | ✅ 轻量 TaskGraph | ✅ Sequential/Hierarchical | ✅ 完整图引擎 | ❌ |
| 共享状态 | ✅ Blackboard | ✅ Shared Memory | ✅ Graph State | ⚠️ Context vars |
| P2P 通信 | ✅ 邮箱互通 | ❌ | ❌ | ✅ Handoff |
| 持久化 | ✅ 文件 JSONL | ❌ | ✅ Checkpoint | ❌ |
| 条件路由 | Phase 4 | ✅ | ✅ Conditional edges | ✅ Handoff functions |
| 动态 DAG | ✅ LLM 生成 | ❌ 预定义 | ⚠️ 需代码定义 | ❌ |
| 外部依赖 | 零 | langchain 生态 | langchain 生态 | openai SDK |
| 学习成本 | 低（纯 Python + 现有模式） | 中 | 高 | 低 |

**定位差异**：mini_ai 追求"零框架依赖、从零理解机制"，改进后在能力上接近 CrewAI 的 hierarchical 模式 + LangGraph 的基础 DAG，但实现更轻量、更透明。
