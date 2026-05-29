<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { getSettings, updateSettings, addModel, removeModel, getMcpStatus, addMcpServer, removeMcpServer, type SettingsResponse, type McpConnectedServer, type McpConfiguredServer } from '../api'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits(['close'])

const loading = ref(true)
const saving = ref(false)
const settings = reactive<Partial<SettingsResponse>>({})

const activeModel = ref('')
const selectedModelName = ref('')
const mcpConfigured = ref<McpConfiguredServer[]>([])
const mcpConnected = ref<McpConnectedServer[]>([])
const showAddMcp = ref(false)
const newMcp = reactive({
  name: '',
  type: 'stdio' as 'stdio' | 'streamable_http' | 'sse',
  command: '',
  args: '',
  url: '',
})

const modelFields = reactive({
  temperature: null as number | null,
  max_tokens: null as number | null,
  top_p: null as number | null,
  context_length: 128000,
  reasoning_effort: '' as string,
  thinking_enabled: false,
  thinking_budget: 10000,
  thinking_type: 'enabled',
})

const globalFields = reactive({
  streaming: true,
  thinking_enabled: false,
  thinking_budget: 10000,
  thinking_type: 'enabled',
  thinking_mode: 'collapsed',
  tool_detail: 'summary',
  max_turns: 20,
  context_usage_limit: 0.88,
  plan_approval: true,
  history_limit: 200,
  context_limit: 50,
  keep_recent: 50,
  keep_budget_ratio: 0.2,
  early_compact_ratio: 0.85,
  max_cached_summaries: 200,
  max_result_chars: 8000,
  log_level: 'WARNING',
  mcp_enabled: false,
})

const showAddModel = ref(false)
const newModel = reactive({
  name: '',
  api_key: '',
  api_url: '',
  api_mode: 'openai' as 'openai' | 'anthropic',
  model: '',
  context_length: 128000,
  temperature: 0.3,
})

function resetNewModel() {
  newModel.name = ''
  newModel.api_key = ''
  newModel.api_url = ''
  newModel.api_mode = 'openai'
  newModel.model = ''
  newModel.context_length = 128000
  newModel.temperature = 0.3
}

async function onAddModel() {
  if (!newModel.name.trim() || !newModel.api_key.trim() || !newModel.api_url.trim() || !newModel.model.trim()) return
  try {
    const resp = await addModel(newModel)
    if (resp.error) { alert(resp.error); return }
    showAddModel.value = false
    resetNewModel()
    await loadSettings()
  } catch {}
}

async function onRemoveModel(name: string) {
  if (!confirm(`确定删除模型 "${name}"？`)) return
  try {
    const resp = await removeModel(name)
    if (resp.error) { alert(resp.error); return }
    await loadSettings()
  } catch {}
}

onMounted(async () => {
  await loadSettings()
})

async function loadSettings() {
  loading.value = true
  try {
    const s = await getSettings()
    Object.assign(settings, s)
    activeModel.value = s.active_model
    selectedModelName.value = s.active_model
    applyModelFields(s.active_model)

    const t = s.thinking || {}
    globalFields.thinking_enabled = t.enabled ?? false
    globalFields.thinking_budget = t.budget_tokens ?? 10000
    globalFields.thinking_type = t.type ?? 'enabled'

    const d = s.display || {}
    globalFields.thinking_mode = d.thinking_mode || 'collapsed'
    globalFields.tool_detail = d.tool_detail || 'summary'

    const r = s.runner || {}
    globalFields.max_turns = r.max_turns ?? 20
    globalFields.context_usage_limit = r.context_usage_limit ?? 0.88

    const p = s.plan || {}
    globalFields.plan_approval = p.approval ?? true

    const w = s.web || {}
    globalFields.history_limit = w.history_limit ?? 200
    const cp = s.compactor || {}
    globalFields.context_limit = cp.context_limit ?? 50
    globalFields.keep_recent = cp.keep_recent ?? 50
    globalFields.keep_budget_ratio = cp.keep_budget_ratio ?? 0.2
    globalFields.early_compact_ratio = cp.early_compact_ratio ?? 0.85
    globalFields.max_cached_summaries = cp.max_cached_summaries ?? 200

    const tool = s.tool || {}
    globalFields.max_result_chars = tool.max_result_chars ?? 8000

    const l = s.logging || {}
    globalFields.log_level = l.level || 'WARNING'

    const mcp = s.mcp || {}
    globalFields.mcp_enabled = mcp.enabled ?? false

    globalFields.streaming = s.streaming ?? true
  } catch {}
  loading.value = false
  try {
    const mcp = await getMcpStatus()
    mcpConfigured.value = mcp.configured || []
    mcpConnected.value = mcp.connected || []
  } catch {}
}

