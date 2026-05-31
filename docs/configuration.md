# Configuration Reference

所有配置集中在 `~/.mini_ai/config.yaml`，无需硬编码。首次运行自动创建，从包内拷贝 `config.example.yaml`。

## 完整示例

```yaml
streaming: true
active_model: claude

models:
  claude:
    api_mode: anthropic
    api_url: "https://your-api.com/v1/messages"
    api_key: "your-api-key"
    model: "Claude Opus 4.7"
    context_length: 200000
    temperature: 0.3
  glm:
    api_mode: openai
    api_url: "https://your-api.com/v1/chat/completions"
    api_key: "your-api-key"
    model: "glm-5.1"
    context_length: 200000
    headers:
      X-Custom-Header: value

timeouts:
  llm: 120
  llm_retries: 3
  llm_retry_delay: 2
  teammate_recv: 5
  lead_wait: 1800
  lead_poll_interval: 2
  web_fetch: 30

compactor:
  context_usage_threshold: 0.8
  keep_recent: 50
  keep_budget_ratio: 0.2
  early_compact_ratio: 0.85
  max_cached_summaries: 200
  context_limit: 50

teammate:
  max_teammates: 10
  max_turns: 20
  idle_timeout: 300
  max_history: 20
  task_timeout: 600
  base_tools:
    - run_command
    - web_fetch
    - load_skill
    - read_file
    - write_file
    - edit_file
    - search_files
    - list_dir

tool:
  max_result_chars: 8000

thinking:
  enabled: true
  budget_tokens: 10000
  type: enabled

display:
  thinking_mode: collapsed
  tool_detail: summary

web:
  history_limit: 200

runner:
  context_usage_limit: 0.88

plan:
  approval: true

mcp:
  enabled: true
  connect_timeout: 10
  execute_timeout: 60
  sse_read_timeout: 120
  servers:
    memory:
      type: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-memory"]
    search:
      type: streamable_http
      url: https://mcp.example.com/sse
      headers:
        Authorization: Bearer xxx
      disabled: true

skill_paths:
  # - /opt/shared/skills

logging:
  level: WARNING
  file_level: DEBUG
```

## 配置项说明

### 全局

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `streaming` | `false` | 启用流式输出，文本逐字显示 |
| `active_model` | — | 当前使用的模型，对应 `models` 下的 key |

### models.\<name\>

每个模型独立配置：

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `api_mode` | 否 | `openai` | 协议模式：`openai` 或 `anthropic` |
| `api_url` | 是 | — | API 地址 |
| `api_key` | 是 | — | API 密钥 |
| `model` | 是 | — | 模型名称/ID |
| `context_length` | 否 | `128000` | 模型上下文窗口大小（token 数） |
| `temperature` | 否 | — | 采样温度，越低越确定，越高越随机 |
| `max_tokens` | 否 | — | 单次回复最大 token 数 |
| `top_p` | 否 | — | 核采样概率阈值 |
| `reasoning_effort` | 否 | — | 推理等级（OpenAI o 系列：`low`/`medium`/`high`） |
| `headers` | 否 | — | 自定义请求头，发送请求时自动附加 |
| `thinking` | 否 | — | 模型级 thinking 覆盖（优先级高于全局 thinking） |

### timeouts

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `llm` | `120` | LLM API 请求超时（秒） |
| `llm_retries` | `3` | LLM 请求失败重试次数（429/5xx/超时） |
| `llm_retry_delay` | `2` | 重试间隔（秒，递增延迟：2s/4s/6s） |
| `teammate_recv` | `5` | 队友等待 inbox 超时（秒） |
| `lead_wait` | `1800` | lead 等待队友回禀上限（秒） |
| `lead_poll_interval` | `2` | lead 轮询 inbox 间隔（秒） |
| `web_fetch` | `20` | 网页抓取超时（秒） |

### compactor

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `context_usage_threshold` | `0.8` | 压缩触发：prompt_tokens 超过上下文长度的比例 |
| `keep_recent` | `50` | 压缩后保留最近消息数 |
| `keep_budget_ratio` | `0.2` | 压缩后保留轮次占上下文窗口比例 |
| `early_compact_ratio` | `0.85` | 预压缩触发阈值相对 context_usage_threshold 的比例 |
| `max_cached_summaries` | `200` | 增量压缩轮次摘要缓存条数上限，超过时自动清理最旧摘要，防止长时间对话内存泄漏 |
| `max_summary_sections` | `50` | 压缩摘要文件保留的最大段落数，超过时截断旧段落 |
| `context_limit` | `50` | 加载到 LLM 上下文的消息条数 |

### teammate

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_teammates` | `10` | 最大队友数量 |
| `max_turns` | `20` | 队友每轮最大 LLM 调用次数 |
| `idle_timeout` | `300` | 空闲超时自动退出（秒），`0` 表示不超时 |
| `max_history` | `20` | 任务完成后保留的最近消息数，用于多轮交互时保持上下文 |
| `task_timeout` | `600` | DAG 工作流单任务超时（秒），可在任务节点中用 `timeout` 字段覆盖 |
| `base_tools` | `[run_command, web_fetch, load_skill, read_file, write_file, edit_file, search_files, list_dir]` | 队友基础工具白名单（通信/黑板/dispatch_subagent 工具自动附加） |

### tool

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_result_chars` | `8000` | 工具返回值截断长度 |

