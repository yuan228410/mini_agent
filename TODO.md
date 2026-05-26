# TODO

## 功能

- [ ] **工具结果缓存** — 同一轮对话中同参数工具调用走 LRU 缓存，减少重复 LLM 往返
- [ ] **危险操作拦截** — `rm -rf` / `git push -f` 等操作在 RULES 中约束提示，或在 `run_command` 中加黑名单检测
- [ ] **会话导出** — `/export` 命令将会话导出为 Markdown

## Web 界面

- [x] **会话文件持久化** — JSONL 文件持久化，重启自动恢复
- [x] **WebSocket 模式** — SSE/WS 双模式，支持中断生成，配置切换
- [x] **多用户并发安全** — RequestContext 隔离：每请求独立 model_config/display/http_session，per-session 模型切换
- [x] **多会话并行** — 左侧侧边栏管理多个会话，可同时并行 LLM 生成
- [x] **工作空间管理** — 按用户隔离，支持创建/切换/删除/移除工作空间
- [x] **Ctrl+C 优雅退出** — SIGINT → SIGTERM 转换，uvicorn timeout_graceful_shutdown
- [ ] **Web 端记忆/压缩/搜索** — 集成 MemoryStore + HistoryDB + Compactor，per-session 隔离
- [ ] **highlight.js 按需加载** — 仅加载常用语言包，减少前端体积
- [ ] **多模态输入** — 支持图片/PDF 作为输入

## 优化

- [ ] **配置热加载** — 修改 `config.yaml` 无需重启
- [ ] **token 用量面板** — 累计统计每次调用的 prompt/completion tokens 和估算费用
- [ ] **多模态输入** — 支持图片/PDF 作为输入
