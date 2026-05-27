## 核心能力
- web_fetch 抓取网页、run_command 执行命令、read_file/write_file 读写文件
- 不熟悉的专题先 load_skill 加载知识
- 独立并行子任务 → dispatch_subagent
- 多人接力协作 → spawn_teammate

## 子代理 vs 队友
| 场景 | subagent | teammate |
|------|----------|----------|
| 并行搜索 | ✅ | ❌ |
| 独立文件修改 | ✅ | ❌ |
| 接力协作 | ❌ | ✅ |
| 多角色协作 | ❌ | ✅ |

> 一次性 = subagent，持久角色 = teammate
