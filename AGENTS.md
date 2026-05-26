# mini_ai 项目规范

## 项目概述

基于 OpenAI / Anthropic Chat API 的智能对话 Agent，支持多模型切换、流式输出、工具调用、技能系统、三层记忆压缩、子代理派遣、Team 协作、会话管理、终端 UI 渲染、Web 界面。

## 目录结构

```
pyproject.toml            # 项目配置（uv/pip 安装）
docs/                     # 设计文档
examples/                 # 功能示例（按模块子目录）
src/mini_ai/            # 包源码
  __init__.py             #   包定义
  __main__.py             #   python -m mini_ai 入口
  main.py                 #   主循环编排
  config.py               #   配置加载 + RequestContext + ConfigError
  llm/                    #   LLM 通信层
    base.py                #     共享基础设施（session/usage/config 读取）
    openai.py              #     OpenAI 协议
    anthropic.py           #     Anthropic Claude 适配层
  cli/                    #   CLI 交互层
    display.py             #     终端 UI 渲染（Markdown/思维链/工具调用）
    commands.py            #     斜杠命令处理
  runner.py               #   统一 Agent 执行循环（run_tool_loop + run_agent）
  context.py              #   系统提示词组装
  memory/                 #   记忆系统
    store.py               #     三层记忆存储（MemoryStore）
    compactor.py           #     对话压缩归档（Compactor）
    session.py             #     会话管理
  skills.py               #   技能加载器（多路径搜索）
  logger.py               #   日志模块
  team/                   #   多 Agent 编排子包
    __init__.py            #     统一导出
    bus.py                 #     消息总线（文件 JSONL + Condition 唤醒）
    manager.py             #     队友管理器（spawn/idle 超时/P2P）
    loop.py                #     回禀等待/清理
    blackboard.py          #     共享黑板（KV 存储 + 持久化）
    task_graph.py          #     DAG 调度器（依赖/条件/重试）
    orchestrator.py        #     编排循环（DAG 驱动派遣）
  character/              #   Agent 人设（SOUL.md + RULES.md）
  tools/                  #   工具系统（ToolRegistry 类）
    __init__.py            #     注册、分发、ToolRegistry
    team_tools.py          #     Team 工具（spawn/send/broadcast/dismiss）
    blackboard_tools.py    #     黑板工具（read/write/list）
    workflow_tools.py      #     工作流工具（run_workflow/status/load）
    dispatch_subagent.py   #     子代理调度
    run_command.py / web_fetch.py / read_file.py / write_file.py
    update_todos.py / list_skills.py / load_skill.py / install_skill.py
  subagents/              #   子代理定义（coder.md, researcher.md）
~/.mini_ai/  # 运行时数据目录
  config.yaml             #   用户配置（含 API 密钥 + active_workspace）
  skills/                 #   用户技能
  workflows/              #   工作流 YAML 模板
  logs/                   #   运行日志
  workspaces/             #   工作空间数据（按项目隔离）
    default/              #     默认工作空间
      memory_data/        #       记忆 + 历史 DB + 会话
      .team/              #       Team 协作数据
```

## 架构原则

- **多模型切换**：`config.yaml` 的 `active_model` 一键切换，`api_mode` 适配 OpenAI/Anthropic 协议；`/model <名称>` 运行时动态切换，立即生效并持久化；`llm/openai.py`/`llm/anthropic.py` 动态读取 `MODEL_CONFIG`
- **模块化**：一个文件一个职责，不要把所有逻辑堆在 main.py
- **工具系统**：ToolRegistry 类管理工具注册/分发。新工具 = `tools/xxx.py`（导出 `definition` + `execute(args)` + 可选 `configure(**kwargs)`），通过 Registry 注册。需要外部依赖的工具通过 `configure()` 注入
- **结果截断**：工具输出超过 `max_result_chars` 自动截断，防止上下文膨胀
- **配置分离**：所有运行时参数走 `DATA_DIR/config.yaml`，通过 `config.py` 加载，不硬编码。`PACKAGE_DIR` 存放只读包数据，`DATA_DIR`（默认 `~/.mini_ai/`）存放可写运行时数据。可选字段有默认值防护，配置文件缺失不崩溃。模型配置支持可选 `headers` 字段，发送请求时自动附加自定义请求头
- **技能多路径**：`SkillLoader` 支持主目录 + `skill_paths` 额外搜索路径，同名技能主目录优先；安装技能写入主目录
- **记忆系统**：MemoryStore（三层存储）+ Compactor（按轮次摘要 + 闲聊过滤）+ remember/recall/forget 主动记忆工具 + SQLite 历史检索。压缩触发：API prompt_tokens 或本地预估超阈值
- **工作空间**：CLI 模式 CWD 即工作空间（自动绑定），Web 模式通过面板管理。每个工作空间独立记忆/会话/历史/Team 数据
- **Event 驱动**：lead 用 `threading.Condition` 等待队友回禀/DAG 完成，精确唤醒零延迟
- **多 Agent 编排**：team/ 子包提供 Blackboard（共享状态）+ TaskGraph（DAG 调度）+ Orchestrator（编排循环），支持链式/并行/条件分支/重试
- **线程安全**：`threading.local()` 隔离各线程 token 统计；`copy_context()` 保持并行 contextvars
- **请求上下文隔离**：`RequestContext` 封装每请求独立的 model_config/display/http_session，CLI/Web 统一使用，多用户并发不互相覆盖；`_sessions_lock` 保护并发读写
- **上下文安全阀**：子代理/队友 `prompt_tokens > context_length × 88%` 时自动终止
- **依赖注入**：工具模块通过 `configure(**kwargs)` 注入外部依赖，避免模块级可变赋值
- **终端 UI**：`cli/display.py` 统一管理所有终端输出，main.py 不直接 print；流式先纯文本后重渲 Markdown；思维链/工具调用/命令补全均由 display 层处理；状态栏每轮对话后右对齐显示模型/上下文/token 信息
- **项目规范自动加载**：`context.py` 自动读取当前目录的 `CLAUDE.md` 或 `AGENTS.md`（优先前者），注入系统提示词
- **Web 界面**：`mini-ai --web` 启动 FastAPI + Vue 3 前端，WS/SSE 双模式（WS 支持中断生成）。`RequestContext` 实现多用户并发隔离：每请求独立 model_config/display/http_session，per-session 模型切换不修改全局配置。多会话隔离（`_SESSIONS` 两级字典 username→session_id），多用户认证（用户名 + localStorage），斜杠命令补全（`/api/commands`），技能面板抽屉，会话 JSONL 文件持久化（`~/.mini_ai/web_sessions/<username>/<sid>.jsonl`，重启自动恢复）

## 行为规则

- **不要主动提交代码**：除非用户明确说"提交"或"commit"，否则只报告完成状态
- **代码不加注释**：默认不写注释，只在 WHY 非显而易见时加一行简短注释
- **不设计过度**：不写半成品实现，三个类似行比过早抽象好
