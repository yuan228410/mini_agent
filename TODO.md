# TODO

## 功能

- [ ] **流式输出** — `chat()` 改为 streaming 模式，先吐思考过程再出最终答案。涉及返回值形态改变，需同步改 `main.py` / `runner.py` / `team_manager.py`
- [ ] **工具结果缓存** — 同一轮对话中同参数工具调用走 LRU 缓存，减少重复 LLM 往返
- [ ] **危险操作拦截** — `rm -rf` / `git push -f` 等操作在 RULES 中约束提示，或在 `run_command` 中加黑名单检测
- [ ] **会话导出** — `/export` 命令将会话导出为 Markdown

## 优化

- [ ] **配置热加载** — 修改 `config.yaml` 无需重启
- [ ] **token 用量面板** — 累计统计每次调用的 prompt/completion tokens 和估算费用
- [ ] **多模态输入** — 支持图片/PDF 作为输入
