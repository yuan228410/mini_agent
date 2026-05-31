# Memory System

## 四层存储模型 + 三层读取合并

从短期到长期逐层提炼，确保 Agent 在长对话中不丢失关键信息：

| 层级 | 存储 | 生命周期 | 说明 |
|------|------|----------|------|
| 对话历史 | `history.db`（SQLite） | 持久 | 全量对话记录，支持 FTS5 全文搜索，按 workspace 隔离。压缩后摘要写入 `compaction_summary.md`，全量数据保留 |
| 情景层 | `YYYY-MM-DD.md` | 按日 | 每日情景记忆短文——当天对话的关键事实、结论、待办。每天一个文件，用于快速回顾当日上下文 |
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

合并规则：按 `## 标题` 拆分 section，同名 section 后层覆盖前层（workspace > user > global）。无标题的头部文本仅保留首个非空文件的内容。

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

## 压缩策略

按轮次摘要 + 闲聊过滤，确保关键信息不丢失：

1. **保留所有 user 消息** — 用户意图不能丢失
2. **按轮次独立摘要** — 每轮 `user → (assistant + tool 执行过程)` 之间的消息独立摘要
3. **闲聊过滤** — 短消息（≤30 字符）+ 无工具调用 + 匹配寒暄关键词的轮次直接丢弃
4. **最近 N 轮保持完整** — 按 `keep_budget_ratio`（默认 0.2）动态决定保留轮次，保留轮次的字符数上限 = `context_length × context_usage_threshold × keep_budget_ratio`
5. **预压缩机制** — token 使用率超过 `context_usage_threshold × early_compact_ratio`（默认 0.8×0.85=0.68）时提前压缩，避免突发超限
6. **批量摘要** — 待压缩轮次按每批 ≤12000 字符分批，每批独立调用 LLM 摘要，分批结果合入同一轮次映射
5. **压缩后结构**：`system → user1 → summary1 → user2 → summary2 → ... → 最近完整轮次`

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