# TODO

## 功能

- [x] **计划模式** — /plan /act 双模式切换，计划模式下只规划不执行，支持审批配置
- [x] **MCP 协议支持** — 实现 MCP 客户端，支持 stdio/streamable_http 连接 MCP 服务器，工具自动注册到 ToolRegistry
- [x] **统一异常体系** — 建立 MiniAIError 异常层次，包含 ToolError、LLMError、ConfigError 等
- [x] **工具结果缓存** — 同一轮对话中同参数工具调用走 LRU 缓存，减少重复执行
- [x] **配置热加载** — ConfigWatcher 监听配置文件变更，自动重载无需重启
- [x] **结构化日志** — 支持 text/json 双模式，便于 ELK 分析
- [x] **工具异常详细反馈** — 所有工具执行异常都返回详细错误信息给 LLM，包括错误类型、参数、堆栈等
- [ ] **计划任务机制** — 支持定时/周期性任务调度（如定时摘要、定期检查），类似 cron 的 Agent 内置调度器
- [ ] **危险操作拦截** — `rm -rf` / `git push -f` 等操作在 RULES 中约束提示，或在 `run_command` 中加黑名单检测
- [ ] **会话导出** — `/export` 命令将会话导出为 Markdown

## Web 界面

- [x] **消息时间戳** — 每条消息含 timestamp，CLI/Web 均显示完整时间（26-05-27 14:30:25）
- [x] **Web 历史加载量独立配置** — `web.history_limit`（默认 200）控制前端展示，`compactor.keep_recent`（默认 50）控制上下文构建
- [x] **HistoryDB SQL 修复** — `load_all` 子查询缺少 id 列导致加载失败，已修复

- [x] **会话文件持久化** — HistoryDB（SQLite）持久化，重启自动恢复
- [x] **WebSocket 模式** — WebSocket 通信，支持中断生成
- [x] **多用户并发安全** — RequestContext 隔离：每请求独立 model_config/display/http_session，per-session 模型切换
- [x] **多会话并行** — 左侧侧边栏管理多个会话，可同时并行 LLM 生成
- [x] **工作空间管理** — 按用户隔离，支持创建/切换/删除/移除工作空间
- [x] **Ctrl+C 优雅退出** — SIGINT → SIGTERM 转换，uvicorn timeout_graceful_shutdown
- [x] **Ctrl+C 中断生成** — 生成/工具执行中 Ctrl+C 优雅中断回到输入提示符，不退出程序
- [x] **历史消息管理工具** — manage_history 工具 + history_cleaner 子代理，支持列出/保留/按关键词删除/彻底删除，分批+并行
- [x] **自认知能力** — config 工具读取/修改配置，AI 可 read_file 查看自身源码和文档
- [x] **模型参数配置** — 支持 temperature/max_tokens/top_p/reasoning_effort
- [x] **Web 端记忆/压缩/搜索** — 集成 MemoryStore + HistoryDB + Compactor，per-session 隔离
- [ ] **highlight.js 按需加载** — 仅加载常用语言包，减少前端体积
- [ ] **多模态输入** — 支持图片/PDF 作为输入

## 优化

- [x] **runner 重构** — 拆分为 state/executor/error_handler/loop 四模块，降低循环复杂度
- [x] **测试框架** — pytest 基础设施，115 个测试用例全覆盖，包括 cache/utils/runner/workspace/web/concurrency
- [x] **Web 端并发安全** — 会话锁、状态检查、淘汰保护、任务清理
- [x] **Web 端稳定性增强** — 消息持久化事务、元数据缓存管理
- [x] **Web 端用户体验** — WebSocket 自动重连、连接状态事件
- [x] **工具异常详细反馈** — 所有工具执行异常都返回详细错误信息给 LLM
- [x] **多会话状态管理** — 会话切换状态验证、会话删除清理、多会话并行隔离
- [ ] **token 用量面板** — 累计统计每次调用的 prompt/completion tokens 和估算费用
- [ ] **多模态输入** — 支持图片/PDF 作为输入

## 架构改进（已完成）

### Phase 1: 基础设施完善 ✅

- **统一异常体系** (`src/mini_ai/exceptions.py`)
  - `MiniAIError` 基类：支持 `recoverable` 标记和 `to_user_message()`
  - `ToolError`：工具执行异常，支持 `tool_name`、`recoverable`
  - `LLMError`：LLM 调用异常，支持 `status_code`、`provider`
  - `ConfigError`：配置加载/校验失败
  - `ResourceNotFoundError`、`PermissionDeniedError`、`ValidationError` 等细粒度异常

- **测试框架** (`tests/`)
  - `conftest.py`：共享 fixtures
  - `test_exceptions.py`：异常体系测试
  - `test_runner.py`：runner 核心路径测试
  - `test_cache.py`：缓存功能测试
  - `test_config.py`：配置热加载测试
  - `test_tools_file.py`：文件工具测试

### Phase 2: 核心性能优化 ✅

- **工具结果缓存** (`src/mini_ai/tools/cache.py`)
  - LRU 淘汰策略（maxsize=100）
  - TTL 过期机制（默认 300s）
  - 黑名单过滤（副作用工具不缓存）
  - 大结果自动跳过（> 1MB）
  - 统计信息（命中率、缓存大小）

- **配置热加载** (`src/mini_ai/config.py`)
  - `ConfigWatcher`：基于轮询的配置文件监听
  - `start_config_watcher()`：启动监听线程
  - `config` 工具支持 `reload` action

### Phase 3: 代码质量提升 ✅

- **runner 重构** (`src/mini_ai/runner/`)
  - `state.py`：循环状态管理（`LoopState`）
  - `executor.py`：LLM 调用和工具执行（`ToolExecutor`）
  - `error_handler.py`：统一错误处理（`ErrorHandler`）
  - `loop.py`：精简版主循环（`run_tool_loop`、`run_agent`）

- **类型标注**
  - `config.py`：引入 `TypedDict` 定义配置结构
  - `runner/`：补充参数/返回类型
  - `tools/cache.py`：完整类型标注

### Phase 4: 可维护性增强 ✅

- **结构化日志** (`src/mini_ai/logger_structured.py`)
  - `StructuredFormatter`：支持 text/json 双模式
  - `ExtraLogger`：支持 `extra_data` 字段
  - `setup_logging()`：统一初始化接口
  - 日志示例：`logger.info("message", extra={"extra_data": {"event": "tool_call", "tool_name": "read_file"}})`

## 测试覆盖

- 异常体系：12 个测试用例
- Runner 循环：7 个测试用例
- 工具缓存：11 个测试用例
- 配置热加载：4 个测试用例
- 文件工具：13 个测试用例

**总计**：48 个测试用例，全部通过 ✅