function applyModelFields(name: string) {
  const m = settings.models?.[name]
  if (!m) return
  modelFields.temperature = m.temperature ?? null
  modelFields.max_tokens = m.max_tokens ?? null
  modelFields.top_p = m.top_p ?? null
  modelFields.context_length = m.context_length ?? 128000
  modelFields.reasoning_effort = m.reasoning_effort ?? ''
  const mt = m.thinking || {}
  modelFields.thinking_enabled = mt.enabled ?? false
  modelFields.thinking_budget = mt.budget_tokens ?? 10000
  modelFields.thinking_type = mt.type ?? 'enabled'
}

function onModelSwitch(name: string) {
  selectedModelName.value = name
  applyModelFields(name)
}

async function onAddMcpServer() {
  if (!newMcp.name.trim()) return
  try {
    const body: any = { name: newMcp.name, type: newMcp.type }
    if (newMcp.type === 'stdio') {
      body.command = newMcp.command
      if (newMcp.args.trim()) body.args = newMcp.args.split(/\s+/)
    } else {
      body.url = newMcp.url
    }
    const resp = await addMcpServer(body)
    if (resp.error) { alert(resp.error); return }
    showAddMcp.value = false
    newMcp.name = ''; newMcp.command = ''; newMcp.url = ''; newMcp.args = ''
    await loadSettings()
  } catch {}
}

async function onRemoveMcpServer(name: string) {
  if (!confirm(`确定删除 MCP 服务器 "${name}"？`)) return
  try {
    const resp = await removeMcpServer(name)
    if (resp.error) { alert(resp.error); return }
    await loadSettings()
  } catch {}
}

async function save() {
  saving.value = true
  try {
    const updates: Record<string, any> = {}

    if (selectedModelName.value !== activeModel.value) {
      updates.active_model = selectedModelName.value
    }

    updates.model_config = {
      name: selectedModelName.value,
      temperature: modelFields.temperature,
      max_tokens: modelFields.max_tokens,
      top_p: modelFields.top_p,
      context_length: modelFields.context_length,
      reasoning_effort: modelFields.reasoning_effort || null,
      thinking: modelFields.thinking_enabled ? {
        enabled: true,
        budget_tokens: modelFields.thinking_budget,
        type: modelFields.thinking_type,
      } : null,
    }

    updates.streaming = globalFields.streaming
    updates.thinking = {
      enabled: globalFields.thinking_enabled,
      budget_tokens: globalFields.thinking_budget,
      type: globalFields.thinking_type,
    }
    updates.display = {
      thinking_mode: globalFields.thinking_mode,
      tool_detail: globalFields.tool_detail,
    }
    updates.runner = {
      max_turns: globalFields.max_turns,
      context_usage_limit: globalFields.context_usage_limit,
    }
    updates.plan = { approval: globalFields.plan_approval }
    updates.web = { history_limit: globalFields.history_limit }
    updates.compactor = {
      context_limit: globalFields.context_limit,
      keep_recent: globalFields.keep_recent,
      keep_budget_ratio: globalFields.keep_budget_ratio,
      early_compact_ratio: globalFields.early_compact_ratio,
      max_cached_summaries: globalFields.max_cached_summaries,
    }
    updates.tool = { max_result_chars: globalFields.max_result_chars }
    updates.logging = { level: globalFields.log_level }
    updates.mcp = { enabled: globalFields.mcp_enabled }

    await updateSettings(updates)
    await loadSettings()
  } catch {}
  saving.value = false
}

