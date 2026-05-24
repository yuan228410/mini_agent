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
├── main.py              # 入口，编排主循环
├── llm.py               # LLM API 通信
├── config.py             # 配置加载
├── config.yaml           # 模型与服务配置
├── context.py            # 系统提示词组装
├── memory.py             # 三层记忆存储
├── compactor.py          # 对话压缩归档
├── skills.py             # 技能加载器
├── character/            # Agent 人设定义
│   ├── SOUL.md           #   核心身份与能力
│   └── RULES.md          #   行为规范
├── tools/                # 工具系统
│   ├── __init__.py       #   注册、分发、执行
│   ├── run_command.py    #   Shell 命令执行
│   ├── web_fetch.py      #   网页抓取
│   ├── list_skills.py    #   列出可用技能
│   └── load_skill.py     #   加载技能内容
├── skills/               # 技能文件目录
│   └── code-review/      #   示例：代码审查技能
│       └── SKILL.md
└── memory_data/          # 运行时生成的记忆数据
    ├── history.jsonl     #   原始对话日志
    ├── YYYY-MM-DD.md     #   每日情景记忆
    ├── MEMORY.md         #   长期记忆
    └── USER.md           #   用户画像
```

## 架构设计

### 主循环 (main.py)

```
用户输入 → chat(LLM) → 有 tool_calls? → 执行工具 → 再次 chat
                                    ↓ 无
                              输出回复 → 存储 → 检查压缩
```

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