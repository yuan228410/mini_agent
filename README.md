# mini_ai

> 🎓 一个学习型 AI Agent 项目 — 在探索 Agent 开发过程中逐步构建，用于学习研究与实践。

基于 OpenAI / Anthropic Chat API 的智能对话 Agent，支持工具调用、技能系统、记忆压缩、子代理派遣、Team 协作、流式输出、多模型切换。

项目从零手写，不依赖任何 Agent 框架，旨在深入理解 Agent 的核心机制：工具调用、记忆管理、上下文压缩、多模型适配、子代理协作等。代码结构清晰，模块职责单一，适合阅读和学习。

**适合人群：** 对 AI Agent 原理感兴趣的开发者，希望了解 Agent 内部工作机制而非仅调用框架的人。

## 快速开始

### 安装

```bash
# 创建虚拟环境
uv venv

# 安装到 .venv（开发模式，可编辑）
uv pip install -e . --python .venv/bin/python

# 激活虚拟环境后直接运行
source .venv/bin/activate
mini-ai
```

### 配置

首次运行自动在 `~/.mini_ai/` 创建数据目录，并从包内拷贝 `config.example.yaml` 为 `config.yaml`。

```bash
# 编辑配置，填入真实 API 密钥和模型地址
vi ~/.mini_ai/config.yaml

# 也可通过环境变量指定数据目录（默认 ~/.mini_ai/）
export MINI_AI_DATA=/path/to/custom/data
```

### 运行

```bash
# 源码库中开发调试
uv run mini-ai

# 或手动激活 .venv
source .venv/bin/activate
mini-ai
```

### 依赖

| 依赖 | 用途 |
|------|------|
| requests | HTTP 请求（LLM API 通信） |
| pyyaml | 配置文件解析 |
| rich | 终端 Markdown 渲染、思维链/工具调用展示 |
| prompt-toolkit | 输入框 / 命令补全交互 |

## 项目结构

```
mini_ai/
├── pyproject.toml             # 项目配置（uv/pip 安装，入口 mini-ai）
├── config.example.yaml        # 配置模板
├── src/mini_ai/             # 包源码
│   ├── __init__.py            #   包定义
│   ├── __main__.py            #   python -m mini_ai 入口
│   ├── main.py                #   主循环编排
│   ├── config.py              #   配置加载（DATA_DIR / PACKAGE_DIR 分离）
│   ├── llm.py                 #   LLM API 通信（OpenAI 协议）
│   ├── anthropic.py           #   Anthropic Claude 适配层
│   ├── display.py             #   终端 UI 渲染（Markdown/思维链/工具调用）
│   ├── runner.py              #   可复用的 Agent 执行循环
│   ├── context.py             #   系统提示词组装
│   ├── memory.py              #   三层记忆存储
│   ├── compactor.py           #   对话压缩归档
│   ├── session.py             #   会话管理
│   ├── skills.py              #   技能加载器（多路径搜索）
│   ├── commands.py            #   斜杠命令处理
│   ├── logger.py              #   日志模块
│   ├── team_bus.py            #   队友消息总线
│   ├── team_manager.py        #   队友管理器
│   ├── team_loop.py           #   回禀等待/清理
│   ├── character/             #   Agent 人设
│   │   ├── SOUL.md            #     核心身份、能力、工作流程
│   │   └── RULES.md           #     行为规范
│   ├── tools/                 #   工具系统
│   │   ├── __init__.py        #     注册、分发、并行执行、结果截断
│   │   ├── run_command.py     #     Shell 命令执行
│   │   ├── web_fetch.py       #     网页抓取
│   │   ├── read_file.py       #     文件读取
│   │   ├── write_file.py      #     文件写入
│   │   ├── list_skills.py     #     列出可用技能
│   │   ├── load_skill.py      #     加载技能内容
│   │   ├── install_skill.py   #     安装技能
│   │   ├── update_todos.py    #     任务规划与状态跟踪
│   │   ├── dispatch_subagent.py #    子代理调度
│   │   └── team_tools.py      #     Team 协作工具（5个）
│   └── subagents/             #   子代理定义
│       ├── __init__.py        #     SubagentLoader
│       ├── coder.md           #     代码工程师
│       └── researcher.md      #     信息检索员
└── ~/.mini_ai/  # 运行时数据目录（自动创建）
    ├── config.yaml            #   用户配置（含 API 密钥）
    ├── skills/                #   用户技能
    ├── memory_data/           #   记忆数据
    ├── logs/                  #   运行日志
    └── .team/                 #   Team 协作数据
```

## 架构设计

### 主循环 (main.py)

```
用户输入 → /save /load /sessions? → 处理会话命令
         → _run_tool_loop(LLM, 过滤后工具) → 有 tool_calls? → 并行/串行执行工具 → 再次 chat
                                          ↓ 无
                                    wait_for_teammates? → Event 等待队友回禀 → 收到 → 注入对话 → 再次 chat
                                    ↓ 无活跃队友
                               输出回复 → 存储 → 自动 shutdown 队友 → 检查压缩
```