const modelNames = computed(() => Object.keys(settings.models || {}))
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="settings-overlay" @click="emit('close')">
      <div class="settings-panel" @click.stop>
        <div class="settings-header">
          <h3 class="settings-title">⚙ 设置</h3>
          <button class="settings-close" @click="emit('close')">✕</button>
        </div>

        <div v-if="loading" class="settings-loading">加载中…</div>

        <div v-else class="settings-body">
          <!-- Model Section -->
          <div class="settings-section">
            <div class="section-title">当前模型参数</div>
            <div class="field">
              <label>选择模型</label>
              <select :value="selectedModelName" @change="onModelSwitch(($event.target as HTMLSelectElement).value)">
                <option v-for="name in modelNames" :key="name" :value="name">{{ name }} ({{ settings.models?.[name]?.model }})</option>
              </select>
            </div>

            <div class="field">
              <label>Temperature</label>
              <input type="number" v-model.number="modelFields.temperature" step="0.1" min="0" max="2" placeholder="API 默认" />
            </div>
            <div class="field">
              <label>Max Tokens</label>
              <input type="number" v-model.number="modelFields.max_tokens" step="256" min="1" placeholder="API 默认" />
            </div>
            <div class="field">
              <label>Top P</label>
              <input type="number" v-model.number="modelFields.top_p" step="0.05" min="0" max="1" placeholder="API 默认" />
            </div>
            <div class="field">
              <label>Context Length</label>
              <input type="number" v-model.number="modelFields.context_length" step="1000" min="1000" />
            </div>
            <div class="field">
              <label>Reasoning Effort</label>
              <select v-model="modelFields.reasoning_effort">
                <option value="">API 默认</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>

            <div class="field-row">
              <input type="checkbox" v-model="modelFields.thinking_enabled" id="model-thinking" />
              <label for="model-thinking">模型级思考（覆盖全局）</label>
            </div>
            <template v-if="modelFields.thinking_enabled">
              <div class="field">
                <label>Budget Tokens</label>
                <input type="number" v-model.number="modelFields.thinking_budget" step="1000" min="1000" />
              </div>
              <div class="field">
                <label>思考类型</label>
                <select v-model="modelFields.thinking_type">
                  <option value="enabled">enabled（标准）</option>
                  <option value="adaptive">adaptive（Bedrock）</option>
                </select>
              </div>
            </template>
          </div>

          <!-- Model Management -->
          <div class="settings-section">
            <div class="section-title">模型管理</div>

            <div class="model-list">
              <div v-for="(m, name) in settings.models" :key="name" class="model-list-item" :class="{ 'model-list-item-active': name === activeModel }">
                <div class="model-list-info">
                  <span class="model-list-name">{{ name }}</span>
                  <span class="model-list-badge" :class="m.api_mode">{{ m.api_mode === 'openai' ? 'OpenAI' : 'Anthropic' }}</span>
                  <span class="model-list-id">{{ m.model }}</span>
                </div>
                <div class="model-list-actions">
                  <span v-if="name === activeModel" class="model-list-active">●</span>
                  <button v-if="Object.keys(settings.models || {}).length > 1" class="btn-remove-model" @click="onRemoveModel(name)" title="删除">✕</button>
                </div>
              </div>
            </div>

            <button class="btn-add-model" @click="showAddModel = true">
              + 添加新模型
            </button>
          </div>

                    <!-- Global Thinking -->
          <div class="settings-section">
            <div class="section-title">全局思考</div>
            <div class="field-row">
              <input type="checkbox" v-model="globalFields.thinking_enabled" id="global-thinking" />
              <label for="global-thinking">启用思考</label>
            </div>
            <template v-if="globalFields.thinking_enabled">
              <div class="field">
                <label>Budget Tokens</label>
                <input type="number" v-model.number="globalFields.thinking_budget" step="1000" min="1000" />
              </div>
              <div class="field">
                <label>思考类型</label>
                <select v-model="globalFields.thinking_type">
                  <option value="enabled">enabled（标准）</option>
                  <option value="adaptive">adaptive（Bedrock）</option>
                </select>
              </div>
            </template>
          </div>

          <!-- Display -->
          <div class="settings-section">
            <div class="section-title">显示</div>
            <div class="field">
              <label>思考链展示</label>
              <select v-model="globalFields.thinking_mode">
                <option value="collapsed">折叠（摘要）</option>
                <option value="expanded">展开（实时）</option>
                <option value="hidden">隐藏</option>
              </select>
            </div>
            <div class="field">
              <label>工具调用详情</label>
              <select v-model="globalFields.tool_detail">
                <option value="summary">摘要</option>
                <option value="full">完整</option>
                <option value="minimal">最小</option>
              </select>
            </div>
          </div>

          <!-- Runner -->
          <div class="settings-section">
            <div class="section-title">运行</div>
            <div class="field">
              <label>最大工具轮次</label>
              <input type="number" v-model.number="globalFields.max_turns" min="1" max="100" />
            </div>
            <div class="field">
              <label>上下文使用上限</label>
              <input type="number" v-model.number="globalFields.context_usage_limit" step="0.01" min="0.5" max="0.99" />
            </div>
            <div class="field-row">
              <input type="checkbox" v-model="globalFields.streaming" id="streaming" />
              <label for="streaming">流式输出</label>
            </div>
            <div class="field-row">
              <input type="checkbox" v-model="globalFields.plan_approval" id="plan-approval" />
              <label for="plan-approval">计划模式需审批</label>
            </div>
          </div>

          <!-- MCP -->
          <div class="settings-section">
            <div class="section-title">MCP 服务器</div>
            <div class="field-row">
              <input type="checkbox" v-model="globalFields.mcp_enabled" id="mcp-enabled" />
              <label for="mcp-enabled">启用 MCP</label>
            </div>

            <div v-if="mcpConfigured.length > 0" class="mcp-server-list">
              <div v-for="srv in mcpConfigured" :key="srv.name" class="mcp-srv-item">
                <div class="mcp-srv-info">
                  <span class="mcp-srv-name">{{ srv.name }}</span>
                  <span class="mcp-srv-badge" :class="srv.type">{{ srv.type }}</span>
                  <span v-if="srv.disabled" class="mcp-srv-status disabled">已禁用</span>
                  <span v-else-if="mcpConnected.find(c => c.name === srv.name)" class="mcp-srv-status connected">已连接</span>
                  <span v-else class="mcp-srv-status disconnected">未连接</span>
                </div>
                <button class="btn-remove-model" @click="onRemoveMcpServer(srv.name)" title="删除">✕</button>
              </div>
            </div>
            <div v-else-if="globalFields.mcp_enabled" class="mcp-note">未配置 MCP 服务器，点击下方添加</div>

            <button class="btn-add-model" @click="showAddMcp = !showAddMcp">
              {{ showAddMcp ? '✕ 取消' : '+ 添加 MCP 服务器' }}
            </button>

            <div v-if="showAddMcp" class="add-model-form">
              <div class="field">
                <label>名称</label>
                <input v-model="newMcp.name" placeholder="my-server" />
              </div>
              <div class="field">
                <label>协议</label>
                <select v-model="newMcp.type">
                  <option value="stdio">stdio（本地进程）</option>
                  <option value="streamable_http">streamable_http（远程）</option>
                </select>
              </div>
              <template v-if="newMcp.type === 'stdio'">
                <div class="field">
                  <label>启动命令</label>
                  <input v-model="newMcp.command" placeholder="npx" />
                </div>
                <div class="field">
                  <label>参数（空格分隔）</label>
                  <input v-model="newMcp.args" placeholder="-y @modelcontextprotocol/server-memory" />
                </div>
              </template>
              <template v-else>
                <div class="field">
                  <label>URL</label>
                  <input v-model="newMcp.url" placeholder="https://mcp.example.com/sse" />
                </div>
              </template>
              <button class="btn-save" style="margin-top:0.5rem" @click="onAddMcpServer" :disabled="!newMcp.name.trim() || (newMcp.type === 'stdio' ? !newMcp.command.trim() : !newMcp.url.trim())">添加</button>
            </div>
          </div>

          <!-- Web & Logging -->
          <div class="settings-section">
            <div class="section-title">其他</div>
            <div class="field">
              <label>前端展示条数</label>
              <input type="number" v-model.number="globalFields.history_limit" min="10" max="1000" />
            </div>
            <div class="field">
              <label>上下文加载条数</label>
              <input type="number" v-model.number="globalFields.context_limit" min="10" max="500" />
            </div>
            <div class="field">
              <label>压缩保留条数</label>
              <input type="number" v-model.number="globalFields.keep_recent" min="5" max="200" />
            </div>
            <div class="field">
              <label>保留轮次预算比例</label>
              <input type="number" v-model.number="globalFields.keep_budget_ratio" min="0.05" max="0.5" step="0.05" />
              <small>压缩后保留轮次占上下文窗口比例</small>
            </div>
            <div class="field">
              <label>预压缩触发比例</label>
              <input type="number" v-model.number="globalFields.early_compact_ratio" min="0.5" max="0.95" step="0.05" />
              <small>预压缩阈值相对 context_usage_threshold 的比例</small>
            </div>
            <div class="field">
              <label>压缩摘要缓存条数上限</label>
              <input type="number" v-model.number="globalFields.max_cached_summaries" min="50" max="1000" step="50" />
              <small>增量压缩摘要缓存条数上限（非上下文消息），超过时自动清理最旧轮次的摘要</small>
            </div>
            <div class="field">
              <label>工具输出截断字符数</label>
              <input type="number" v-model.number="globalFields.max_result_chars" min="1000" max="50000" step="1000" />
            </div>
            <div class="field">
              <label>日志级别</label>
              <select v-model="globalFields.log_level">
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
          </div>
        </div>

        <div class="settings-footer">
          <button class="btn-cancel" @click="emit('close')">取消</button>
          <button class="btn-save" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>
    <!-- Add Model Modal -->
    <Teleport to="body">
      <div v-if="showAddModel" class="modal-overlay" @click="showAddModel = false; resetNewModel()">
        <div class="modal-card" @click.stop>
          <div class="modal-header">
            <h3>添加新模型</h3>
            <button class="settings-close" @click="showAddModel = false; resetNewModel()">✕</button>
          </div>
          <div class="modal-body">
            <div class="field">
              <label>配置名称</label>
              <input v-model="newModel.name" placeholder="例如 my-claude" />
            </div>
            <div class="field">
              <label>协议类型</label>
              <select v-model="newModel.api_mode">
                <option value="openai">OpenAI 兼容</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div class="field">
              <label>API URL</label>
              <input v-model="newModel.api_url" :placeholder="newModel.api_mode === 'anthropic' ? 'https://api.anthropic.com/v1/messages' : 'https://api.openai.com/v1/chat/completions'" />
            </div>
            <div class="field">
              <label>API Key</label>
              <input v-model="newModel.api_key" type="password" placeholder="sk-..." />
            </div>
            <div class="field">
              <label>模型 ID</label>
              <input v-model="newModel.model" :placeholder="newModel.api_mode === 'anthropic' ? 'claude-sonnet-4-20250514' : 'gpt-4o'" />
            </div>
            <div class="field-row-inline">
              <div class="field" style="flex:1">
                <label>Context Length</label>
                <input type="number" v-model.number="newModel.context_length" step="1000" min="1000" />
              </div>
              <div class="field" style="flex:1">
                <label>Temperature</label>
                <input type="number" v-model.number="newModel.temperature" step="0.1" min="0" max="2" />
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn-cancel" @click="showAddModel = false; resetNewModel()">取消</button>
            <button class="btn-save" @click="onAddModel" :disabled="!newModel.name.trim() || !newModel.api_key.trim() || !newModel.api_url.trim() || !newModel.model.trim()">添加</button>
          </div>
        </div>
      </div>
    </Teleport>
  </Teleport>
