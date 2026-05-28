# Memory System

## 四层存储模型

从短期到长期逐层提炼，确保 Agent 在长对话中不丢失关键信息：

| 层级 | 存储 | 生命周期 | 说明 |
|------|------|----------|------|
| 对话历史 | `history.db`（SQLite） | 持久 | 全量对话记录，支持 FTS5 全文搜索，按 workspace 隔离。压缩后旧消息标记 `archived`，数据不删除 |
| 情景层 | `YYYY-MM-DD.md` | 按日 | 每日情景记忆短文——当天对话的关键事实、结论、待办。每天一个文件，用于快速回顾当日上下文 |
| 长期层 | `MEMORY.md` | 持久 | 跨对话的长期记忆——核心目标、重要决策、项目背景、关键技术选型等。压缩时由 LLM 增量更新 |
| 用户画像 | `USER.md` | 持久 | 用户偏好、习惯、知识背景。帮助 Agent 更好地理解用户需求风格 |

## 数据流

```
用户发消息 → 存入 history.db（未归档）
             ↓
        token 超阈值？
          是 → 触发压缩
               ├─ 旧轮次摘要 → 写入情景记忆（今日 .md）
               ├─ 重要信息提炼 → 增量更新长期记忆（MEMORY.md）
               ├─ 用户特征提取 → 增量更新用户画像（USER.md）
               ├─ 旧消息标记 archived（不删除，仍可搜索）
               └─ 重建 system prompt（含最新记忆 + 项目规范）
          否 → 继续
```

## 压缩触发条件

- **API 返回值**：`prompt_tokens > context_length × context_usage_threshold`（默认 0.8）
- **本地预估**：字符数 / 2.5 超阈值（提前预防，应对 API 不返回 usage 的情况）

## 压缩策略

按轮次摘要 + 闲聊过滤，确保关键信息不丢失：

1. **保留所有 user 消息** — 用户意图不能丢失
2. **按轮次独立摘要** — 每轮 `user → (assistant + tool 执行过程)` 之间的消息独立摘要
3. **闲聊过滤** — 短消息（≤30 字符）+ 无工具调用 + 匹配寒暄关键词的轮次直接丢弃
4. **最近 N 轮保持完整** — 按字符阈值（默认 20000）动态决定保留多少轮
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
| `/compact` | 手动触发对话压缩，归档旧消息 |
| `/clear` | 清空当前会话的历史消息 |
| `/history` | 查看历史消息列表 |

## 存储位置

每个工作空间独立的记忆数据，存储在 `memory_data/` 目录：

```
memory_data/
├── history.db         # SQLite 历史（FTS 全文搜索）
├── MEMORY.md          # 长期记忆
├── USER.md            # 用户画像
├── YYYY-MM-DD.md      # 情景记忆（按日）
└── sessions/          # 命名会话
```

## Web 端 Per-session 隔离

Web 模式下每个会话独立初始化 MemoryStore + HistoryDB + Compactor 实例：

- **MemoryStore** — 三层记忆（情景层/长期层/用户画像），存放在 `<session_dir>/<sid>/memory_data/`
- **HistoryDB** — SQLite 历史存储，支持全文搜索（`/api/chat/search`）
- **Compactor** — 复用 `config.yaml` 的 `compactor` 配置，上下文超阈值自动压缩
- **实时持久化** — 通过 `persist_fn` 回调，每条消息（用户/助手/工具调用/工具结果）生成即写入 DB，工具结果完整保存不截断（仅 LLM 上下文截断）
- **会话名称** — 持久化到 `<session_dir>/<sid>/meta.json`，重启后恢复
- **历史加载量** — `web.history_limit`（默认 200）控制前端展示的消息条数，`compactor.keep_recent`（默认 50）控制上下文构建量，两者独立配置
- **项目规范共享** — 同一工作空间下所有会话共享 CLAUDE.md/AGENTS.md