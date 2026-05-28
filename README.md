# mini_ai

> 🎓 一个学习型 AI Agent 项目 — 从零手写，不依赖任何 Agent 框架，用于学习研究与实践。

**作者：笨笨** — 热爱编程与 AI 技术的探索者。对大语言模型之上那片神奇地带——Agentic AI 尤为着迷：工具调用、记忆管理、多模型协作、自主规划……这些让 LLM 从"能说话"变成"能做事"的机制，正是这个项目想要亲手弄明白的东西。项目从零手写，边学边造，只为深入理解 Agent 的每一个齿轮是如何咬合运转的。

## 快速开始

```bash
# 安装
uv venv
uv pip install -e . --python .venv/bin/python

# 运行（CLI 模式）
uv run mini-ai

# 运行（Web 模式，默认 http://localhost:8765）
uv run mini-ai --web
```

首次运行自动在 `~/.mini_ai/` 创建 `config.yaml`，填入 API 密钥即可使用。

> 详细安装和配置指南见 [docs/quick-start.md](docs/quick-start.md)

## 特性一览

| 特性 | 说明 | 文档 |
|------|------|------|
| 🔀 **多模型切换** | OpenAI / Anthropic 双协议，运行时 `/model` 一键切换，立即生效 | [架构设计 → 多模型](docs/architecture.md#多模型支持) |
| ⚙️ **工具系统** | 22+ 内置工具，ToolRegistry 统一注册/分发，支持并行执行和结果截断 | [架构设计 → 工具系统](docs/architecture.md#工具系统) |
| 🧠 **记忆系统** | 四层存储：对话历史 → 情景 → 长期 → 画像，自动压缩归档不丢失 | [记忆系统](docs/memory-system.md) |
| 🤝 **多 Agent 协作** | 子代理（一次性并行）+ 队友（持久角色）+ DAG 工作流编排 | [多 Agent 编排](docs/team-collaboration.md) |
| 🌐 **MCP 协议** | 支持 stdio/streamable_http 连接 MCP 服务器，工具自动注册 | [架构设计 → MCP](docs/architecture.md#mcp-协议支持) |
| 📋 **计划模式** | `/plan` 只规划不执行，`/act` 切回执行模式，支持审批配置 | [架构设计 → 计划模式](docs/architecture.md#计划模式) |
| 🖥️ **双模式** | CLI（Rich + prompt-toolkit）和 Web（FastAPI + Vue 3）同一套后端 | [架构设计 → 终端 UI](docs/architecture.md#终端-ui) / [WEB.md](WEB.md) |
| 📁 **工作空间** | 按项目隔离记忆/会话/历史，CLI 自动绑定 CWD，Web 面板管理 | [架构设计 → 工作空间](docs/architecture.md#工作空间) |
| 🎭 **自定义人设** | 编辑 `character/SOUL.md` 和 `RULES.md` 即可改变 Agent 角色 | [架构设计 → 自定义人设](docs/architecture.md#自定义-agent-人设) |

## 项目结构

```
src/mini_ai/
├── main.py              # 主循环编排
├── config.py            # 配置加载
├── llm/                 # LLM 通信层（OpenAI / Anthropic 双协议）
├── cli/                 # CLI 交互层（终端渲染 + 斜杠命令）
├── runner.py            # 统一 Agent 执行循环
├── context.py           # 系统提示词组装
├── memory/              # 记忆系统（存储 + 压缩 + 历史 DB + 会话管理）
├── tools/               # 工具系统（ToolRegistry 注册/分发）
├── team/                # 多 Agent 编排（队友 + 黑板 + DAG）
├── subagents/           # 子代理定义
├── web/                 # Web 界面（FastAPI + Vue 3）
└── character/           # Agent 人设
```

## 设计原则

- **从零手写** — 不依赖 LangChain / CrewAI 等框架，每个机制透明可读
- **模块化** — 一个文件一个职责，代码结构清晰
- **可扩展** — 新工具=一个文件，新子代理=一个 markdown，新模型=一行配置

**适合人群：** 对 AI Agent 原理感兴趣的开发者，希望了解 Agent 内部工作机制而非仅调用框架的人。

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/architecture.md](docs/architecture.md) | 主循环、工具系统、终端 UI、日志、技能、MCP 等 |
| [docs/memory-system.md](docs/memory-system.md) | 四层存储、压缩策略、会话管理 |
| [docs/team-collaboration.md](docs/team-collaboration.md) | 子代理、队友、DAG 工作流编排 |
| [docs/configuration.md](docs/configuration.md) | 所有配置项详细参考 |
| [docs/quick-start.md](docs/quick-start.md) | 安装、配置、运行指南 |
| [WEB.md](WEB.md) | Web 界面设计、API 接口、前端组件 |