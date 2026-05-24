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
│   ├── dispatch_subagent.py  # 子代理调度
│   └── team_tools.py     #   Team 协作工具（5个）
├── subagents/            # 子代理定义
│   ├── __init__.py       #   SubagentLoader
│   ├── coder.md          #   代码工程师
│   └── researcher.md     #   信息检索员
├── team_bus.py           # 队友消息总线（文件 JSONL 邮箱）
├── team_manager.py       # 队友管理器（spawn、状态、线程循环）
├── team_loop.py         # 队友轮询与回禀处理（从 main.py 抽出）
├── skills/               # 技能文件目录
├── memory_data/          # 运行时记忆数据（不入 git）
└── logs/                 # 运行日志（不入 git）
```

## 架构设计

### 主循环 (main.py)

```
用户输入 → 检查是否需要规划(update_todos)
         → chat(LLM, 无read_inbox) → 有 tool_calls? → 并行/串行执行工具 → 再次 chat
                                     ↓ 无
                               有活跃队友? → wait_for_teammates 轮询等待 → 收到回禀 → 注入对话 → 再次 chat
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

### Team 协作系统 (team_bus.py + team_manager.py + team_loop.py + tools/team_tools.py)

召入持久队友组成 agent team，通过文件 JSONL 邮箱（inbox）收发消息协同工作：

| 工具 | 用途 |
|------|------|
| spawn_teammate | 召入/唤醒队友，指定名字、职司、首项任务 |
| list_teammates | 列出所有队友及状态（idle/working/offline） |
| send_message | 给队友或 lead 发 inbox 消息 |
| read_inbox | 读取并清空自己的 inbox（仅队友内部使用，lead 不暴露此工具） |
| broadcast | 向所有队友广播消息 |

**特点：**
- 持久队友：每个队友有独立 daemon 线程，spawn 后持续运行直到 shutdown
- 文件邮箱：队友间通过 `.team/inbox/{name}.jsonl` 通信，进程重启不丢消息
- 工具白名单：队友只能使用 `run_command`、`web_fetch`、`load_skill`、`send_message`
- 回禀机制：lead 不手动轮询 inbox，`team_loop.py` 的 `wait_for_teammates` 自动等待并注入队友回禀
- contextvars 身份识别：队友线程自动标记身份，工具调用时使用正确的发送者
- 并行 spawn：`spawn_teammate` 标记为可并行，模型可一次召入多个队友

### 日志系统 (logger.py)

- 终端：仅显示 WARNING+ 级别消息和 `print` 输出（回禀通知、Assistant 回复等）
- 文件 `logs/YYYYMMDD.log`：DEBUG 级别全量记录，格式含进程/线程 ID（`[PID/TID]`），记录 LLM 请求响应、工具调用与返回、MSG 通信、队友状态等所有事件

### 记忆系统 (memory.py + compactor.py)

**三层存储模型：**

| 层级 | 存储 | 生命周期 | 说明 |
|------|------|----------|------|
| 原始层 | `history.jsonl` | 持久 | 完整对话日志，按 compact_event 标记归档状态 |
| 情景层 | `YYYY-MM-DD.md` | 按日 | 每日对话的关键信息摘要 |
| 长期层 | `MEMORY.md` | 持久 | 核心目标、重要决策、项目背景 |

**压缩触发条件：**
- `prompt_tokens > context_length × context_usage_threshold`
- 即上下文使用量超过模型上下文窗口的指定比例（默认 80%）

**压缩后保留：**
- 最近 `keep_recent` 条消息（默认 100 条）
- 且保留消息总字符数不超过 `char_threshold`（超出则从头部继续裁剪）

**压缩流程：**
1. 根据上述条件确定归档区间和保留区间
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

每个工具是一个独立模块，导出三个接口：
- `definition` — OpenAI Function Calling 的工具定义
- `execute(args)` — 工具执行函数
- `configure(**kwargs)` — （可选）注入外部依赖，避免模块级可变赋值

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
timeouts:
  llm: 120              # LLM API 请求超时（秒）
  teammate_recv: 5      # 队友等待 inbox 超时（秒）
  lead_wait: 300        # lead 等待队友回禀上限（秒）
  lead_poll_interval: 2 # lead 轮询 inbox 间隔（秒）
  web_fetch: 10         # 网页抓取超时（秒）

compactor:
  context_usage_threshold: 0.8 # 压缩触发：prompt_tokens 超过上下文长度的比例
  keep_recent: 100              # 压缩后保留最近消息数
  char_threshold: 50000         # 压缩后保留消息的最大字符数

teammate:
  max_teammates: 10     # 最大队友数量
  max_turns: 20         # 队友每轮最大 LLM 调用次数
  base_tools:           # 队友基础工具白名单
    - run_command
    - web_fetch
    - load_skill

tool:
  max_result_chars: 3000  # 工具返回值截断

runner:
  context_usage_limit: 0.88  # 子代理/队友上下文安全阀

model:
  api_url: "https://your-api.com/v1/chat/completions"
  api_key: "your-api-key"
  model: "your-model-name"
  context_length: 128000  # 模型上下文窗口大小（token 数）
```

## 自定义 Agent 人设

编辑 `character/` 目录下的文件：
- `SOUL.md` — 定义 Agent 的角色定位和核心能力
- `RULES.md` — 定义回复风格和行为规范