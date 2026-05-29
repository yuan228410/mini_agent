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
  base_tools:
    - run_command
    - web_fetch
    - load_skill

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
| `web_fetch` | `30` | 网页抓取超时（秒） |

### compactor

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `context_usage_threshold` | `0.8` | 压缩触发：prompt_tokens 超过上下文长度的比例 |
| `keep_recent` | `50` | 压缩后保留最近消息数 |
| `keep_budget_ratio` | `0.2` | 压缩后保留轮次占上下文窗口比例 |
| `early_compact_ratio` | `0.85` | 预压缩触发阈值相对 context_usage_threshold 的比例 |
| `max_cached_summaries` | `200` | 增量压缩轮次摘要缓存条数上限，超过时自动清理最旧摘要，防止长时间对话内存泄漏 |
| `context_limit` | `50` | 加载到 LLM 上下文的消息条数 |

### teammate

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_teammates` | `10` | 最大队友数量 |
| `max_turns` | `20` | 队友每轮最大 LLM 调用次数 |
| `idle_timeout` | `300` | 空闲超时自动退出（秒），`0` 表示不超时 |
| `base_tools` | `[run_command, web_fetch, load_skill]` | 队友基础工具白名单 |

### tool

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_result_chars` | `8000` | 工具返回值截断长度 |

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

### logging

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `level` | `WARNING` | 终端日志级别 |
| `file_level` | `DEBUG` | 文件日志级别（写入 `logs/YYYYMMDD.log`） |