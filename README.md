# mini_ai

> 🎓 一个学习型 AI Agent 项目 — 在探索 Agent 开发过程中逐步构建，用于学习研究与实践。

**作者：笨笨** — 热爱编程与 AI 技术的探索者，喜欢折腾新技术、拆解新事物。对大语言模型之上那片神奇地带——Agentic AI 尤为着迷：工具调用、记忆管理、多模型协作、自主规划……这些让 LLM 从"能说话"变成"能做事"的机制，正是这个项目想要亲手弄明白的东西。项目从零手写，不依赖任何 Agent 框架，边学边造，只为深入理解 Agent 的每一个齿轮是如何咬合运转的。

基于 OpenAI / Anthropic Chat API 的智能对话 Agent，支持工具调用、技能系统、三层记忆压缩、子代理派遣、Team 协作、流式输出、多模型切换、MCP 协议、计划模式、Web 界面（多用户 + 多会话并行 + 工作空间管理 + 在线配置）。

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
# CLI 模式
uv run mini-ai

# Web 模式
uv run mini-ai --web              # 默认 http://localhost:8765
uv run mini-ai --web --port 3000  # 自定义端口

# 或手动激活 .venv
source .venv/bin/activate
mini-ai
mini-ai --web
```

### 依赖

| 依赖 | 用途 |
|------|------|
| requests | HTTP 请求（LLM API 通信） |
| pyyaml | 配置文件解析 |
| rich | 终端 Markdown 渲染、思维链/工具调用展示 |
| prompt-toolkit | 输入框 / 命令补全交互 |
| fastapi | Web 后端框架（API + WebSocket 流式） |
| uvicorn | ASGI 服务器（Web 模式运行） |

## 项目结构

```
mini_ai/
├── pyproject.toml             # 项目配置（uv/pip 安装，入口 mini-ai）
├── config.example.yaml        # 配置模板
├── docs/                      # 设计文档
│   └── multi-agent-orchestration.md  # 多 Agent 编排设计方案
├── examples/                  # 功能示例
│   └── team/                  #   多 Agent 编排示例
├── src/mini_ai/             # 包源码
│   ├── __init__.py            #   包定义
│   ├── __main__.py            #   python -m mini_ai 入口
│   ├── main.py                #   主循环编排
│   ├── config.py              #   配置加载（DATA_DIR / PACKAGE_DIR 分离）
│   ├── llm/                   #   LLM 通信层
│   │   ├── base.py            #     共享基础设施（session/usage/config）
│   │   ├── openai.py          #     OpenAI 协议
│   │   └── anthropic.py       #     Anthropic Claude 适配层
│   ├── cli/                   #   CLI 交互层
│   │   ├── display.py         #     终端 UI 渲染（Markdown/思维链/工具调用）
│   │   └── commands.py        #     斜杠命令处理
│   ├── runner.py              #   统一 Agent 执行循环（run_tool_loop）
│   ├── context.py             #   系统提示词组装
│   ├── workspace.py           #   工作空间管理（创建/切换/添加/删除）
│   ├── memory/                #   记忆系统
│   │   ├── store.py           #     记忆存储（情景层+长期层+画像）
│   │   ├── compactor.py       #     对话压缩归档（Compactor）
│   │   ├── history_db.py      #     历史搜索（SQLite 全文搜索）
│   │   └── session.py         #     会话管理
│   ├── skills.py              #   技能加载器（多路径搜索）
│   ├── logger.py              #   日志模块
│   ├── character/             #   Agent 人设（SOUL.md + RULES.md）
│   ├── team/                  #   多 Agent 编排子包
│   │   ├── __init__.py        #     统一导出
│   │   ├── bus.py             #     消息总线（文件 JSONL 邮箱 + Event 唤醒）
│   │   ├── manager.py         #     队友生命周期管理（spawn/idle 超时/P2P）
│   │   ├── loop.py            #     回禀等待/清理
│   │   ├── blackboard.py      #     共享黑板（Agent 间 KV 存储）
│   │   ├── task_graph.py      #     DAG 调度器（依赖/条件分支/重试）
│   │   └── orchestrator.py    #     编排循环（DAG 驱动派遣）
│   ├── character/             #   Agent 人设
│   │   ├── SOUL.md            #     核心身份、能力、工作流程
│   │   └── RULES.md           #     行为规范
│   ├── tools/                 #   工具系统（ToolRegistry 类）
│   │   ├── __init__.py        #     注册、分发、并行执行、结果截断
│   │   ├── run_command.py     #     Shell 命令执行（+timeout/cwd）
│   │   ├── web_fetch.py       #     网页抓取
│   │   ├── read_file.py       #     文件读取
│   │   ├── write_file.py      #     文件写入（+append 模式）
│   │   ├── edit_file.py       #     部分编辑（search-and-replace）
│   │   ├── search_files.py    #     文件搜索（grep + glob）
│   │   ├── list_dir.py        #     目录列表
│   │   ├── list_skills.py     #     列出可用技能
│   │   ├── load_skill.py      #     加载技能内容
│   │   ├── install_skill.py   #     安装技能
│   │   ├── update_todos.py    #     任务规划与状态跟踪
│   │   ├── dispatch_subagent.py #   子代理调度
│   │   ├── team_tools.py      #     Team 工具（spawn/send/broadcast/dismiss）
│   │   ├── blackboard_tools.py #    黑板工具（read/write/list）
│   │   ├── workflow_tools.py  #     工作流工具（run_workflow/status/load）
│   │   ├── memory_tools.py    #     主动记忆（remember/recall/forget）
│   │   └── history_tools.py   #     历史搜索（search_history）
│   └── subagents/             #   子代理定义
│       ├── __init__.py        #     SubagentLoader
│       ├── coder.md           #     代码工程师
│       └── researcher.md      #     信息检索员
└── ~/.mini_ai/  # 运行时数据目录（自动创建）
    ├── config.yaml            #   用户配置（含 API 密钥）
    ├── skills/                #   用户技能
    ├── workflows/             #   工作流 YAML 模板
    ├── logs/                  #   运行日志（按日期轮转，级别可配置）
    └── workspaces/            #   工作空间数据（按项目隔离）
        └── <name>/            #     每个工作空间独立存储
            ├── workspace.yaml #       元数据（关联项目路径）
            ├── memory_data/   #       记忆 + 历史 DB + 会话
            │   ├── history.db #         SQLite 历史（FTS 搜索）
            │   ├── MEMORY.md  #         长期记忆
            │   ├── USER.md    #         用户画像
            │   └── sessions/  #         命名会话
            └── .team/         #       Team 协作数据
