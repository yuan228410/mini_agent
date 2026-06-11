# Memory System

## 四层存储模型 + 三层读取合并

从短期到长期逐层提炼，确保 Agent 在长对话中不丢失关键信息：

| 层级 | 存储 | 生命周期 | 说明 |
|------|------|----------|------|
| 对话历史 | `history.db`（SQLite） | 持久 | 全量对话记录，支持 FTS5 全文搜索，按 workspace 隔离。压缩后摘要写入 `compaction_summary.md`，全量数据保留 |
| 情景层 | `YYYY-MM-DD.md` | 压缩时 | 当日情景记忆短文——压缩触发时生成，记录当天对话的关键事实、结论、待办。按日期归档，用于快速回顾当日上下文 |
| 长期层 | `MEMORY.md` | 持久 | 跨对话的长期记忆——核心目标、重要决策、项目背景、关键技术选型等。压缩时由 LLM 增量更新 |
| 用户画像 | `USER.md` | 持久 | 用户偏好、习惯、知识背景。帮助 Agent 更好地理解用户需求风格 |

### 三层读取合并

长期层和用户画像支持 **global → user → workspace** 三层级合并读取。写入时指定层级，读取时自动合并：

```
global/MEMORY.md  (全用户共享)
       ↓ 叠加
user/MEMORY.md    (用户级，优先级高于 global)
       ↓ 叠加
workspace/MEMORY.md (工作空间级，最高优先级)
```

**合并规则**：按 `## 标题` 拆分 section，同名 section 后层覆盖前层（workspace > user > global）。无标题的头部文本仅保留首个非空文件的内容。

**适用文件**：
- `MEMORY.md` — 长期记忆
- `SOUL.md` — 身份定义
- `RULES.md` — 行为规范

**合并示例**：
```
# global/SOUL.md
## 自我介绍
我是全局 AI

## 行为风格
- 喜欢写代码

---
# user/SOUL.md
## 自我介绍
我是用户级 AI  ← 覆盖 global

## 新增特性
- 喜欢问问题  ← 新增

---
# workspace/SOUL.md
## 自我介绍
我是工作空间 AI  ← 覆盖 user

---
# 最终合并结果
## 自我介绍
我是工作空间 AI

## 行为风格
- 喜欢写代码

## 新增特性
- 喜欢问问题
```

## 数据流

```
用户发消息 → 存入 history.db
             ↓
        token 超阈值？
          是 → 触发压缩
               ├─ 旧轮次摘要 → 写入情景记忆（今日 .md）
               ├─ 重要信息提炼 → 增量更新长期记忆（MEMORY.md）
               ├─ 用户特征提取 → 增量更新用户画像（USER.md）
               ├─ 摘要写入 `compaction_summary.md`（全量数据保留在 history.db，仍可搜索）
               └─ 重建 system prompt（含最新记忆 + 项目规范）
          否 → 继续
```

## 压缩触发条件

- **API 返回值**：`prompt_tokens > context_length × context_usage_threshold`（默认 0.8）
- **本地预估**：CJK 字符按 1:1、其他字符按 4:1 估算 token（`estimate_messages_tokens`），超阈值提前预防，应对 API 不返回 usage 的情况
- **API 上下文溢出**：API 返回 HTTP 400 且 body 含 `context_length`/`prompt is too long`/`request too large`/`input is too long` 等关键词时，自动识别为上下文溢出，触发 `force_compact` 渐进恢复

## 压缩策略

按轮次摘要 + 闲聊过滤，确保关键信息不丢失：

1. **保留所有 user 消息** — 用户意图不能丢失
2. **按轮次独立摘要** — 每轮 `user → (assistant + tool 执行过程)` 之间的消息独立摘要
3. **闲聊过滤** — 短消息（≤30 字符）+ 无工具调用 + 匹配寒暄关键词的轮次直接丢弃
4. **最近 N 轮保持完整** — 按 `keep_budget_ratio`（默认 0.2）动态决定保留轮次，保留轮次的字符数上限 = `context_length × context_usage_threshold × keep_budget_ratio`
5. **预压缩机制** — token 使用率超过 `context_usage_threshold × early_compact_ratio`（默认 0.8×0.85=0.68）时提前压缩，避免突发超限
6. **批量摘要** — 待压缩轮次按每批 ≤12000 字符分批，每批独立调用 LLM 摘要，分批结果合入同一轮次映射
5. **压缩后结构**：`system → user1 → summary1 → user2 → summary2 → ... → 最近完整轮次`

## 工具结果裁剪 (ContextPruner)

压缩前自动执行三级策略裁剪旧工具结果，减少 prompt token（纯本地操作，零 LLM 开销）：