</template>

<style scoped>
.settings-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  z-index: 1000;
  display: flex;
  justify-content: flex-end;
}
.settings-panel {
  width: 380px;
  max-width: 90vw;
  height: 100vh;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.2rem 1.5rem;
  border-bottom: 0.5px solid var(--border);
}
.settings-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--fg);
  margin: 0;
}
.settings-close {
  background: none;
  border: none;
  color: var(--fg-dim);
  font-size: 1.2rem;
  cursor: pointer;
  padding: 0.2rem;
}
.settings-loading {
  padding: 2rem;
  color: var(--fg-dim);
  text-align: center;
}
.settings-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 1.5rem;
}
.settings-section {
  margin-bottom: 1.5rem;
}
.section-title {
  font-family: 'Playfair Display', serif;
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 0.8rem;
  padding-bottom: 0.3rem;
  border-bottom: 0.5px solid var(--border-light);
}
.field {
  margin-bottom: 0.7rem;
}
.field label {
  display: block;
  font-size: 0.82rem;
  color: var(--fg-dim);
  margin-bottom: 0.25rem;
  font-family: 'JetBrains Mono', monospace;
}
.field input[type="number"],
.field input[type="text"],
.field input[type="password"],
.field select {
  width: 100%;
  padding: 0.4rem 0.6rem;
  font-size: 0.88rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-input);
  color: var(--fg);
  outline: none;
  font-family: 'Source Sans 3', sans-serif;
}
.field input[type="number"]:focus,
.field select:focus {
  border-color: var(--accent);
}
.field-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.7rem;
}
.field-row input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
  cursor: pointer;
}
.field-row label {
  font-size: 0.88rem;
  color: var(--fg);
  cursor: pointer;
}
.settings-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.8rem;
  padding: 1rem 1.5rem;
  border-top: 0.5px solid var(--border);
}
.btn-cancel {
  padding: 0.5rem 1.2rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--fg-dim);
  cursor: pointer;
  font-size: 0.9rem;
}
.btn-cancel:hover { background: var(--bg-card); }
.btn-save {
  padding: 0.5rem 1.2rem;
  border: none;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 500;
}
.btn-save:hover:not(:disabled) { background: var(--accent-hover); }
.btn-save:disabled { opacity: 0.5; cursor: default; }