```

## 架构设计

### 主循环 (main.py)

```
用户输入 → /save /load /sessions? → 处理会话命令
         → run_tool_loop(LLM, 过滤后工具) → 有 tool_calls? → 并行/串行执行工具 → 再次 chat
                                          ↓ 无
                                    wait_for_teammates? → Event 等待队友回禀 → 收到 → 注入对话 → 再次 chat
                                    ↓ 无活跃队友
                               输出回复 → 存储 → 检查压缩
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
    temperature: 0.3        # 可选：采样温度，越低越确定，越高越随机
    # max_tokens: 8192      # 可选：单次回复最大 token 数
    # top_p: 0.9            # 可选：核采样概率阈值
  glm:
    api_mode: openai
    api_url: https://...
    model: glm-5.1
    context_length: 200000
    reasoning_effort: high  # 可选：推理等级（OpenAI o 系列：low/medium/high）
```

### 流式输出

配置 `streaming: true` 后，LLM 文本回复逐字输出到终端，工具调用仍走批量模式。Anthropic 和 OpenAI 协议均支持流式。

### 终端 UI (cli/display.py)

基于 Rich + prompt_toolkit 的终端渲染层，统一管理所有用户可见输出：

**Markdown 渲染**：流式输出时逐字打印纯文本，完成后清除并重渲为 Rich Markdown（标题、列表、代码块、粗体等）。

**思维链展示**：支持三种模式（`/thinking <mode>` 切换）：

| 模式　　　　　　　| 终端显示　　　　　　　　　　　　　　　| 查看详情　　　　　　 |
| -------------------| ---------------------------------------| ----------------------|
| collapsed（默认） | `💭 已思考 N 字 (Xs)`　　　　　　　　 | `/thinking` 展开查看 |
| expanded　　　　　| 实时输出思考内容 + `💭 思考完毕 (Xs)` | 直接可见　　　　　　 |
| hidden　　　　　　| 不显示　　　　　　　　　　　　　　　　| `/thinking` 仍可查看 |

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

### 会话管理 (memory/session.py)

在对话中使用命令管理会话：

| 命令 | 说明 |
|------|------|
| `/save <名称>` | 保存当前对话为命名会话 |
| `/load <名称>` | 加载已保存的会话，恢复上下文 |
| `/sessions` | 列出所有已保存的会话 |
| `/compact` | 手动触发对话压缩，归档旧消息 |
| `/workspace` | 列出所有工作空间 |
| `/workspace new <名称> [路径]` | 创建新工作空间 |
| `/workspace add <路径>` | 添加现有文件夹为工作空间 |
| `/workspace remove <名称>` | 移除工作空间（保留数据） |
| `/workspace delete <名称>` | 删除工作空间（含数据） |
| `/clear` | 清空当前会话的历史消息 |
| `/history` | 查看历史消息列表 |
| `/genskill <名称>` | 从当前对话总结生成技能 |
| `/skill` | 列出所有可用技能 |
| `/skill <名称>` | 加载并使用指定技能 |
| `/model` | 列出所有可用模型 |
| `/model <名称>` | 切换模型（立即生效，持久化） |
| `/thinking` | 查看最近一次思考过程 |
| `/thinking <mode>` | 切换思考展示：collapsed / expanded / hidden |
| `/mcp` | 查看 MCP 服务器连接状态和工具列表 |
| `/plan` | 进入计划模式（只规划不执行，Agent 仅输出分析和步骤） |
| `/act` | 切换到执行模式（按计划执行） |

### 工作空间

工作空间按项目隔离记忆、会话、历史数据：

- **CLI 自动绑定**：在哪个目录运行 `mini-ai`，该目录名即为工作空间名（自动创建）
- **Web 手动管理**：通过顶栏 📂 按钮打开工作空间面板
- 每个工作空间有独立的 MEMORY.md、会话列表、历史 DB、Team 数据
- 支持手动操作：`/workspace new`、`/workspace add`（关联现有目录）、`/workspace remove/delete`

```bash
cd ~/projects/my-app
mini-ai                  # 自动使用/创建 "my-app" 工作空间

