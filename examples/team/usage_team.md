# Team 协作系统使用说明

## 概述

Team 系统让 lead 组建一支 Agent 团队，持久队友在后台独立运行，通过消息邮箱通信协作。相比 subagent（一次性同步调用），team 适合需要**多轮交互、角色分工、长期协作**的场景。

## 工具列表

| 工具 | 暴露对象 | 说明 |
|------|----------|------|
| `spawn_teammate(name, role, prompt)` | lead | 召入/唤醒队友 |
| `send_message(to, content, msg_type?)` | lead + teammates | 发送消息 |
| `broadcast(content)` | lead | 广播给所有队友 |
| `list_teammates()` | lead + teammates | 查看队友状态 |
| `dismiss_team()` | lead | 解散全部队友 |
| `read_inbox()` | teammates（自动） | 读取邮箱（teammate 内部自动调用） |

## 队友生命周期

```
spawn → working → idle → (收到新消息) → working → idle → ...
                    │                                  │
                    └── idle 超时 (300s) ──→ 自动退出   └── dismiss_team → 退出
```

- 队友在 **idle 超时**后自动退出（默认 300 秒，`config.yaml` 的 `teammate.idle_timeout` 可调）
- 也可通过 `dismiss_team` 主动解散
- 不再有 auto-shutdown——队友可跨多轮用户对话保持存活

## 使用示例

### 示例 1：基础协作（coder + reviewer）

```
用户: 帮我实现一个 JSON schema 校验器，让 coder 写代码，reviewer 审查

Lead 行为:
1. spawn_teammate(name="coder", role="coder", prompt="实现 JSON schema 校验器")
2. spawn_teammate(name="reviewer", role="reviewer", prompt="等待 coder 完成后审查代码")

coder 执行:
- 编写代码
- send_message(to="reviewer", content="代码完成，请审查: ...")  ← P2P 通信
- send_message(to="lead", content="编码完成")

reviewer 执行:
- 收到 coder 的消息
- 审查代码
- send_message(to="lead", content="审查结果: ...")
```

### 示例 2：使用黑板共享数据

```
用户: 三个人分工搜索不同来源

spawn researcher_a → "在 arxiv 搜索，结果写入 blackboard key=arxiv"
spawn researcher_b → "在 GitHub 搜索，结果写入 blackboard key=github"
spawn researcher_c → "在博客搜索，结果写入 blackboard key=blogs"

各 researcher 执行:
- blackboard_write("arxiv", "论文结果...")
- send_message(to="lead", content="搜索完成")

Lead 收到所有回禀后:
- blackboard_read("arxiv"), blackboard_read("github"), blackboard_read("blogs")
- 综合分析
```

### 示例 3：P2P 直接协作

Teammate 之间可以直接通信，不经过 lead：

```
coder 需要 researcher 帮忙查一个 API：
1. list_teammates() → 发现 researcher 在队中
2. send_message(to="researcher", content="帮我查 XXX API 的文档")
3. researcher 收到，查询，回复:
   send_message(to="coder", content="API 文档: ...")
4. coder 收到文档，继续编码
```

### 示例 4：与工作流结合

Team 和 Workflow 可以互补使用：

- **简单并行任务** → `run_workflow`（自动编排，无需手动管理）
- **需要交互/迭代的任务** → `spawn_teammate`（支持多轮通信）
- **混合场景** → workflow 中的 agent 字段指定 teammate 名，由 orchestrator 自动派遣

## P2P 通信

队友现在可以：
- **发现彼此**：通过 `list_teammates()` 查看团队成员
- **直接通信**：通过 `send_message(to="其他队友名", content="...")` 直接发消息
- **任务传递**：A 完成后可以直接将结果传给 B，无需经过 lead 中转

## 配置参数

```yaml
teammate:
  max_teammates: 10        # 最大队友数
  max_turns: 20            # 每个队友每轮最大 LLM 调用次数
  idle_timeout: 300        # 空闲超时（秒），0 表示不超时
  base_tools:              # 基础工具白名单
    - run_command
    - web_fetch
    - load_skill
```

## Team vs Subagent vs Workflow 选型

| 场景 | 推荐方式 | 原因 |
|------|----------|------|
| 并行搜索 3 个网站 | subagent 或 workflow | 无需交互，一次性任务 |
| 编码 + 审查接力 | team | 需要 P2P 通信 |
| 研究→设计→编码→测试 | workflow | 有明确依赖链 |
| 长期驻守的助手角色 | team (idle_timeout=0) | 跨多轮对话保持 |
| 失败自动重试 | workflow (max_retry) | DAG 内置重试 |
| 条件分支（成功/失败走不同路径） | workflow (condition) | DAG 内置条件 |