.btn-add-model {
  background: none;
  border: 1px dashed var(--accent);
  color: var(--accent);
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
  font-family: 'Source Sans 3', sans-serif;
  width: 100%;
  text-align: center;
}
.btn-add-model:hover { background: var(--bg-thinking); }
.mcp-server-list { margin-bottom: 0.5rem; }
.mcp-srv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0;
  border-bottom: 0.5px solid var(--border-light);
}
.mcp-srv-item:last-child { border-bottom: none; }
.mcp-srv-info { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; min-width: 0; }
.mcp-srv-name { font-size: 0.85rem; font-weight: 500; color: var(--fg); }
.mcp-srv-badge {
  font-size: 0.68rem; padding: 0.1rem 0.35rem; border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
}
.mcp-srv-badge.stdio { background: #10a37f18; color: #10a37f; border: 1px solid #10a37f30; }
.mcp-srv-badge.streamable_http { background: #6366f118; color: #6366f1; border: 1px solid #6366f130; }
.mcp-srv-status { font-size: 0.7rem; }
.mcp-srv-status.connected { color: #4CAF50; }
.mcp-srv-status.disconnected { color: var(--fg-dim); }
.mcp-srv-status.disabled { color: #e55; }
.mcp-note {
  font-size: 0.78rem;
  color: var(--fg-dim);
  line-height: 1.4;
  padding: 0.3rem 0;
}
.field-row-inline {
  display: flex;
  gap: 0.6rem;
}
.model-list {
  margin-bottom: 0.8rem;
}
.model-list-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.6rem;
  border-radius: 6px;
  margin-bottom: 2px;
  transition: background 0.15s ease;
}
.model-list-item:hover { background: var(--bg-card); }
.model-list-item-active { background: var(--bg-thinking); }
.model-list-info {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  min-width: 0;
}
.model-list-name {
  font-weight: 500;
  font-size: 0.88rem;
  color: var(--fg);
}
.model-list-badge {
  font-size: 0.68rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
}
.model-list-badge.openai {
  background: #10a37f18;
  color: #10a37f;
  border: 1px solid #10a37f30;
}
.model-list-badge.anthropic {
  background: #d4a57418;
  color: #d4a574;
  border: 1px solid #d4a57430;
}
.model-list-id {
  font-size: 0.75rem;
  color: var(--fg-dim);
  font-family: 'JetBrains Mono', monospace;
}
.model-list-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
}
.model-list-active {
  font-size: 0.72rem;
  color: #4CAF50;
}
.btn-remove-model {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.75rem;
  opacity: 0.3;
  padding: 0.2rem 0.4rem;
  color: #e55;
  border-radius: 4px;
}
.btn-remove-model:hover { opacity: 1; background: #e5515; }

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 1100;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-card {
  width: 420px;
  max-width: 90vw;
  max-height: 85vh;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0,0,0,0.2);
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.5rem;
  border-bottom: 0.5px solid var(--border);
}
.modal-header h3 {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--fg);
  margin: 0;
}
.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.2rem 1.5rem;
}
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.8rem;
  padding: 1rem 1.5rem;
  border-top: 0.5px solid var(--border);
}
</style>