cd ~/projects/another
mini-ai                  # 自动使用/创建 "another" 工作空间，数据独立
```

数据存储：`~/.mini_ai/workspaces/<name>/`（memory_data/ + sessions/ + .team/）

### 终端 UI

启动时显示 `mini ai` ASCII banner（bold green + bold yellow），输入提示符为 `mini-ai> `。首次启动和每轮对话后显示状态栏。

**命令补全：** 输入 `/` 弹出命令列表，`/skill ` 弹出技能名，`/model ` 弹出模型名，按 Tab 选择。

**状态栏：** 每轮对话结束后右对齐显示 `⚙ 模型 │ 上下文用量% │ ↑输入 ↓输出 token │ 系统提示词大小 │ 消息数`，上下文用量超过 70% 变黄、85% 变红。

**自动加载项目规范：** 如果当前工作目录存在 `CLAUDE.md` 或 `AGENTS.md`，自动读取并加入系统提示词作为项目规范（优先加载 CLAUDE.md）。

**模型切换：** 通过 `/model <名称>` 运行时切换模型，立即生效并持久化到 `config.yaml`。补全列表包含 `/model <模型名>` 选项。

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

### Team 协作系统 (team/)

多 Agent 编排子包，支持持久队友、共享黑板、DAG 工作流、P2P 通信：

| 工具 | 用途 |
|------|------|
| spawn_teammate | 召入/唤醒队友，指定名字、职司、首项任务 |
| list_teammates | 列出所有队友及状态（idle/working/offline） |
| send_message | 给队友或 lead 发 inbox 消息（支持 P2P） |
| read_inbox | 读取并清空自己的 inbox（仅队友内部使用） |
| broadcast | 向所有队友广播消息 |
| dismiss_team | 主动解散所有活跃队友 |
| blackboard_write | 向共享黑板写入数据 |
| blackboard_read | 从共享黑板读取数据 |
| blackboard_list | 列出黑板上的 key |
| run_workflow | 提交 DAG 工作流并执行 |
| workflow_status | 查看工作流执行状态 |
| load_workflow | 加载预定义 YAML 工作流模板 |

**特点：**
- 持久队友：spawn 后持续运行，空闲超时（`idle_timeout`）自动退出，无 auto-shutdown
- P2P 通信：队友可通过 `send_message` 直接互通，`list_teammates` 发现彼此
- 共享黑板：`Blackboard` 提供线程安全的 KV 存储，可选持久化到文件
- DAG 编排：`run_workflow` 定义任务依赖图，Orchestrator 自动调度（并行/串行/条件分支）
- 条件分支：DAG 节点支持 `condition` 表达式，不满足时跳过
- 错误重试：DAG 节点支持 `max_retry`，失败后自动重试
- 工作流模板：`~/.mini_ai/workflows/` 目录存放 YAML 模板，`load_workflow` 加载
- Event 唤醒：`threading.Condition` 精确唤醒，零延迟响应
- 工具白名单：队友可使用 `run_command`、`web_fetch`、`load_skill`、`send_message`、`list_teammates`、`blackboard_read/write/list`
- 上下文安全阀：队友 prompt_tokens 超过 context_length × 88% 时自动终止并回禀
- inbox 容量限制：单个 inbox 上限 100KB，防止无限膨胀

**DAG 工作流示例：**
```json
{
  "tasks": [
    {"id": "search", "agent": "researcher", "prompt": "搜索 RAG 技术"},
    {"id": "design", "agent": "architect", "prompt": "设计架构: {search}", "depends_on": ["search"]},
    {"id": "code", "agent": "coder", "prompt": "实现: {design}", "depends_on": ["design"]}
  ]
}
```

详细使用说明见 [examples/team/](examples/team/)。

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
| dismiss_team | 解散所有活跃队友 |
| blackboard_write | 写入共享黑板 |
| blackboard_read | 读取共享黑板 |
| blackboard_list | 列出黑板 key |
| run_workflow | 提交 DAG 工作流 |
| workflow_status | 查看工作流状态 |
| load_workflow | 加载 YAML 工作流模板 |
| remember | 主动写入长期记忆 |
| recall | 检索长期记忆 |
| forget | 删除过期记忆 |

**关键机制：**
- 结果截断：工具输出超过 `max_result_chars` 时自动截断，防止上下文膨胀
- 并行执行：无依赖的工具调用通过 ThreadPoolExecutor 并行运行
- contextvars 传递：并行线程中通过 `copy_context()` 保持 `team_caller` 身份

添加新工具：创建 `tools/新工具.py`，通过 `ToolRegistry` 注册。

### 日志系统 (logger.py)

- 终端：仅显示 WARNING+ 级别消息和 `print` 输出（回禀通知、Assistant 回复等）
- 文件 `logs/YYYYMMDD.log`：DEBUG 级别全量记录，格式含进程/线程 ID（`[PID/TID]`），记录 LLM 请求响应、工具调用与返回、MSG 通信、队友状态等所有事件
- 工具结果只记录名称和长度，不打印内容，避免日志膨胀

### 记忆系统 (memory/)

四层存储模型，从短期到长期逐层提炼，确保 Agent 在长对话中不丢失关键信息：

| 层级 | 存储 | 生命周期 | 说明 |
|------|------|----------|------|
| 对话历史 | `history.db`（SQLite） | 持久 | 全量对话记录，支持 FTS5 全文搜索，按 workspace 隔离。压缩后旧消息标记 `archived`，数据不删除 |
| 情景层 | `YYYY-MM-DD.md` | 按日 | 每日情景记忆短文——当天对话的关键事实、结论、待办。每天一个文件，用于快速回顾当日上下文 |
| 长期层 | `MEMORY.md` | 持久 | 跨对话的长期记忆——核心目标、重要决策、项目背景、关键技术选型等。压缩时由 LLM 增量更新 |
| 用户画像 | `USER.md` | 持久 | 用户偏好、习惯、知识背景。帮助 Agent 更好地理解用户需求风格 |

**数据流：**

```
用户发消息 → 存入 history.db（未归档）
              ↓
         token 超阈值？
           是 → 触发压缩
                ├─ 旧轮次摘要 → 写入情景记忆（今日 .md）
                ├─ 重要信息提炼 → 增量更新长期记忆（MEMORY.md）
                ├─ 用户特征提取 → 增量更新用户画像（USER.md）
                ├─ 旧消息标记 archived（不删除，仍可搜索）
                └─ 重建 system prompt（含最新记忆 + 项目规范）
           否 → 继续
