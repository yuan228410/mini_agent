# mini_ai 项目规范

## 项目概述

基于 OpenAI / Anthropic Chat API 的智能对话 Agent，支持多模型切换、流式输出、工具调用、技能系统、三层记忆压缩、子代理派遣、Team 协作、会话管理、终端 UI 渲染。

## 目录结构

```
pyproject.toml            # 项目配置（uv/pip 安装）
src/mini_ai/            # 包源码
  __init__.py             #   包定义
  __main__.py             #   python -m mini_ai 入口
  main.py                 #   主循环编排
  config.py               #   配置加载（DATA_DIR/PACKAGE_DIR 分离）
  config.example.yaml     #   配置模板（首次运行自动拷贝到 DATA_DIR）
  llm.py                  #   LLM API 通信（OpenAI 协议）
  anthropic.py            #   Anthropic Claude 适配层
  display.py              #   终端 UI 渲染（Markdown/思维链/工具调用）
  runner.py               #   可复用的 Agent 执行循环
  context.py              #   系统提示词组装
  memory.py               #   三层记忆存储（MemoryStore）
  compactor.py            #   对话压缩归档（Compactor）
  session.py              #   会话管理
  skills.py               #   技能加载器
  logger.py               #   日志模块
  team_bus.py             #   队友消息总线
  team_manager.py         #   队友管理器
  team_loop.py            #   回禀等待/清理
  character/              #   Agent 人设（SOUL.md + RULES.md）
  tools/                  #   工具系统（每个工具一个py文件）
  subagents/              #   子代理定义（coder.md, researcher.md）
~/.mini_ai/  # 运行时数据目录
  config.yaml             #   用户配置（含 API 密钥）
  skills/                 #   用户技能
  memory_data/            #   记忆数据
  logs/                   #   运行日志
  .team/                  #   Team 协作数据
```

## 架构原则

- **多模型切换**：`config.yaml` 的 `active_model` 一键切换，`api_mode` 适配 OpenAI/Anthropic 协议
- **模块化**：一个文件一个职责，不要把所有逻辑堆在 main.py
- **工具系统**：新工具 = `tools/xxx.py`（导出 `definition` + `execute(args)` + 可选 `configure(**kwargs)`），在 `tools/__init__.py` 注册。需要外部依赖的工具通过 `configure()` 注入，避免模块级可变赋值
- **结果截断**：工具输出超过 `max_result_chars` 自动截断，防止上下文膨胀
- **配置分离**：所有运行时参数走 `DATA_DIR/config.yaml`，通过 `config.py` 加载，不硬编码。`PACKAGE_DIR` 存放只读包数据，`DATA_DIR`（默认 `~/.mini_ai/`）存放可写运行时数据
- **记忆系统**：MemoryStore（存储）+ Compactor（压缩），触发条件：`prompt_tokens > context_length × context_usage_threshold`
- **Event 驱动**：lead 用 `threading.Event` 等待队友回禀，`bus.send()` 即唤醒，0ms 响应
- **线程安全**：`threading.local()` 隔离各线程 token 统计；`copy_context()` 保持并行 contextvars
- **上下文安全阀**：子代理/队友 `prompt_tokens > context_length × 88%` 时自动终止
- **依赖注入**：工具模块通过 `configure(**kwargs)` 注入外部依赖，避免模块级可变赋值
- **终端 UI**：`display.py` 统一管理所有终端输出，main.py 不直接 print；流式先纯文本后重渲 Markdown；思维链/工具调用/命令补全均由 display 层处理

## 行为规则

- **不要主动提交代码**：除非用户明确说"提交"或"commit"，否则只报告完成状态
- **代码不加注释**：默认不写注释，只在 WHY 非显而易见时加一行简短注释
- **不设计过度**：不写半成品实现，三个类似行比过早抽象好