### 多模型支持 (config.yaml)

通过 `active_model` 切换模型，每个模型独立配置 API 地址、密钥、上下文长度和协议模式：

```yaml
active_model: claude        # 切换为 glm 即可用另一个模型
models:
  claude:
    api_mode: anthropic     # 支持 openai / anthropic 两种协议
    api_url: https://...
    api_key: sk-...
    model: Claude Opus 4.7
    context_length: 200000
  glm:
    api_mode: openai
    api_url: https://...
    model: glm-5.1
    context_length: 200000
```

### 流式输出

配置 `streaming: true` 后，LLM 文本回复逐字输出到终端，工具调用仍走批量模式。Anthropic 和 OpenAI 协议均支持流式。

### 终端 UI (display.py)

基于 Rich + prompt_toolkit 的终端渲染层，统一管理所有用户可见输出：

**Markdown 渲染**：流式输出时逐字打印纯文本，完成后清除并重渲为 Rich Markdown（标题、列表、代码块、粗体等）。

**思维链展示**：支持三种模式（`/thinking <mode>` 切换）：

| 模式 | 终端显示 | 查看详情 |
|------|----------|----------|
| collapsed（默认） | `💭 已思考 N 字 (Xs)` | `/thinking` 展开查看 |
| expanded | 实时输出思考内容 + `💭 思考完毕 (Xs)` | 直接可见 |
| hidden | 不显示 | `/thinking` 仍可查看 |

**工具调用展示**：三种粒度（`display.tool_detail` 配置）：

| 粒度 | 显示内容 |
|------|----------|
| summary（默认） | 工具名 + 参数摘要(80字) + 结果预览(200字) + 耗时 |
| minimal | 工具名 + 耗时 |
| full | 工具名 + 完整参数 + 完整结果 + 耗时 |

**命令补全**：输入 `/` 自动弹出命令菜单，方向键选择，Tab 确认。

**多协议思维链**：
- Anthropic 协议：`thinking` content block + `thinking_delta` 流式块
- OpenAI 协议（DeepSeek 等）：`reasoning_content` 字段 + 流式 `delta.reasoning_content`

### 会话管理 (session.py)

在对话中使用命令管理会话：

| 命令 | 说明 |
|------|------|
| `/save <名称>` | 保存当前对话为命名会话 |
| `/load <名称>` | 加载已保存的会话，恢复上下文 |
| `/sessions` | 列出所有已保存的会话 |
| `/compact` | 手动触发对话压缩，归档旧消息 |
| `/clear` | 清空当前会话的历史消息 |
| `/history` | 查看历史消息列表 |
| `/genskill <名称>` | 从当前对话总结生成技能 |
| `/skill` | 列出所有可用技能 |
| `/skill <名称>` | 加载并使用指定技能 |
| `/thinking` | 查看最近一次思考过程 |
| `/thinking <mode>` | 切换思考展示：collapsed / expanded / hidden |

### 终端 UI

启动时显示 `mini ai` ASCII banner（bold green + bold yellow），输入提示符为 `mini-ai>`。

**状态栏：** 每轮对话结束后右对齐显示 `⚙ 模型 │ 上下文用量% │ ↑输入 ↓输出 token │ 系统提示词大小 │ 消息数`，上下文用量超过 70% 变黄、85% 变红。

**自动加载项目规范：** 如果当前工作目录存在 `CLAUDE.md` 或 `AGENTS.md`，自动读取并加入系统提示词作为项目规范（优先加载 CLAUDE.md）。

### 任务规划 (update_todos)

模型收到复杂任务后自动拆解为待办列表：
- `pending` → `in_progress` → `completed` 三态推进
- 同一时间最多一个 in_progress
- 每次全量覆盖更新
- 状态注入系统提示词，压缩不会丢失

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
- 安全阀：prompt_tokens 超过 context_length × 88% 时自动终止

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

召入持久队友组成 agent team，通过文件 JSONL 邮箱 + Event 唤醒协同工作：

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
- Event 唤醒：lead 使用 `threading.Event` 等待回禀， teammate 发消息即唤醒，零延迟响应
- 工具白名单：队友只能使用 `run_command`、`web_fetch`、`load_skill`、`send_message`、`read_inbox`
- 回禀机制：`team_loop.py` 的 `wait_for_teammates` 自动等待并注入队友回禀，lead 无需手动轮询
- 消息过滤：shutdown_response 自动忽略，短消息（<30字符且无关键词）静默丢弃
- 自动 shutdown：每轮对话结束自动 shutdown 所有 idle/working 队友，清理残留 inbox
- inbox 容量限制：单个 inbox 上限 100KB，防止无限膨胀
- contextvars 身份识别：队友线程自动标记身份，工具调用时使用正确的发送者；并行执行时使用 `copy_context()` 保持上下文
- 上下文安全阀：队友 prompt_tokens 超过 context_length × 88% 时自动终止并回禀
- 并行 spawn：`spawn_teammate` 标记为可并行，模型可一次召入多个队友
- 线程安全 token 统计：使用 `threading.local()` 隔离各线程的 token 用量，避免竞态