```

**压缩触发条件：**
- `prompt_tokens > context_length × context_usage_threshold`（API 返回值）
- 或本地 token 预估超阈值（字符数 / 2.5，提前预防）

**压缩策略（按轮次摘要）：**
- 保留所有 user 消息（用户意图不丢失）
- 每轮 assistant+tool 执行过程独立摘要
- **闲聊过滤**：短消息 + 无工具调用 + 匹配寒暄关键词的轮次直接丢弃，不占 token
- 最近 N 轮保持完整（按字符阈值动态决定保留多少轮）
- 压缩后结构：`system → user1 → summary1 → user2 → summary2 → ... → 最近完整轮次`

**压缩产出（三层记忆同时更新）：**
1. `<episode>` — 写入今日情景记忆（`YYYY-MM-DD.md`，追加模式）
2. `<updated_memory>` — 增量更新长期记忆（`MEMORY.md`，保留旧要点 + 新增/更新内容）
3. `<updated_user>` — 增量更新用户画像（`USER.md`，同上）

**记忆在上下文中的体现：**

压缩后重建系统提示词时，长期记忆、今日情景、用户画像会被注入 system prompt，确保 Agent 在后续对话中"记得"之前的对话内容。项目规范（CLAUDE.md/AGENTS.md）也一并注入。

**主动记忆工具：**
- `remember(content, category)` — Agent 随时主动写入长期记忆（不需要等压缩触发）
- `recall(keyword?)` — 检索长期记忆（模糊匹配）
- `forget(keyword)` — 删除过期记忆

### 计划模式

通过 `/plan` 和 `/act` 切换对话模式：

| 模式 | 说明 | 指令 |
|------|------|------|
| 执行模式（默认） | Agent 可自由调用工具、执行任务 | `/act` |
| 计划模式 | Agent 只输出分析和步骤规划，不调用任何工具 | `/plan` |

- 计划模式下 Agent 收到空工具列表，自然只输出文本
- `plan.approval: true`（默认）：计划输出后暂停，用户确认 `/act` 后执行
- `plan.approval: false`：计划输出后自动切换执行模式
- CLI 提示符变化：`mini-ai>` → `mini-ai 📋>`
- Web 端状态栏显示 📋 计划模式 / ⚡ 执行模式

```yaml
plan:
  approval: true    # 计划模式下是否需要用户审批后才能执行
