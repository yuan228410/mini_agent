# 工具系统

## ToolRegistry 注册模式

位于 `src/mini_ai/tools/`，每个工具是一个独立模块，导出三个接口：

- `definition` — OpenAI Function Calling 的工具定义（JSON Schema）
- `execute(args)` — 工具执行函数
- `metadata` — 可选，声明并行、缓存、计划模式等调度能力
- `configure(**kwargs)` — 可选兼容接口，用于旧的模块级依赖注入

CLI/Web 每个会话都会创建独立的 `ToolRegistry`。有状态工具（memory/history/skills/team/blackboard/workflow/subagent）优先通过 `_BoundTool` 闭包或 `contextvars` 绑定当前会话的 MemoryStore、HistoryDB、SkillLoader、MessageBus、Blackboard、Display 和 registry，避免不同 Web 会话之间串状态。模块级 `configure(**kwargs)` 仍保留给旧调用路径和兼容工具使用。

### ToolBase 基类

`tools/base.py` 提供统一的工具基类，新工具推荐继承 `ToolBase`：

```python
from .base import ToolBase

class MyTool(ToolBase):
    name = "my_tool"
    description = "做某事"
    parameters = {"type": "object", "properties": {...}, "required": [...]}
    metadata = {"parallel_safe": True, "cacheable": True, "side_effect_free": True}

    @staticmethod
    def execute(args: dict) -> str:
        ...

# 向后兼容
definition = MyTool.definition()
execute = MyTool.execute
```

`ToolBase` 自动生成 `definition()` 格式，并通过 `normalize_tool_definition()` 标准化参数 schema、附加内部 metadata。发送给 OpenAI/Anthropic provider 前，LLM adapter 会剥离内部 metadata，只保留 provider 接受的标准 function definition。

添加新工具只需在 `tools/` 目录下创建新模块，通过 `ToolRegistry.add_tools()` 注册即可。

### ToolMetadata

`tools/metadata.py` 定义工具调度元数据：

| 字段 | 说明 |
|------|------|
| `parallel_safe` | 是否允许和其他并行安全工具一起在线程池执行 |
| `cacheable` | 是否允许在当前 `ToolRegistry` 的局部缓存中缓存结果 |
| `side_effect_free` | 是否无副作用，供策略和审计使用 |
| `allowed_in_plan` | 计划模式下是否可见 |
| `allowed_for_teammate` | 是否允许队友使用 |
| `capabilities` | 能力标签，如 `filesystem.read`、`history.read`、`agent.spawn` |

内置工具的默认 metadata 集中在 `DEFAULT_TOOL_METADATA`。旧的 `_parallel_tools` 仅作为兼容扩展保留，新代码优先声明 metadata。

## 内置工具

### 文件操作

| 工具 | 说明 |
|------|------|
| `read_file(path, start_line?, end_line?)` | 读取文件内容，支持行号范围筛选，适用于大文件分段读取 |
| `write_file(path, content, mode?)` | 写入文件，自动创建父目录，`mode=append` 追加到末尾 |
| `edit_file(path, old_string, new_string)` | search-and-replace 模式编辑，只替换第一个匹配 |
| `search_files(pattern, path?, include?, max_results?)` | 文件内容搜索（grep），支持正则和 glob 过滤 |
| `list_dir(path?, recursive?, max_depth?)` | 目录列表，支持递归展示 |
| `read_image(path)` | 读取本地图片文件并转换为 base64 格式，支持 PNG/JPEG/GIF/WebP，大图自动压缩 |


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
| `config(action, path?, value?)` | 读取/修改 mini-ai 配置。action=list 返回概览和结构，action=read 读取指定路径，action=write 写入并持久化，action=reload 热加载配置（无需重启） |

**热加载配置：**

```python
# 修改配置文件后，无需重启即可生效
config(action="reload")
# 返回：✓ 配置已重新加载
# 当前模型: claude (Claude Opus 4.7)
```

配置热加载通过 `ConfigWatcher` 实现（位于 `src/mini_ai/config.py`），基于文件 mtime 轮询检测变更。CLI/Web 启动时自动开启监听线程。

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
|---|---|
| `dispatch_subagent(type, task, inputs?)` | 派遣子代理执行独立任务。可用类型：coder / researcher / reviewer / tester / planner / **vision**。`inputs` 参数支持按 `{key}` 占位符链式传递前置结果 |
| `register_subagent(name, description, prompt, tools?, max_turns?)` | 对话中动态创建并注册新的子代理类型，立即可用 |

**vision 子代理自动处理图片 URL：**

```python
# 无需手动下载图片
dispatch_subagent(type="vision", task="分析这张图片 https://example.com/image.png")

# 自动完成：
# 1. 检测 task 中的图片 URL
# 2. 下载到临时目录
# 3. 压缩大图（5.7MB → 62KB）
# 4. 转换为 base64
# 5. 派遣 vision 分析
# 6. 清理临时文件
```

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

### 工具结果缓存

工具执行结果在当前 `ToolRegistry` 内自动缓存，减少同一会话中重复调用相同只读工具的开销。缓存不跨 registry/会话共享，避免不同用户、workspace 或 memory/history 实例之间交叉污染。

**特性：**

