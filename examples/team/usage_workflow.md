# DAG 工作流编排使用说明

## 概述

`run_workflow` 工具让 LLM 定义一个任务依赖图（DAG），系统自动按依赖顺序编排执行：
- 无依赖的任务**并行**执行
- 有依赖的任务等前置完成后**自动触发**
- 支持**条件分支**（condition 不满足则跳过）
- 支持**失败重试**（max_retry 控制次数）

## 工具列表

| 工具 | 说明 |
|------|------|
| `run_workflow(tasks)` | 提交 DAG 定义并执行 |
| `workflow_status()` | 查看执行状态 |
| `load_workflow(name)` | 加载预定义 YAML 模板 |

## DAG 定义格式

```json
{
  "tasks": [
    {
      "id": "唯一标识",
      "agent": "执行者名称",
      "prompt": "任务描述，支持 {dep_id} 引用依赖结果",
      "depends_on": ["依赖的 task id 列表"],
      "condition": "可选，Python 表达式，false 时跳过",
      "max_retry": 1
    }
  ]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✅ | 任务唯一标识，也作为 blackboard key |
| `agent` | ✅ | 执行者。teammate 名（如 `coder`）或 `subagent:type`（如 `subagent:researcher`） |
| `prompt` | ✅ | 任务描述。`{task_id}` 被替换为依赖任务结果，`{blackboard.key}` 引用黑板数据 |
| `depends_on` | ❌ | 依赖列表，默认空（立即执行） |
| `condition` | ❌ | 条件表达式。可用 `dep_id['status']`、`dep_id['result']`、`dep_id['error']` |
| `max_retry` | ❌ | 失败重试次数，默认 1（不重试） |

## 执行示例

### 示例 1：链式执行（研究 → 设计 → 编码）

```
用户: 帮我研究 WebSocket 并实现一个聊天室

LLM 调用 run_workflow:
{
  "tasks": [
    {"id": "research", "agent": "subagent:researcher", "prompt": "搜索 WebSocket 实时通信最佳实践"},
    {"id": "design", "agent": "subagent:researcher", "prompt": "设计聊天室架构: {research}", "depends_on": ["research"]},
    {"id": "code", "agent": "coder", "prompt": "实现聊天室: {design}", "depends_on": ["design"]}
  ]
}

执行流程:
  [research] ▶️ 执行中...
  [research] ✅ 完成 → blackboard["research"] = 结果
  [design]   ▶️ prompt 中 {research} 被替换为实际结果
  [design]   ✅ 完成 → blackboard["design"] = 结果
  [code]     ▶️ prompt 中 {design} 被替换为设计文档
  [code]     ✅ 完成
```

### 示例 2：并行 fan-out + 汇总

```json
{
  "tasks": [
    {"id": "search_arxiv", "agent": "subagent:researcher", "prompt": "在 arxiv 搜索 LLM agent 论文"},
    {"id": "search_github", "agent": "subagent:researcher", "prompt": "在 GitHub 搜索 LLM agent 开源项目"},
    {"id": "search_blog", "agent": "subagent:researcher", "prompt": "搜索 LLM agent 技术博客"},
    {"id": "summary", "agent": "analyst", "prompt": "综合以下三个来源的调研结果写报告:\n\narxiv: {search_arxiv}\n\nGitHub: {search_github}\n\n博客: {search_blog}", "depends_on": ["search_arxiv", "search_github", "search_blog"]}
  ]
}

执行流程:
  [search_arxiv]  ▶️ ─┐
  [search_github] ▶️ ─┼─ 三个并行执行
  [search_blog]   ▶️ ─┘
  ... 全部完成 ...
  [summary]       ▶️ 汇总三个结果
```

### 示例 3：条件分支

```json
{
  "tasks": [
    {"id": "test", "agent": "coder", "prompt": "运行项目测试套件"},
    {"id": "fix", "agent": "coder", "prompt": "修复测试失败: {test}", "depends_on": ["test"], "condition": "'FAIL' in test['result']", "max_retry": 3},
    {"id": "deploy", "agent": "coder", "prompt": "部署到预发环境", "depends_on": ["test"], "condition": "'FAIL' not in test['result']"}
  ]
}

如果 test 结果包含 FAIL → 走 fix 分支（最多重试 3 次）
如果 test 通过 → 走 deploy 分支
```

## 预定义模板

将 YAML 文件放入 `~/.mini_ai/workflows/` 目录，可通过 `load_workflow` 加载：

```yaml
# ~/.mini_ai/workflows/research_and_code.yaml
tasks:
  - id: research
    agent: subagent:researcher
    prompt: "搜索 {topic}"
    depends_on: []
  - id: code
    agent: coder
    prompt: "根据调研结果实现: {research}"
    depends_on: [research]
```

对话中使用：
```
> 加载 research_and_code 模板，topic 是 "向量数据库"
```

## 状态查看

执行中或执行后使用 `workflow_status` 查看进度：

```
工作流状态:
  ✅ [research] researcher — done
  ▶️ [design] architect (依赖: research) — running
  ⏳ [code] coder (依赖: design) — pending
  进度: 1/3
```

状态图标：⏳ pending | ▶️ running | ✅ done | ❌ failed | ⏭️ skipped