```

### MCP 协议支持

支持连接 MCP（Model Context Protocol）服务器，自动获取远程工具并注册到工具系统。

- 传输协议：stdio（本地进程）+ streamable_http（远程服务）
- 工具自动注册，命名格式 `mcp_<服务器>_<工具名>`，与内置工具统一调度
- `/mcp` 命令查看已连接服务器和工具列表
- 同步/异步桥接：MCP SDK 异步调用通过后台 event loop 桥接到同步 ToolRegistry

```yaml
mcp:
  enabled: true
  connect_timeout: 10
  execute_timeout: 60
  sse_read_timeout: 120
  servers:
    memory:
      type: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-memory"]
    search:
      type: streamable_http
      url: https://mcp.example.com/sse
      headers:
        Authorization: Bearer xxx
      disabled: true  # 可选，跳过此服务器
```

- 支持配置多个 MCP 服务器，每个服务器独立连接，工具名自动加 `mcp_<服务器名>_` 前缀避免冲突
- 启动时连接所有已启用服务器，关闭时断开
- 连接失败跳过该服务器，不影响其他工具和服务器
- 调用超时返回错误信息，不阻塞主循环
- 生成/工具执行中 Ctrl+C 优雅中断，回到输入提示符，不退出程序
- 需要 `mcp` 包：`pip install mcp`

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
CLAUDE.md / AGENTS.md (项目规范，自动读取当前目录)
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
    headers:                    # 可选自定义请求头
      X-Custom-Header: value

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
  idle_timeout: 300        # 空闲超时自动退出（秒），0 表示不超时
  base_tools:              # 队友基础工具白名单
    - run_command
    - web_fetch
    - load_skill

tool:
  max_result_chars: 8000   # 工具返回值截断长度

thinking:
  enabled: true             # 启用思维链（仅支持 extended thinking 的模型生效）
  budget_tokens: 10000     # thinking 预算 token 数
  type: enabled             # Anthropic 协议：enabled（标准）| adaptive（Bedrock 兼容）

display:
  thinking_mode: collapsed  # 思考展示：collapsed / expanded / hidden
  tool_detail: summary      # 工具展示：summary / minimal / full

web:
  history_limit: 200        # Web 端加载历史消息条数（前端展示），compactor.keep_recent 控制上下文构建量

runner:
  context_usage_limit: 0.88  # 子代理/队友上下文安全阀

skill_paths:                  # 额外技能搜索路径（只读，安装仍存主目录）
  #  - /opt/shared/skills
  #  - ~/my-skills
```


## Web 界面

