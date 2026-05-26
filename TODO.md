# TODO

## 功能

- [ ] **工具结果缓存** — 同一轮对话中同参数工具调用走 LRU 缓存，减少重复 LLM 往返
- [ ] **危险操作拦截** — `rm -rf` / `git push -f` 等操作在 RULES 中约束提示，或在 `run_command` 中加黑名单检测
- [ ] **会话导出** — `/export` 命令将会话导出为 Markdown

## Web 界面

- [x] **会话文件持久化** — JSONL 文件持久化，重启自动恢复
- [ ] **highlight.js 按需加载** — 仅加载常用语言包，减少前端体积
- [x] **WebSocket 模式** — SSE/WS 双模式，支持中断生成，配置切换
- [ ] **多用户并发安全** — register_display 全局覆盖、MODEL_CONFIG 全局状态、requests.Session 共享，多用户同时会话会互相影响
- [ ] **多模态输入** — 支持图片/PDF 作为输入

## 优化

- [ ] **配置热加载** — 修改 `config.yaml` 无需重启
- [ ] **token 用量面板** — 累计统计每次调用的 prompt/completion tokens 和估算费用
- [ ] **多模态输入** — 支持图片/PDF 作为输入