| 级别 | 条件 | 处理 |
|------|------|------|
| **保护区** | 最近 N 轮 assistant 消息（`protect_recent=3`） | 完整保留 |
| **软裁剪** | 旧轮次且工具结果 > `max_tool_result_chars` | 保留首尾 `soft_prune_lines` 行 + 省略号 |
| **硬裁剪** | 超过 `hard_prune_after` 轮的旧结果 | 替换为 `[tool result pruned]` |

**软裁剪规则**：
- 行数 > keep_lines×2 → 保留前 N 行 + `... (M lines omitted) ...` + 后 N 行
- 行数不多但字符超长 → 保留前 keep_lines×80 字符 + 省略 + 后 keep_lines×80 字符

## 上下文溢出检测 + force_compact 渐进恢复

当 API 返回 400 错误且 body 包含上下文溢出关键词时，不简单重试，而是触发渐进式恢复：

```
API 400 + "context_length"
  → LLMError(is_context_overflow=True)
  → runner loop 检测 → _recover_from_overflow()
  → compactor.force_compact(llm_chat, messages, ctx)
    → L0: prune(10,2000,5) → 估算 → compact(默认) → 估算
    → L1: prune(5,1000,4)  → 估算 → compact(keep//2) → 估算
    → L2: prune(3,600,3)   → 估算 → compact(keep//4) → 估算
    → L3: prune(0,400,3)   → 估算 → compact(3) → 估算
    → L4: prune(0,200,2)   → 估算 → compact(1) → 估算
  → 成功: messages 已替换, continue 重试 LLM（最多 3 次）
  → 失败: 返回错误给用户
```

**5 级渐进参数**（对标 my_agent）：

| 级别 | hard_prune_after | max_tool_result_chars | soft_prune_lines | keep_recent |
|------|------------------|-----------------------|------------------|-------------|
| L0   | 10               | 2000                  | 5                | 默认        |
| L1   | 5                | 1000                  | 4                | 默认//2     |
| L2   | 3                | 600                   | 3                | 默认//4     |
| L3   | 0                | 400                   | 3                | 3           |
| L4   | 0                | 200                   | 2                | 1           |

每级两步：① 裁剪（零开销）→ 估算 → 若低于安全线(70%) 则返回成功；② compact（调 LLM 摘要）→ 估算 → 若低于安全线则返回成功。compact 调用异常时 catch 并继续下一级，不崩溃。

## 压缩产出

压缩时通过一次 LLM 调用同时更新三层记忆：

```xml
<episode>
本次对话的关键记录（事实、结论、待办），写入今日情景记忆
</episode>

<updated_memory>
增量更新长期记忆，保留旧要点 + 新增/更新内容。无需更新则写"(无需更新)"
</updated_memory>

<updated_user>
增量更新用户画像。无需更新则写"(无需更新)"
</updated_user>
```

## 主动记忆工具

Agent 可随时主动操作长期记忆，不需要等待压缩触发：

| 工具 | 说明 |
|------|------|
| `remember(content, category)` | Agent 主动写入长期记忆，支持分类（user_preference / project_info / decision / discovery / general） |
| `recall(keyword?)` | 检索长期记忆，支持模糊关键词匹配 |
| `forget(keyword)` | 删除包含指定关键词的过期记忆 |

## 会话管理

通过斜杠命令管理命名会话：

| 命令 | 说明 |
|------|------|
| `/save <名称>` | 保存当前对话为命名会话（跳过 system 消息） |
| `/load <名称>` | 加载已保存的会话，恢复上下文（保留当前 system prompt） |
| `/sessions` | 列出所有已保存会话（标记当前） |
| `/compact` | 手动触发对话压缩，摘要写入文件 |
| `/clear` | 清空当前会话的历史消息 |
| `/history` | 查看历史消息列表 |

## 存储位置

记忆数据分布在三个层级，按 global → user → workspace 顺序合并读取：

```
~/.mini_ai/
├── memory/                      # global 层（全用户共享）
│   ├── MEMORY.md                #   长期记忆
│   └── USER.md                  #   用户画像
├── users/<username>/memory/     # user 层（用户级，优先级高于 global）
│   ├── MEMORY.md
│   └── USER.md
└── workspaces/<name>/
    └── memory_data/             # workspace 层（工作空间级，最高优先级）
        ├── history.db           # SQLite 历史（FTS 全文搜索）
        ├── MEMORY.md            # 长期记忆（当前工作空间专属）
        ├── USER.md              # 用户画像（当前工作空间专属）
        ├── YYYY-MM-DD.md        # 情景记忆（按日）
        └── sessions/            # 命名会话

# CLI 模式：workspace 层 = ws_dir/memory_data
# Web 模式：user 层 = users/<username>/memory，workspace 层 = ws_dir/memory_data
```

## 异步写入优化（Web 端）

Web 端多会话并发场景下，默认启用异步写入优化，将高频数据库操作改为队列 + 后台线程批量写入：

### 性能提升

- **同步模式**: 每次写入都需要获取锁 + 事务开启/提交
- **异步模式**: 批量写入，减少锁竞争
- **实测性能**: 写入延迟降低 **74.5%**