通过 `mini-ai --web` 启动 Web 对话界面（默认 `http://localhost:8765`），支持流式输出、思维链/工具调用展示、亮暗主题切换、多用户隔离、多会话并行、工作空间管理、在线配置面板、模型增删、会话持久化。Editorial 杂志编辑风设计，Markdown + 代码高亮渲染。

```bash
mini-ai --web                    # 启动 Web 模式
mini-ai --web --port 3000        # 自定义端口
```

详细设计、API 接口、前端组件、开发模式等见 [WEB.md](WEB.md)。

## 多 Agent 编排

所有编排功能通过自然语言触发，模型自动选择合适的执行方式：

```bash
# 并行搜索
你: 同时搜索 arxiv 和 GitHub 上关于 RAG 的最新内容

# DAG 工作流（自动按依赖顺序执行）
你: 先研究 WebSocket 技术，再设计聊天室架构，最后写代码实现

# 条件分支
你: 运行测试，如果失败就修复，通过就部署

# 共享黑板
你: 把搜索结果存到黑板，让 coder 读取后编码

# 持久队友（跨多轮对话保持）
你: 召入一个 coder 待命
你: 让 coder 实现 JSON parser    ← 第二轮，coder 还在

# P2P 协作
你: 让 coder 写完后直接发给 reviewer 审查

# 预定义工作流模板
你: 用 research_and_code 模板，topic 是向量数据库
```

| 需求 | 模型自动使用 |
|------|-------------|
| 简单搜索/分析 | subagent（同步一次性） |
| 并行多任务 | spawn_teammate 并行 |
| 有依赖的多步骤 | run_workflow（DAG 编排） |
| 多角色配合 | spawn + P2P 通信 |
| 跨 Agent 传递数据 | blackboard 共享黑板 |
| 失败自动重试 | DAG max_retry |

### 工作流使用说明

**DAG 工作流**让你定义有依赖关系的多步任务，系统自动编排执行：

```
你: 帮我先调研 RAG 技术，然后设计架构，最后写代码
```

模型自动生成并执行依赖图：`[research] → [design] → [code]`

**核心机制：**
- 无依赖的任务**自动并行**
- `{task_id}` 占位符被替换为前置任务结果
- 每个任务完成后结果自动写入共享黑板
- 支持 `condition` 条件分支（不满足则跳过）
- 支持 `max_retry` 失败自动重试

**预定义模板**：将 YAML 放入 `~/.mini_ai/workflows/` 即可复用：

```yaml
# ~/.mini_ai/workflows/research_and_code.yaml
tasks:
  - id: research
    agent: subagent:researcher
    prompt: "搜索 {topic}"
    depends_on: []
  - id: code
    agent: subagent:coder
    prompt: "根据调研实现: {research}"
    depends_on: [research]
```

使用：`你: 用 research_and_code 模板，topic 是向量数据库`

### Team 队友使用说明

**Team 模式**适合需要多轮交互、角色分工的场景：

```
你: 召入 coder 和 reviewer 两个队友
你: 让 coder 实现 JSON parser，完成后发给 reviewer 审查
```

**核心机制：**
- 队友持久存在（空闲超时 300s 自动退出，或用 `dismiss_team` 解散）
- 队友间可 P2P 直接通信（不必经 lead 中转）
- 通过共享黑板传递数据（`blackboard_write` / `blackboard_read`）

**生命周期：**
```
spawn_teammate → working（执行任务）→ idle（等待）→ 收到消息 → working → ...
                                        │
                                        ├── idle 超时 (300s) → 自动退出
                                        └── dismiss_team → 退出
```

- **创建**：Lead 调用 `spawn_teammate` 时。同名队友已存在则直接发新任务
- **运行**：执行完任务后进入 idle，收到新消息再次 working，支持跨多轮对话
- **销毁**：idle 超时 / `dismiss_team` 主动解散 / 收到 shutdown_request

### Workflow vs Team 选型

| 场景 | 推荐 | 原因 |
|------|------|------|
| A 结果传给 B | workflow | 自动传递 |
| A 和 B 来回对话 | team | P2P 多轮通信 |
| 固定流程复用 | workflow YAML | 一次定义多次用 |
| 条件分支/重试 | workflow | 内置支持 |
| 长期驻守的助手 | team | 跨轮保持 |

详细使用说明、YAML 模板格式、condition 语法见 [examples/team/](examples/team/)。

## 自定义 Agent 人设

编辑 `character/` 目录下的文件：
- `SOUL.md` — 定义 Agent 的角色定位和核心能力
- `RULES.md` — 定义回复风格和行为规范