### 工具系统 (tools/)

每个工具是一个独立模块，导出三个接口：
- `definition` — OpenAI Function Calling 的工具定义
- `execute(args)` — 工具执行函数
- `configure(**kwargs)` — （可选）注入外部依赖，避免模块级可变赋值

**内置工具：**

| 工具 | 说明 |
|------|------|
| run_command | 执行 Shell 命令，返回 stdout/stderr |
| web_fetch | 抓取网页内容，自动清洗 HTML（跳过 style/script/svg/head，压缩空白） |
| read_file | 读取文件内容，支持行号范围筛选 |
| write_file | 写入文件，自动创建父目录 |
| update_todos | 任务规划与状态跟踪 |
| list_skills | 列出可用技能 |
| load_skill | 加载技能内容 |
| install_skill | 安装技能（压缩包 URL/本地路径 或 内联内容） |
| dispatch_subagent | 派遣子代理执行任务 |
| spawn_teammate | 召入持久队友 |
| send_message | 发 inbox 消息给队友/lead |
| broadcast | 向所有队友广播消息 |

**关键机制：**
- 结果截断：工具输出超过 `max_result_chars` 时自动截断，防止上下文膨胀
- 并行执行：无依赖的工具调用通过 ThreadPoolExecutor 并行运行
- contextvars 传递：并行线程中通过 `copy_context()` 保持 `team_caller` 身份

添加新工具：创建 `tools/新工具.py`，在 `tools/__init__.py` 的 `_ALL_TOOLS` 中注册。

### 日志系统 (logger.py)

- 终端：仅显示 WARNING+ 级别消息和 `print` 输出（回禀通知、Assistant 回复等）
- 文件 `logs/YYYYMMDD.log`：DEBUG 级别全量记录，格式含进程/线程 ID（`[PID/TID]`），记录 LLM 请求响应、工具调用与返回、MSG 通信、队友状态等所有事件
- 工具结果只记录名称和长度，不打印内容，避免日志膨胀

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
- 最近 `keep_recent` 条消息（默认 50 条）
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

模型通过 `load_skill` 工具按需加载技能内容，通过 `install_skill` 安装新技能。

**技能搜索路径：** 默认扫描 `~/.mini_ai/skills/`，可在 `config.yaml` 中配置额外搜索路径（只读），安装的技能始终存入主目录。同名技能主目录优先。

**技能生成：** 用户可通过 `/genskill <名称>` 从当前对话总结生成技能；LLM 也会在对话中产生可复用经验时主动建议保存。

## 配置

编辑 `~/.mini_ai/config.yaml`：

```yaml
streaming: true              # 流式输出
active_model: claude         # 当前使用的模型（对应 models 下的 key）

models:
  claude:
    api_mode: anthropic       # 协议模式：openai / anthropic
    api_url: "https://your-api.com/v1/messages"
    api_key: "your-api-key"
    model: "Claude Opus 4.7"
    context_length: 200000    # 模型上下文窗口大小（token 数）
  glm:
    api_mode: openai
    api_url: "https://your-api.com/v1/chat/completions"
    api_key: "your-api-key"
    model: "glm-5.1"
    context_length: 200000

timeouts:
  llm: 120                    # LLM API 请求超时（秒）
  llm_retries: 3              # LLM 请求失败重试次数
  llm_retry_delay: 2          # 重试间隔（秒）
  teammate_recv: 5            # 队友等待 inbox 超时（秒）
  lead_wait: 1800             # lead 等待队友回禀上限（秒）
  lead_poll_interval: 2       # lead 轮询 inbox 间隔（秒）
  web_fetch: 30               # 网页抓取超时（秒）

compactor:
  context_usage_threshold: 0.8  # 压缩触发：prompt_tokens 超过上下文长度的比例
  keep_recent: 50                # 压缩后保留最近消息数
  char_threshold: 20000          # 压缩后保留消息的最大字符数

teammate:
  max_teammates: 10       # 最大队友数量
  max_turns: 20            # 队友每轮最大 LLM 调用次数
  base_tools:              # 队友基础工具白名单
    - run_command
    - web_fetch
    - load_skill

tool:
  max_result_chars: 8000   # 工具返回值截断长度

thinking:
  enabled: true             # 启用思维链（Anthropic 协议自动加 thinking 参数）
  budget_tokens: 10000     # Anthropic thinking 预算 token 数

display:
  thinking_mode: collapsed  # 思考展示：collapsed / expanded / hidden
  tool_detail: summary      # 工具展示：summary / minimal / full

runner:
  context_usage_limit: 0.88  # 子代理/队友上下文安全阀

skill_paths:                  # 额外技能搜索路径（只读，安装仍存主目录）
  #  - /opt/shared/skills
  #  - ~/my-skills
```

## 自定义 Agent 人设

编辑 `character/` 目录下的文件：
- `SOUL.md` — 定义 Agent 的角色定位和核心能力
- `RULES.md` — 定义回复风格和行为规范
