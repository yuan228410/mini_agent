# yzx_agent 项目规范

## 项目概述

基于 OpenAI Chat Completions API 的智能对话 Agent，支持工具调用、技能系统、三层记忆压缩。

## 目录结构

```
main.py              # 入口，编排主循环（保持简洁，~50行）
llm.py               # LLM API 通信
config.py             # 配置加载
context.py            # 系统提示词组装
memory.py             # 三层记忆存储（MemoryStore）
compactor.py          # 对话压缩归档（Compactor）
skills.py             # 技能加载器（SkillLoader）
character/            # Agent 人设（SOUL.md + RULES.md）
tools/                # 工具系统（每个工具一个py文件）
skills/               # 用户技能（SKILL.md，不入 git）
memory_data/          # 运行时记忆数据（不入 git）
```

## 架构原则

- **模块化**：一个文件一个职责，不要把所有逻辑堆在 main.py
- **工具系统**：新工具 = `tools/xxx.py`（导出 `definition` + `execute(args)`），在 `tools/__init__.py` 注册
- **配置分离**：模型相关配置走 `config.yaml`，通过 `config.py` 加载
- **记忆系统**：MemoryStore（存储）+ Compactor（压缩），触发条件：消息 > 10 且字符 > 8000

## 行为规则

- **不要主动提交代码**：除非用户明确说"提交"或"commit"，否则只报告完成状态
- **执行命令时优先用 rtk**：`rtk <command>` 压缩输出，减少 token 消耗
- **代码不加注释**：默认不写注释，只在 WHY 非显而易见时加一行简短注释
- **不设计过度**：不写半成品实现，三个类似行比过早抽象好