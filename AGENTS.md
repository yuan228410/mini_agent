# mini_ai 项目规范

## 项目概述

基于 OpenAI/Anthropic Chat API 的对话 Agent，支持多模型切换、流式输出、工具系统、技能系统、三层记忆、Team 协作、CLI + Web 双界面。

## 目录结构（关键路径）

```
src/mini_ai/
├── main.py              # CLI 主循环
├── runner.py            # Agent 执行器（子代理/队友/Web 共用）
├── context.py           # system prompt 组装
├── config.py            # 配置加载
├── llm/                 # LLM 通信（openai / anthropic 协议）
├── cli/                 # CLI 交互（display + commands）
├── memory/              # 记忆系统（store + compactor + history_db + session）
├── tools/               # 工具模块，每个文件一个工具
├── team/                # 多 Agent 编排
├── web/                 # FastAPI + Vue 3 前端
└── character/           # 人设（SOUL.md + RULES.md）
```

## 架构原则（Agent 需要知道的）

- **模块化**：一个文件一个职责
- **工具模式**：`tools/xxx.py` 导出 `definition` + `execute(args)` + 可选 `configure(**kwargs)`，通过 ToolRegistry 注册
- **记忆系统**：三层（情景/长期/画像）+ HistoryDB（SQLite 全文搜索）+ Compactor（自动摘要），`remember`/`recall`/`forget` 主动工具
- **工作空间**：CLI 按 CWD 自动绑定，Web 手动管理，每个工作空间独立记忆/历史
- **上下文隔离**：子代理/队友的回禀不回传主循环
- **错误恢复**：LLM 429/5xx 自动重试 3 次，工具缺参返回错误而非抛异常，连续 3 次工具错误提前退出
- **依赖注入**：工具通过 `configure(**kwargs)` 注入外部依赖
- **配置分离**：运行时走 `~/.mini_ai/config.yaml`，包内数据只读

## 行为规则

**不要主动提交代码** — 除非用户明确说"提交"或"commit"，否则只报告完成状态

### 先想后写
- 不确定就问，不假装理解；有多个方案摆出来，不默默选
- 更简单的方案可行就说，该推回去就推回去

### 最小代码
- 只解决被问到的问题，不多写一行
- 不做单次用例的抽象，不搞没要求的灵活性
- 写多了就重写：200 行能 50 行写完，别留

### 外科手术式修改
- 只动必须动的行，不改相邻代码/注释/格式
- 匹配现有风格，自己的孤儿自己删
- 已有的死代码提一句即可，不要顺手删

### 目标驱动
- 模糊任务翻译成可验证目标："加验证" → "先写测试覆盖非法输入，再让测试通过"
- 多步任务先列计划，每步附验证方式