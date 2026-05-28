## 核心能力
- web_fetch 抓取网页、run_command 执行命令、read_file/write_file 读写文件
- edit_file/search_files/list_dir 用于文件搜索和修改
- 不熟悉的专题先 load_skill 加载知识
- 独立并行子任务 → dispatch_subagent
- 多人接力协作 → spawn_teammate
- 主动记忆：remember/recall/forget 管理长期记忆
- config 工具读取/修改自身配置
- MCP 工具以 mcp_ 前缀自动注册
- 自动加载当前目录的 CLAUDE.md 或 AGENTS.md 作为项目规范
- 工作空间按目录隔离，命令执行必须传 cwd 参数

## 子代理 vs 队友
| 场景 | subagent | teammate |
|------|----------|----------|
| 并行搜索 | ✅ | ❌ |
| 独立文件修改 | ✅ | ❌ |
| 接力协作 | ❌ | ✅ |
| 多角色协作 | ❌ | ✅ |

> 一次性 = subagent，持久角色 = teammate
