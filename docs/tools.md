# 工具系统

## ToolRegistry 注册模式

位于 `src/mini_ai/tools/`，每个工具是一个独立模块，导出三个接口：

- `definition` — OpenAI Function Calling 的工具定义（JSON Schema）
- `execute(args)` — 工具执行函数
- `configure(**kwargs)` — 可选，注入外部依赖

添加新工具只需在 `tools/` 目录下创建新模块，通过 `ToolRegistry.add_tools()` 注册即可。

## 内置工具

### 文件操作

| 工具 | 说明 |
|------|------|
| `read_file(path, start_line?, end_line?)` | 读取文件内容，支持行号范围筛选，适用于大文件分段读取 |
| `write_file(path, content, mode?)` | 写入文件，自动创建父目录，`mode=append` 追加到末尾 |
| `edit_file(path, old_string, new_string)` | search-and-replace 模式编辑，只替换第一个匹配 |
| `search_files(pattern, path?, include?, max_results?)` | 文件内容搜索（grep），支持正则和 glob 过滤 |
| `list_dir(path?, recursive?, max_depth?)` | 目录列表，支持递归展示 |

### 命令执行

| 工具 | 说明 |
|------|------|
| `run_command(command, cwd?, timeout?)` | 执行 Shell 命令，返回 stdout/stderr，需传 cwd 参数 |

### 网页抓取

| 工具 | 说明 |
|------|------|
| `web_fetch(url, extract_mode?, max_chars?)` | 抓取网页内容，自动清洗 HTML（跳过 style/script/svg/head 标签树，压缩连续空白） |

### 记忆工具

| 工具 | 说明 |
|------|------|
| `remember(content, category?, level?)` | 主动写入长期记忆，支持分类：user_preference / project_info / decision / discovery / general。`level` 指定写入层级：`global` / `user`（默认）/ `workspace` |
| `recall(keyword?)` | 检索长期记忆，支持模糊关键词匹配（读取合并后结果） |
| `forget(keyword, level?)` | 删除包含指定关键词的过期记忆。`level` 指定操作层级：`global` / `user`（默认）/ `workspace` |
| `search_history(keyword, date_from?, date_to?, limit?)` | 跨会话全文搜索历史对话 |
| `manage_history(action, keep_count?, keyword?, confirmed?)` | 管理历史消息：list / keep_recent / delete_keyword / delete_all（需用户确认后方可删除） |

### 配置工具

| 工具 | 说明 |
|------|------|
| `config(action, path?, value?)` | 读取/修改 mini-ai 配置。action=list 返回概览和结构，action=read 读取指定路径，action=write 写入并持久化 |

### 任务规划

| 工具 | 说明 |
|------|------|
| `update_todos(todos)` | 创建或更新任务待办列表，全量覆盖。三态推进：pending → in_progress → completed，最多 5 个 in_progress 并行 |

### 技能系统

技能按优先级分四层（低→高）：`extra`（只读补充）→ `global`（全局）→ `user`（用户级）→ `workspace`（工作空间级）。同名技能高层覆盖低层，`list_skills` 显示技能层级。

| 工具 | 说明 |
|------|------|
| `list_skills()` | 列出所有可用技能名称、描述和层级 |
| `load_skill(name)` | 加载指定技能的完整内容 |
| `install_skill(name, source?, content?, level?)` | 安装技能到指定层级。source=压缩包 URL/本地路径，或 content=内联 Markdown。level=global/user/workspace（默认 user） |
| `delete_skill(name, level?)` | 删除指定技能。不传 level 则删除当前活跃的副本；传 level 则删除该层级的副本 |

### 子代理

| 工具 | 说明 |
|------|------|
| `dispatch_subagent(type, task, inputs?)` | 派遣子代理执行独立任务。可用类型：coder / researcher / reviewer / tester / planner。`inputs` 参数支持按 `{key}` 占位符链式传递前置结果 |
| `register_subagent(name, description, prompt, tools?, max_turns?)` | 对话中动态创建并注册新的子代理类型，立即可用 |

### 多 Agent 协作

| 工具 | 说明 |
|------|------|
| `spawn_teammate(name, role, prompt)` | 召入/唤醒持久队友。同名队友已存在则发新任务 |
| `list_teammates()` | 列出所有队友及状态（idle / working / offline / shutdown） |
| `send_message(to, content, msg_type?)` | 给队友或 lead 发 inbox 消息，支持 P2P |
| `read_inbox()` | 读取并清空自己的 inbox（仅队友内部使用，lead 不暴露） |
| `broadcast(content)` | 向所有队友广播消息 |
| `dismiss_team()` | 解散所有活跃队友，发送 shutdown_request |

### 共享黑板

| 工具 | 说明 |
|------|------|
| `blackboard_write(key, value)` | 向共享黑板写入数据，其他 Agent 立即可读 |
| `blackboard_read(key)` | 从共享黑板读取指定 key 的值 |
| `blackboard_list(prefix?)` | 列出黑板上的所有 key，支持前缀过滤 |

### DAG 工作流

| 工具 | 说明 |
|------|------|
| `run_workflow(tasks)` | 提交 DAG 工作流并执行。tasks 为任务数组，支持 `depends_on` / `condition` / `max_retry` |
| `workflow_status()` | 查看当前工作流的各节点状态和结果摘要 |
| `load_workflow(name)` | 加载预定义 YAML 工作流模板 |

## 关键机制

### 结果截断

工具输出超过 `tool.max_result_chars`（默认 8000）时自动截断，防止上下文膨胀。截断标记 `[已截断，原长 N 字符]`。

### 并行执行

以下工具标记为可并行执行，通过 `ThreadPoolExecutor` 并发运行：

- **子代理/队友**：`dispatch_subagent`、`spawn_teammate`
- **文件读取**：`read_file`、`search_files`、`list_dir`
- **网络抓取**：`web_fetch`
- **技能查询**：`list_skills`、`load_skill`
- **记忆/历史检索**：`recall`、`search_history`
- **技能删除**：`delete_skill`

LLM 一次返回的多个工具调用会先按类型分组（并行组 / 串行组），再逐组执行：并行组内的工具并发运行，串行组的工具逐一顺序执行。并行线程中通过 `copy_context()` 保持 `contextvars` 上下文（如 `team_caller` 身份）。

> 写操作工具（`write_file`、`edit_file`、`install_skill` 等）保持串行执行，避免同一文件上的竞态覆盖。

### 依赖注入

工具通过 `configure(**kwargs)` 注入外部依赖（如 MemoryStore、HistoryDB），使用 `contextvars.ContextVar` 替代全局变量，Web 模式多会话并发安全。

### 工具白名单

- **Lead**：全部工具（排除 `read_inbox` / `list_teammates`）
- **子代理**：按定义中的 `tools` 字段白名单限制
- **队友**：基础工具 + `send_message` / `list_teammates` / `blackboard_read/write/list`

### 容错

- 工具参数 JSON 解析失败时自动降级为空字典
- 执行异常捕获为 `Error: 类型: 消息` 格式返回
- 连续 3 次工具 Error → `runner` 提前退出，避免 LLM 空循环
- MCP 工具超时返回错误消息，不阻塞主循环