- **Registry-local 作用域**：每个 `ToolRegistry` 持有独立 `ToolCache`
- **Metadata 控制**：只有 `ToolMetadata.cacheable=True` 的工具允许缓存
- **LRU 淘汰**：默认缓存 100 条，超过时淘汰最旧的
- **TTL 过期**：默认 300 秒后过期
- **安全兜底黑名单**：写文件、执行命令、消息发送、黑板写入、队友/工作流触发等有副作用工具永不缓存
- **大结果跳过**：超过 1MB 的结果不缓存

**使用方式：**

```python
registry = ToolRegistry()
registry.add_tools(read_file, search_files)

# 查看当前会话 registry 的缓存统计
stats = registry._cache.stats()
# {'size': 10, 'hits': 25, 'misses': 15, 'hit_rate': '62.5%'}
```

`mini_ai.tools.cache.get_tool_cache()` 仍存在，但仅作为旧代码/兼容路径的全局缓存入口；主流程工具执行使用 `ToolRegistry._cache`。

**不可缓存工具示例：**

- 文件写入：`write_file`、`edit_file`、`delete_file`、`rename_file`
- 命令执行：`run_command`
- 消息发送：`send_message`、`broadcast`
- 黑板写入：`blackboard_write`
- 记忆/历史有状态读写：`remember`、`forget`、`recall`、`search_history`、`manage_history`
- 技能管理：`install_skill`、`delete_skill`
- 配置写入：`config(action="write")` 不缓存，并清除当前 registry 中 config 相关缓存
- 队友/工作流/子代理触发：`spawn_teammate`、`dismiss_team`、`run_workflow`、`workflow_status`、`dispatch_subagent`、`register_subagent`

**并发安全机制：**

并行执行相同工具时，使用 `get_or_wait()` + `mark_done()` 避免重复执行：

```python
# 首个线程执行，其他线程等待结果
cached_result, hit = cache.get_or_wait(tool_name, args)
if hit:
    return cached_result  # 等待首个线程完成后获取缓存

# 执行工具
result = execute_tool(tool_name, args)

# 写入缓存并通知等待线程
cache.mark_done(tool_name, args, result)
```

**config 工具缓存智能化：**

`config` 工具根据 `action` 动态判断是否缓存：

- `read`/`list`/`reload`：无副作用，可缓存
- `write`：有副作用，不缓存，且清除所有 config 相关缓存

### 结果截断

工具输出超过 `tool.max_result_chars`（默认 8000）时自动截断，防止上下文膨胀。截断标记 `[已截断，原长 N 字符]`。

### 并行执行

工具是否可并行由 `ToolMetadata.parallel_safe` 控制。LLM 一次返回的多个工具调用会先按 metadata 分为并行组和串行组：并行组通过 `ThreadPoolExecutor` 并发运行，串行组逐一执行。并行线程中通过 `copy_context()` 保持 `contextvars` 上下文（如 team caller、session-local 工具绑定）。

默认并行安全的典型工具：

- **子代理/队友**：`dispatch_subagent`、`spawn_teammate`
- **文件读取**：`read_file`、`search_files`、`list_dir`、`read_image`
- **网络抓取**：`web_fetch`
- **技能查询**：`list_skills`、`load_skill`
- **记忆/历史检索**：`recall`、`search_history`

> 写操作工具（`write_file`、`edit_file`、`install_skill` 等）默认保持串行执行，避免同一文件或共享状态上的竞态覆盖。

### 有状态工具隔离

CLI/Web 每个会话创建自己的 `ToolRegistry`，并将当前会话状态绑定到工具执行闭包：

- `register_memory_tools(store)` 绑定当前 MemoryStore
- `register_history_tools(history_db, workspace)` 绑定当前 HistoryDB 和 workspace
- `register_skills(skill_loader)` 绑定当前 SkillLoader
- `register_team(bus, manager)` 绑定当前 MessageBus / TeammateManager
- `register_blackboard(blackboard, ...)` 绑定当前 Blackboard 和 workflow 图状态
- `register_subagents(subagent_loader)` 绑定当前 project_path、display 和 registry

这意味着同名工具在不同 Web 会话中可以指向不同的 memory/history/team/blackboard 实例。旧模块级 `configure(**kwargs)` 仍会调用，保证旧代码可用，但主流程以 registry-local 绑定为准。

### 计划模式工具过滤

计划模式只暴露只读/规划安全工具。`plan/tool_policy.py` 会读取工具定义中的 metadata：

- `allowed_in_plan=True` 的工具可在 `/plan` 期间使用
- 执行模式仍会过滤掉只应内部使用或不适合暴露给 lead 的工具
- provider 请求前会剥离 metadata，避免 OpenAI/Anthropic API 收到内部字段

### 工具白名单

- **Lead**：全部工具（排除 `read_inbox` / `list_teammates`）
- **子代理**：按定义中的 `tools` 字段白名单限制
- **队友**：基础工具 + `send_message` / `list_teammates` / `blackboard_read/write/list`

### 容错

- 工具参数 JSON 解析失败时自动降级为空字典
- 执行异常捕获为 `Error: 类型: 消息` 格式返回
- 连续 3 次工具 Error → `runner` 提前退出，避免 LLM 空循环
- MCP 工具超时返回错误消息，不阻塞主循环