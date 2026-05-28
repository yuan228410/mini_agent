# Quick Start

## 安装

```bash
# 创建虚拟环境
uv venv

# 安装到 .venv（开发模式，可编辑）
uv pip install -e . --python .venv/bin/python

# 激活虚拟环境后直接运行
source .venv/bin/activate
mini-ai
```

## 配置

首次运行自动在 `~/.mini_ai/` 创建数据目录，并从包内拷贝 `config.example.yaml` 为 `config.yaml`。

```bash
# 编辑配置，填入真实 API 密钥和模型地址
vi ~/.mini_ai/config.yaml

# 也可通过环境变量指定数据目录（默认 ~/.mini_ai/）
export MINI_AI_DATA=/path/to/custom/data
```

## 运行

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

## 依赖

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
├── examples/                  # 功能示例
├── src/mini_ai/               # 包源码
│   ├── main.py                # 主循环编排
│   ├── config.py              # 配置加载
│   ├── llm/                   # LLM 通信层（OpenAI / Anthropic 双协议）
│   ├── cli/                   # CLI 交互层（终端渲染 + 斜杠命令）
│   ├── runner.py              # 统一 Agent 执行循环
│   ├── context.py             # 系统提示词组装
│   ├── memory/                # 记忆系统（四层存储 + 压缩 + 会话管理）
│   ├── tools/                 # 工具系统（ToolRegistry 注册/分发）
│   ├── team/                  # 多 Agent 编排（队友 + 黑板 + DAG）
│   ├── subagents/             # 子代理定义
│   ├── web/                   # Web 界面（FastAPI + Vue 3）
│   └── character/             # Agent 人设（SOUL.md + RULES.md）
└── ~/.mini_ai/                # 运行时数据目录
    ├── config.yaml            # 用户配置
    ├── skills/                # 用户技能
    ├── workflows/             # 工作流 YAML 模板
    ├── logs/                  # 运行日志（按日期轮转）
    └── workspaces/            # 工作空间数据（按项目隔离）
```

## 下一步

- [架构设计](architecture.md) — 主循环、工具系统、终端 UI 等
- [记忆系统](memory-system.md) — 四层存储、压缩策略、会话管理
- [多 Agent 编排](team-collaboration.md) — 子代理、队友、DAG 工作流
- [配置参考](configuration.md) — 所有配置项详细说明
- [Web 界面](../WEB.md) — Web 模式完整文档