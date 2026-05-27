# TODO

## 功能

- [x] **计划模式** — /plan /act 双模式切换，计划模式下只规划不执行，支持审批配置
- [x] **MCP 协议支持** — 实现 MCP 客户端，支持 stdio/streamable_http 连接 MCP 服务器，工具自动注册到 ToolRegistry
- [ ] **计划任务机制** — 支持定时/周期性任务调度（如定时摘要、定期检查），类似 cron 的 Agent 内置调度器
- [ ] **工具结果缓存** — 同一轮对话中同参数工具调用走 LRU 缓存，减少重复 LLM 往返
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

- [ ] **配置热加载** — 修改 `config.yaml` 无需重启
- [ ] **token 用量面板** — 累计统计每次调用的 prompt/completion tokens 和估算费用
- [ ] **多模态输入** — 支持图片/PDF 作为输入