### 工作原理

```
多线程并发写入 → Queue (队列) → 单后台线程 → 批量写入 SQLite
                     ↓
              缓存 (读取一致性)
```

### 核心特性

| 特性 | 说明 |
|------|------|
| **批量写入** | 50 条消息或 100ms 时间窗口触发批量写入 |
| **读取一致性** | 写入时缓存消息，读取时自动合并缓存和数据库 |
| **持久化保证** | atexit 注册 + SIGTERM/SIGINT 信号处理，异常退出时自动刷盘 |
| **优雅关闭** | 停止前处理队列中所有剩余任务，不丢失数据 |

### 配置参数

```python
# 可在代码中调整的参数
BATCH_TIME_WINDOW = 0.1      # 批量时间窗口（秒）
BATCH_SIZE_THRESHOLD = 50    # 批量数量阈值
MAX_RETRY_COUNT = 3          # 写入失败重试次数
QUEUE_MAX_SIZE = 10000       # 队列最大容量
```

### 使用方式

```python
# 方式1：通过 HistoryDB 使用（推荐）
from mini_ai.memory.history_db import HistoryDB

db = HistoryDB(db_path, async_write=True)  # 启用异步模式
db.append(workspace, session_id, role, content)  # 自动异步写入
messages = db.load_session(workspace, session_id)  # 自动缓存一致性
db.flush()  # 等待刷盘（可选）
db.close()

# 方式2：通过连接池使用
from mini_ai.memory.history_db import HistoryDBPool

HistoryDBPool.set_async_write_default(True)  # 全局启用异步
db = HistoryDBPool.get("username")
```

### 统计监控

```python
# 查看异步写入统计
stats = db.get_async_stats()
# {
#   "total_writes": 1000,      # 总写入次数
#   "batch_writes": 20,        # 批量写入次数
#   "cache_hits": 50,          # 缓存命中次数
#   "write_errors": 0          # 写入错误次数
# }
```

### 适用场景

**推荐使用异步模式**：
- ✅ Web 端多会话并发
- ✅ 高频消息写入
- ✅ 对响应速度要求高

**推荐使用同步模式**：
- ✅ CLI 单会话模式
- ✅ 批量导入场景
- ✅ 对实时持久化要求极高

### 线程安全保障

1. **单一写入线程** - 所有数据库写入由单一后台线程处理，避免竞态条件
2. **WAL 模式** - SQLite 启用 Write-Ahead Logging，并发读写不阻塞
3. **原子缓存清理** - 批量写入成功后原子清理缓存，保证读取一致性
4. **异常恢复** - 写入失败自动重试（最多 3 次，指数退避）

## Web 端 Per-session 隔离

Web 模式下每个会话独立初始化 MemoryStore + HistoryDB + Compactor 实例：

- **MemoryStore** — 三层记忆（情景层/长期层/用户画像），支持 global→user→workspace 三层级合并读取。Web 端 user 层存在 `users/<username>/memory/`，global 层存在 `~/.mini_ai/memory/`，workspace 层存在工作空间目录下
  - ⚠ **跨实例并发安全**：Web 多会话各自创建独立的 MemoryStore 实例，所有实例共享同一组 `MEMORY.md` / `USER.md` / `YYYY-MM-DD.md` 文件。MemoryStore 使用 **`fcntl.flock` 文件级锁**（`LOCK_SH` 读 / `LOCK_EX` 写）而非实例级 `threading.Lock`，确保跨实例读写安全
  - Windows 回退：不支持 `fcntl` 的平台回退到进程级 `threading.Lock`，单进程多会话仍安全，多进程场景需注意
- **HistoryDB** — SQLite 历史存储，支持全文搜索（`/api/chat/search`）
- **Compactor** — 复用 `config.yaml` 的 `compactor` 配置，上下文超阈值自动压缩。引入增量缓存机制：已摘要轮次复用缓存，`max_cached_summaries`（默认 200）控制缓存上限，超过时自动裁剪最旧轮次的摘要
- **实时持久化** — 通过 `persist_fn` 回调，每条消息（用户/助手/工具调用/工具结果）生成即写入 DB，工具结果完整保存不截断（仅 LLM 上下文截断）
- **会话名称** — 持久化到 `<session_dir>/<sid>/meta.json`，重启后恢复
- **历史加载量** — `web.history_limit`（默认 200）控制前端展示的消息条数，`compactor.context_limit`（默认 50）控制 LLM 上下文加载量，`compactor.keep_recent` 控制压缩后保留的完整消息数。`keep_budget_ratio`（默认 0.2）和 `early_compact_ratio`（默认 0.85）控制压缩保留精度和触发时机，`max_cached_summaries`（默认 200）控制增量压缩缓存上限，三者独立配置
- **项目规范共享** — 同一工作空间下所有会话共享 CLAUDE.md/AGENTS.md