### image

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_size` | `10485760` | 最大图片文件大小（10MB） |
| `compress_threshold` | `512000` | 压缩阈值（500KB），超过则自动压缩 |
| `compress_max_dimension` | `800` | 压缩后最大边长（像素） |
| `compress_quality` | `85` | JPEG 压缩质量（1-100） |

**图片自动压缩：**
- 超过 `compress_threshold` 的图片自动压缩
- 压缩后最长边不超过 `compress_max_dimension`
- 压缩为 JPEG 格式，质量为 `compress_quality`
- 支持 PNG、JPEG、GIF、WebP、BMP 格式

### thinking

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 启用思维链（仅支持 extended thinking 的模型生效） |
| `budget_tokens` | `10000` | thinking 预算 token 数 |
| `type` | `enabled` | Anthropic 协议：`enabled`（标准）或 `adaptive`（Bedrock 兼容） |

模型级 `thinking` 配置会覆盖全局设置（`models.<name>.thinking`）。

### display

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `thinking_mode` | `collapsed` | 思考展示：`collapsed` / `expanded` / `hidden` |
| `tool_detail` | `summary` | 工具展示：`summary` / `minimal` / `full` |

### web

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `history_limit` | `200` | Web 端前端展示历史消息条数，`compactor.context_limit` 控制 LLM 上下文加载量 |

### runner

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `context_usage_limit` | `0.88` | 子代理/队友上下文安全阀，超限自动终止 |
| `max_turns` | `20` | 工具循环最大轮数，超限强制退出 |

### plan

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `approval` | `true` | 计划模式下是否需要用户审批后才能执行 |

### mcp

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用 MCP 客户端 |
| `connect_timeout` | `10` | MCP 服务器连接超时（秒） |
| `execute_timeout` | `60` | MCP 工具执行超时（秒） |
| `sse_read_timeout` | `120` | SSE 读取超时（秒） |
| `servers` | `{}` | MCP 服务器配置列表 |

每个 MCP 服务器配置：

| 参数 | 说明 |
|------|------|
| `type` | 传输协议：`stdio`（本地进程）或 `streamable_http`（远程服务） |
| `command` | stdio 模式：启动命令 |
| `args` | stdio 模式：命令参数 |
| `url` | streamable_http 模式：服务 URL |
| `headers` | HTTP 请求头 |
| `disabled` | 可选，设为 `true` 跳过此服务器 |

### skill_paths

额外技能搜索路径（只读），安装的技能始终存入主目录 `~/.mini_ai/skills/`。

### image

图片处理配置，用于 `read_image` 工具和 vision 子代理。

|| 参数 | 默认值 | 说明 |
||------|--------|------|
|| `max_size` | `10485760` | 最大图片文件大小（字节），默认 10MB |
|| `compress_threshold` | `512000` | 压缩阈值（字节），超过则自动压缩，默认 500KB |
|| `compress_max_dimension` | `800` | 压缩后最大边长（像素），默认 800 |
|| `compress_quality` | `85` | JPEG 压缩质量（1-100），默认 85 |

### subagent_models

子代理模型映射，为特定子代理类型指定使用的模型。

```yaml
subagent_models:
  vision: claude          # vision 子代理使用 claude 模型处理图片分析
  # researcher: deepseek  # researcher 子代理使用 deepseek 模型
  # planner: claude       # planner 子代理使用 claude 模型
```

映射的模型名称必须是 `models` 中定义的模型名。

### logging

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `level` | `WARNING` | 终端日志级别 |
| `file_level` | `DEBUG` | 文件日志级别（写入 `logs/YYYYMMDD.log`） |
| `format` | `text` | 日志格式：`text`（人类可读）或 `json`（结构化，便于 ELK 分析） |

**结构化日志示例（format=json）：**

```json
{"timestamp": "2026-05-31T12:00:00Z", "level": "INFO", "logger": "mini_ai", "message": "工具执行完成", "event": "tool_call", "tool_name": "read_file"}
```

**使用方式：**

```python
from mini_ai.logger_structured import setup_logging

# JSON 模式
logger = setup_logging({"format": "json", "level": "INFO"})

# 结构化日志
logger.info("message", extra={"extra_data": {"event": "tool_call", "tool_name": "read_file"}})
```

---

## 配置热加载

CLI/Web 启动时自动开启配置文件监听，修改 `~/.mini_ai/config.yaml` 后自动重新加载，无需重启进程。

**实现机制：**

- `ConfigWatcher`（位于 `src/mini_ai/config.py`）基于文件 mtime 轮询检测变更
- 检测到变更后调用 `init_config()` 重新加载配置
- 配置错误时保留旧配置并输出警告

**手动触发热加载：**

```python
# 通过 config 工具
config(action="reload")

# 或直接调用
from mini_ai.config import init_config
init_config()
```

**注意事项：**

- 修改 `active_model` 后可通过 `/model` 命令或 `config reload` 即时切换
- 修改 `models`、`mcp`、`plan` 等配置后需重启生效（已在工具返回中提示）
