# mini_ai 功能示例

本目录按模块组织功能示例和使用说明。

## 目录结构

```
examples/
├── README.md              # 本文件
└── team/                  # 多 Agent 编排（Team + Workflow + Blackboard）
    ├── workflow_basic.yaml        # 基础工作流模板（链式 + 并行）
    ├── workflow_conditional.yaml  # 条件分支工作流模板
    ├── usage_blackboard.md        # Blackboard 共享黑板使用说明
    ├── usage_workflow.md          # DAG 工作流编排使用说明
    └── usage_team.md              # Team 协作系统使用说明
```

## 后续规划

| 子目录 | 模块 |
|--------|------|
| `team/` | 多 Agent 编排 |
| `skills/` | 技能系统示例 |
| `memory/` | 记忆与压缩 |
| `tools/` | 自定义工具开发 |
