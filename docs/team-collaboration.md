# Team Collaboration

mini_ai 提供两套独立的 Agent 协作机制：**子代理**（一次性、并行）和 **Team 队友**（持久、角色化），以及 **DAG 工作流**编排。

---

## 子代理系统

派遣独立的一次性子代理执行并行任务，上下文完全隔离。子代理执行完成后自动销毁，不保留状态。

### 内置子代理

| 子代理 | 可用工具 | 用途 |
|--------|----------|------|
| researcher | run_command, web_fetch, load_skill, search_history | 信息搜索与分析 |
| coder | run_command, load_skill, read_file, write_file, edit_file, delete_file, rename_file, search_files, list_dir | 代码编写与修改 |
| reviewer | read_file, search_files, run_command, load_skill | 代码审查，发现潜在问题 |
| tester | read_file, write_file, edit_file, delete_file, rename_file, run_command, search_files, load_skill | 编写和执行测试 |
| planner | read_file, search_files, web_fetch, run_command, load_skill | 需求分析，方案设计 |
| vision | read_file, read_image, web_fetch | 图片分析、OCR、图表识别 |

### 基本用法

```
你: 帮我查一下 FastAPI 的文档

→ 模型调用 dispatch_subagent(type="researcher", task="搜索 FastAPI 官方文档，总结核心特性")
→ researcher 独立运行，返回结果
```

多个子代理**自动并行**执行：

```
你: 同时搜索 arxiv 和 GitHub 上关于 RAG 的最新内容

→ 模型同时调用两个 dispatch_subagent（ThreadPoolExecutor 并行）：
   dispatch_subagent(type="researcher", task="搜索 arxiv 上 RAG 最新论文")
   dispatch_subagent(type="researcher", task="搜索 GitHub 上 RAG 热门项目")
→ 两个 researcher 同时执行，互不干扰
```

### 结果链式传递（inputs）

子代理之间**不会自动传结果**，每次 dispatch 都是独立的。`inputs` 参数让主 Agent 把上一步的结果手动传递给下一步。

**场景：先调研后编码**

```
你一条指令完成：
你: 调研 FastAPI 和 Flask 的区别，然后根据调研结果写个 demo
```

模型内部实际执行两步：

```
第一步 → dispatch_subagent(type="researcher", task="调研 FastAPI 和 Flask 的区别")
  返回结果："FastAPI 支持异步、性能好; Flask 轻量、生态成熟..."

第二步 → dispatch_subagent(
  type="coder",
  task="根据调研结果实现一个 FastAPI demo: {research}",
  inputs={"research": "FastAPI 支持异步、性能好; Flask 轻量、生态成熟..."}
)
  coder 实际收到的 task:
  "根据调研结果实现一个 FastAPI demo: FastAPI 支持异步、性能好; Flask 轻量、生态成熟..."
```

**三步链：搜索 → 方案设计 → 编码**

```
第一步 → researcher 搜索 WebSocket 技术
  返回: "WebSocket 是双向通信协议，基于 TCP..."

第二步 → planner 设计聊天室架构
  dispatch_subagent(
    type="planner",
    task="基于调研设计聊天室架构: {research}",
    inputs={"research": "WebSocket 是双向通信协议，基于 TCP..."}
  )
  返回: "前端用 WS API，后端用 FastAPI WebSocket..."

第三步 → coder 实现代码
  dispatch_subagent(
    type="coder",
    task="按方案实现: {plan}",
    inputs={"plan": "前端用 WS API，后端用 FastAPI WebSocket..."}
  )
```

**并行搜索后汇总**

```
并行 dispatch 两个 researcher（A 搜百度，B 搜 GitHub）：
  A → 返回: "百度云函数文档..."
  B → 返回: "GitHub star 数量..."

再派 planner 汇总：
  dispatch_subagent(
    type="planner",
    task="综合以下两个来源的信息做技术选型:
      百度结果: {baidu}
      GitHub 结果: {github}",
    inputs={
      "baidu": "百度云函数文档...",
      "github": "GitHub star 数量..."
    }
  )
```

### 动态注册子代理

`register_subagent` 工具让你在对话中直接创建新子代理，无需手动写文件。

```
你: 帮我创建一个叫 data-analyzer 的子代理，用来分析数据，工具用 web_fetch 和 load_skill

→ 模型调用 register_subagent(
    name="data-analyzer",
    description="数据分析专家，负责分析和提取数据中的模式",
    prompt="你是一个数据分析专家。收到数据后分析其中的模式、趋势和异常...",
    tools=["web_fetch", "load_skill"],
    max_turns=10
  )
→ 文件写入 subagents/data-analyzer.md
→ dispatch_subagent 工具描述刷新
→ 立即可用
```

也可以用来自定义编码流程：

```
你: 创建一个叫 frontend-coder 的子代理，专门负责写 Vue 组件

→ register_subagent(
    name="frontend-coder",
    description="前端工程师，负责编写 Vue 组件",
    prompt="你是一个 Vue 前端工程师...",
    tools=["run_command", "read_file", "write_file", "edit_file", "search_files"],
    max_turns=15
  )
```

### 特点

- **工具白名单**：子代理只能使用定义中列出的工具
- **轮次限制**：每个子代理有 `max_turns` 上限，防止无限循环
- **并行执行**：多个 `dispatch_subagent` 通过 ThreadPoolExecutor 并行运行
- **上下文隔离**：子代理内部对话历史不回传，只返回最终结果
- **安全阀**：`prompt_tokens` 超过 `context_length × 88%` 时自动终止

### 新增子代理（传统方式）