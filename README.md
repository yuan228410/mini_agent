# yzx_agent

基于 OpenAI Chat Completions API 的智能对话 Agent，支持工具调用、技能系统、记忆压缩。

## 快速开始

```bash
pip install requests pyyaml
python main.py
```

## 项目结构

```
yzx_agent/
├── main.py              # 入口，编排主循环（保持简洁，~50行）
├── llm.py               # LLM API 通信
├── runner.py             # 可复用的 Agent 执行循环
├── config.py             # 配置加载
├── config.yaml           # 模型与服务配置
├── logger.py             # 日志模块（双输出：终端+文件）
├── context.py            # 系统提示词组装
├── memory.py             # 三层记忆存储
├── compactor.py          # 对话压缩归档
├── skills.py             # 技能加载器
├── character/            # Agent 人设定义
│   ├── SOUL.md           #   核心身份、能力、工作流程
│   └── RULES.md          #   行为规范、任务规划要求
├── tools/                # 工具系统（每个工具一个py文件）
│   ├── __init__.py       #   注册、分发、并行执行
│   ├── run_command.py    #   Shell 命令执行
│   ├── web_fetch.py      #   网页抓取
│   ├── list_skills.py    #   列出可用技能
│   ├── load_skill.py     #   加载技能内容
│   ├── update_todos.py   #   任务规划与状态跟踪
│   └── dispatch_subagent.py  # 子代理调度
├── subagents/            # 子代理定义
│   ├── __init__.py       #   SubagentLoader
│   ├── coder.md          #   代码工程师
│   └── researcher.md     #   信息检索员
├── skills/               # 技能文件目录
├── memory_data/          # 运行时记忆数据（不入 git）
└── logs/                 # 运行日志（不入 git）
```

## 架构设计

### 主循环 (main.py)

```
用户输入 → 检查是否需要规划(update_todos)
         → chat(LLM) → 有 tool_calls? → 并行/串行执行工具 → 再次 chat
                                     ↓ 无
                               输出回复 → 存储 → 检查压缩
```

### 任务规划 (update_todos)

模型收到复杂任务后自动拆解为待办列表：
- `pending` → `in_progress` → `completed` 三态推进
- 同一时间最多一个 in_progress
- 每次全量覆盖更新
- 状态持久化在内存中，压缩不会丢失

### 子代理系统 (subagents/ + dispatch_subagent)

派遣独立子代理执行并行任务，隔离上下文：

| 子代理 | 工具 | 用途 |
|--------|------|------|
| researcher | run_command, web_fetch, load_skill | 信息搜索与分析 |
| coder | run_command, load_skill | 代码编写与修改 |

**特点：**
- 工具白名单：子代理只能使用指定工具
- 轮次限制：每个子代理有 max_turns 上限
- 并行执行：多个 dispatch_subagent 通过 ThreadPoolExecutor 并行运行
- 上下文隔离：子代理内部历史不回传，只返回最终结果

新增子代理：在 `subagents/` 下创建 `xxx.md` 文件即可。
```markdown
---
name: my-agent
description: 我的子代理
tools: run_command, web_fetch
max_turns: 10
---
你是一个...（system prompt）
```

### 日志系统 (logger.py)

- 终端：仅显示聊天内容和 WARNING+ 级别消息
- 文件 `logs/YYYYMMDD.log`：DEBUG 级别，记录工具调用、任务计划、子代理派遣、压缩归档等所有系统事件

### 记忆系统 (memory.py + compactor.py)

**三层存储模型：**

| 层级 | 存储 | 生命周期 | 说明 |
|------|------|----------|------|
| 原始层 | `history.jsonl` | 持久 | 完整对话日志，按 compact_event 标记归档状态 |
| 情景层 | `YYYY-MM-DD.md` | 按日 | 每日对话的关键信息摘要 |
| 长期层 | `MEMORY.md` | 持久 | 核心目标、重要决策、项目背景 |

**压缩触发条件：**
- 非系统消息数 > 10 条
- 且总字符数 > 8000

**压缩流程：**
1. 保留最近 10 条消息
2. 旧消息发送给模型，提取三个维度的结构化输出：
   - `<episode>` — 写入今日情景记忆
   - `<updated_memory>` — 更新长期记忆
   - `<updated_user>` — 更新用户画像
3. 写入 compact_event 标记，注入更新后的长期记忆到上下文

### 上下文组装 (context.py)

按优先级拼接系统提示词：

```
SOUL.md (核心身份)
---
长期记忆 (MemoryStore)
---
用户画像 (MemoryStore)
---
可用技能 (SkillLoader)
---
RULES.md (行为规范)
```

### 工具系统 (tools/)

每个工具是一个独立模块，导出两个接口：
- `definition` — OpenAI Function Calling 的工具定义
- `execute(args)` — 工具执行函数

添加新工具：创建 `tools/新工具.py`，在 `tools/__init__.py` 的 `_ALL_TOOLS` 中注册。

### 技能系统 (skills.py)

技能存放在 `skills/<名称>/SKILL.md`，使用 YAML frontmatter 定义元数据：

```markdown
---
name: skill-name
description: 简短描述
tags: tag1,tag2
---
技能正文（Markdown 格式）
```

模型通过 `load_skill` 工具按需加载技能内容。

## 配置

编辑 `config.yaml`：

```yaml
model:
  api_url: "https://your-api.com/v1/chat/completions"
  api_key: "your-api-key"
  model: "your-model-name"
```

## 自定义 Agent 人设

编辑 `character/` 目录下的文件：
- `SOUL.md` — 定义 Agent 的角色定位和核心能力
- `RULES.md` — 定义回复风格和行为规范