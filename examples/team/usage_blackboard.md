# Blackboard 共享黑板使用说明

## 概述

Blackboard 是 Agent 间共享数据的键值存储。所有 teammate 和 lead 都可以读写，用于在多个 Agent 之间传递搜索结果、分析结论、代码片段等。

## 工具列表

| 工具 | 说明 | 可用者 |
|------|------|--------|
| `blackboard_write(key, value)` | 写入数据 | lead + teammates |
| `blackboard_read(key)` | 读取数据 | lead + teammates |
| `blackboard_list(prefix?)` | 列出 key | lead + teammates |

## 使用场景

### 场景 1：Agent 间传递搜索结果

```
用户: 帮我搜索 RAG 技术，然后让 coder 根据搜索结果写代码

Lead 行为:
1. spawn researcher → "搜索 RAG 技术，结果写入 blackboard key=rag_research"
2. researcher 执行: blackboard_write("rag_research", "RAG 最新论文...")
3. researcher 回禀 lead
4. spawn coder → "从 blackboard 读取 rag_research，据此编写代码"
5. coder 执行: blackboard_read("rag_research") → 获取搜索结果
6. coder 编码完成，回禀 lead
```

### 场景 2：工作流中自动传递

在 `run_workflow` 中，每个 task 完成后结果**自动写入 blackboard**（key = task_id）。后续 task 的 prompt 中用 `{task_id}` 引用：

```json
{
  "tasks": [
    {"id": "search", "agent": "researcher", "prompt": "搜索 X"},
    {"id": "code", "agent": "coder", "prompt": "根据搜索结果编码: {search}", "depends_on": ["search"]}
  ]
}
```

`{search}` 会被替换为 search task 的实际结果。

### 场景 3：多 Agent 协作累积知识

```
用户: 团队合作分析这个系统

researcher 写入: blackboard_write("arch_analysis", "架构分析...")
coder 写入: blackboard_write("code_review", "代码审查...")
reviewer 读取两者: blackboard_read("arch_analysis"), blackboard_read("code_review")
reviewer 综合分析后写入: blackboard_write("final_report", "综合报告...")
```

## 特性

- **线程安全**：内部使用 `threading.Lock`，并发读写安全
- **可选持久化**：数据存储在 `~/.mini_ai/.team/blackboard.json`，重启不丢失
- **作者追踪**：每次写入记录作者名，便于溯源
- **命名空间**：用 prefix 约定组织 key，如 `research.xxx`、`code.xxx`
- **自动淘汰**：达到 500 条上限时自动淘汰最旧条目，防止内存无限增长
- **空值安全**：明确区分"key 不存在"和"值为空"，避免误读

## 注意事项

- 黑板是全局共享的，所有 Agent 可见——不要写入敏感信息
- key 命名建议简洁有意义（`search_result`、`design_doc`）
- 大量数据建议分 key 存储，避免单个 value 过大
- 工作流结束后黑板数据仍保留，下次工作流可复用
