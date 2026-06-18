<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import type { WorkflowEndData, WorkflowStartData, WorkflowState, WorkflowTaskEndData, WorkflowTaskStartData, WorkflowTaskStatus } from '../api'

type TaskInfo = WorkflowState['tasks'][string]

interface GraphNode {
  id: string
  agent: string
  status: WorkflowTaskStatus
  prompt?: string
  x: number
  y: number
  level: number  // 层级（基于依赖深度）
}

interface GraphEdge {
  from: string
  to: string
  status: WorkflowTaskStatus  // 连线状态取决于源节点
}

const props = defineProps<{
  visible?: boolean
  embedded?: boolean
  sessionWorkflowState?: WorkflowState  // 从父组件传入的当前会话工作流状态
}>()
const emit = defineEmits(['close'])

// 本地工作流状态（用于展示）
const localState = ref<WorkflowState>({
  status: 'idle',
  tasks: {}
})

// 监听父组件传入的会话状态，同步到本地状态
watch(() => props.sessionWorkflowState, (newState) => {
  console.log('[WorkflowPanel] watch sessionWorkflowState:', newState)
  // 如果有有效的工作流状态，同步它
  if (newState && (newState.status !== 'idle' || Object.keys(newState.tasks).length > 0)) {
    localState.value = {
      status: newState.status,
      tasks: { ...newState.tasks },
      elapsed: newState.elapsed,
      completed: newState.completed,
      failed: newState.failed,
      total: newState.total
    }
    console.log('[WorkflowPanel] localState updated:', localState.value)
  } else {
    // 否则清空本地状态
    localState.value = { status: 'idle', tasks: {} }
    console.log('[WorkflowPanel] localState cleared')
  }
}, { immediate: true, deep: true })

// 如果有 props.sessionWorkflowState 则使用它，否则使用本地事件状态
const workflowState = computed(() => {
  // 如果有传入的会话状态，且它有实际内容（非 idle 或有任务），优先使用
  if (props.sessionWorkflowState && (props.sessionWorkflowState.status !== 'idle' || Object.keys(props.sessionWorkflowState.tasks).length > 0)) {
    return props.sessionWorkflowState
  }
  // 否则使用本地状态（由事件更新）
  return localState.value
})

const activeTab = ref<'running' | 'history' | 'graph'>('running')
const expandedTasks = ref<Set<string>>(new Set())
const historyList = ref<Array<{time: string, state: WorkflowState}>>([])

// ── 图数据计算 ──
const NODE_WIDTH = 140
const NODE_HEIGHT = 70
const HORIZONTAL_GAP = 60
const VERTICAL_GAP = 50

// 计算节点层级（基于依赖深度）
function calculateLevels(tasks: Record<string, TaskInfo>): Map<string, number> {
  const levels = new Map<string, number>()
  const visited = new Set<string>()
  
  function getLevel(taskId: string): number {
    if (levels.has(taskId)) return levels.get(taskId)!
    if (visited.has(taskId)) return 0  // 防止循环依赖
    
    visited.add(taskId)
    const task = tasks[taskId]
    if (!task || !task.depends_on || task.depends_on.length === 0) {
      levels.set(taskId, 0)
      return 0
    }
    
    const maxDepLevel = Math.max(...task.depends_on.map(depId => getLevel(depId)))
    const level = maxDepLevel + 1
    levels.set(taskId, level)
    return level
  }
  
  Object.keys(tasks).forEach(taskId => getLevel(taskId))
  return levels
}

// 构建图节点（带布局坐标）
const graphNodes = computed<GraphNode[]>(() => {
  const tasks = workflowState.value.tasks
  if (Object.keys(tasks).length === 0) return []
  
  const levels = calculateLevels(tasks)
  const levelGroups = new Map<number, string[]>()
  
  // 按层级分组
  levels.forEach((level, taskId) => {
    if (!levelGroups.has(level)) levelGroups.set(level, [])
    levelGroups.get(level)!.push(taskId)
  })
  
  // 计算每个节点的坐标
  const nodes: GraphNode[] = []
  let maxWidth = 0
  
  levelGroups.forEach((taskIds, level) => {
    const totalWidth = taskIds.length * NODE_WIDTH + (taskIds.length - 1) * HORIZONTAL_GAP
    maxWidth = Math.max(maxWidth, totalWidth)
  })
  
  levelGroups.forEach((taskIds, level) => {
    const totalWidth = taskIds.length * NODE_WIDTH + (taskIds.length - 1) * HORIZONTAL_GAP
    const startX = (maxWidth - totalWidth) / 2  // 居中对齐
    
    taskIds.forEach((taskId, index) => {
      const task = tasks[taskId]
      nodes.push({
        id: taskId,
        agent: task.agent,
        status: task.status,
        prompt: task.prompt,
        x: startX + index * (NODE_WIDTH + HORIZONTAL_GAP),
        y: level * (NODE_HEIGHT + VERTICAL_GAP),
        level
      })
    })
  })
  
  return nodes
})

// 构建图边（连线）
const graphEdges = computed<GraphEdge[]>(() => {
  const tasks = workflowState.value.tasks
  const edges: GraphEdge[] = []
  
  Object.values(tasks).forEach(task => {
    if (task.depends_on && task.depends_on.length > 0) {
      task.depends_on.forEach(depId => {
        const depTask = tasks[depId]
        edges.push({
          from: depId,
          to: task.id,
          status: depTask?.status || 'pending'
        })
      })
    }
  })
  
  return edges
})

// 计算SVG画布尺寸
const graphSize = computed(() => {
  const nodes = graphNodes.value
  if (nodes.length === 0) return { width: 400, height: 300 }
  
  const maxX = Math.max(...nodes.map(n => n.x)) + NODE_WIDTH
  const maxY = Math.max(...nodes.map(n => n.y)) + NODE_HEIGHT
  
  return {
    width: Math.max(400, maxX + 40),
    height: Math.max(300, maxY + 40)
  }
})

const hasWorkflow = computed(() => workflowState.value.status !== 'idle' || Object.keys(workflowState.value.tasks).length > 0)
const runningTasks = computed(() => 
  Object.values(workflowState.value.tasks)
    .filter(t => t.status === 'running')
)
const pendingTasks = computed(() => 
  Object.values(workflowState.value.tasks)
    .filter(t => t.status === 'pending')
)
const doneTasks = computed(() =>
  Object.values(workflowState.value.tasks)
    .filter(t => t.status === 'done' || t.status === 'skipped')
)
const failedTasks = computed(() =>
  Object.values(workflowState.value.tasks)
    .filter(t => t.status === 'failed')
)
const progress = computed(() => {
  const total = Object.keys(workflowState.value.tasks).length
  if (total === 0) return 0
  const done = doneTasks.value.length + failedTasks.value.length
  return Math.round((done / total) * 100)
})

type WorkflowPanelEvent =
  | { event: 'workflow_start'; data: WorkflowStartData }
  | { event: 'task_start'; data: WorkflowTaskStartData }
  | { event: 'task_end'; data: WorkflowTaskEndData }
  | { event: 'workflow_end'; data: WorkflowEndData }

function handleWorkflowEvent(event: CustomEvent<WorkflowPanelEvent>) {
  const detail = event.detail
  if (!detail || !detail.event) return

  // 只更新本地状态（如果有 props.sessionWorkflowState，会由父组件控制）
  switch (detail.event) {
    case 'workflow_start':
      localState.value = {
        status: 'running',
        tasks: {},
        total: detail.data.total || detail.data.tasks.length || 0
      }
      detail.data.tasks.forEach((t) => {
        localState.value.tasks[t.id] = {
          id: t.id,
          agent: t.agent,
          status: 'pending',
          prompt: t.prompt,
          depends_on: t.depends_on || []
        }
      })
      break

    case 'task_start':
      if (detail.data.id) {
        const taskId = detail.data.id
        if (!localState.value.tasks[taskId]) {
          localState.value.tasks[taskId] = {
            id: taskId,
            agent: detail.data.agent || '',
            status: 'running',
            prompt: detail.data.prompt || '',
            depends_on: []
          }
        } else {
          localState.value.tasks[taskId].status = 'running'
        }
      }
      break

    case 'task_end':
      if (detail.data.id) {
        const taskId = detail.data.id
        if (localState.value.tasks[taskId]) {
          localState.value.tasks[taskId].status = detail.data.status === 'done' || detail.data.status === 'skipped' ? detail.data.status : 'failed'
          localState.value.tasks[taskId].result = detail.data.result_preview || detail.data.error
        }
      }
      break

    case 'workflow_end':
      localState.value.status = (detail.data.failed || 0) > 0 ? 'failed' : 'done'
      localState.value.elapsed = detail.data.elapsed
      localState.value.completed = detail.data.completed
      localState.value.failed = detail.data.failed
      // 确保所有未完成的任务都标记为最终状态
      Object.keys(localState.value.tasks).forEach(taskId => {
        const task = localState.value.tasks[taskId]
        if (task.status === 'pending' || task.status === 'running') {
          task.status = 'done'
        }
      })
      historyList.value.unshift({
        time: new Date().toLocaleString(),
        state: { ...localState.value }
      })
      if (historyList.value.length > 20) historyList.value.pop()
      break
  }
}

function toggleExpand(taskId: string) {
  if (expandedTasks.value.has(taskId)) {
    expandedTasks.value.delete(taskId)
  } else {
    expandedTasks.value.add(taskId)
  }
}

function getAgentIcon(agent: string): string {
  if (agent.includes('coder') || agent.includes('code')) return '💻'
  if (agent.includes('research') || agent.includes('search')) return '🔍'
  if (agent.includes('review')) return '👀'
  if (agent.includes('test')) return '🧪'
  if (agent.includes('plan')) return '📋'
  if (agent.includes('vision')) return '👁'
  return '🤖'
}

function statusGradient(s: WorkflowTaskStatus): string {
  if (s === 'running') return 'linear-gradient(135deg, #2196f3 0%, #42a5f5 100%)'
  if (s === 'pending') return 'linear-gradient(135deg, #ff9800 0%, #ffb74d 100%)'
  if (s === 'done') return 'linear-gradient(135deg, #4caf50 0%, #81c784 100%)'
  if (s === 'failed') return 'linear-gradient(135deg, #f44336 0%, #e57373 100%)'
  if (s === 'skipped') return 'linear-gradient(135deg, #9e9e9e 0%, #bdbdbd 100%)'
  return 'linear-gradient(135deg, #9e9e9e 0%, #bdbdbd 100%)'
}

function statusIcon(s: WorkflowTaskStatus): string {
  if (s === 'running') return '⚡'
  if (s === 'pending') return '⏳'
  if (s === 'done') return '✓'
  if (s === 'failed') return '✗'
  if (s === 'skipped') return '⏭'
  return '○'
}

function statusLabel(s: WorkflowTaskStatus) {
  if (s === 'running') return '执行中'
  if (s === 'pending') return '等待中'
  if (s === 'done') return '已完成'
  if (s === 'failed') return '失败'
  if (s === 'skipped') return '已跳过'
  return s
}

function clearHistory() {
  historyList.value = []
}

// ── 图谱辅助函数 ──
function getNodeById(nodeId: string): GraphNode | undefined {
  return graphNodes.value.find(n => n.id === nodeId)
}

// 生成贝塞尔曲线路径
function generateEdgePath(edge: GraphEdge): string {
  const fromNode = getNodeById(edge.from)
  const toNode = getNodeById(edge.to)
  
  if (!fromNode || !toNode) return ''
  
  const fromX = fromNode.x + NODE_WIDTH / 2
  const fromY = fromNode.y + NODE_HEIGHT
  const toX = toNode.x + NODE_WIDTH / 2
  const toY = toNode.y
  
  // 贝塞尔曲线控制点
  const midY = (fromY + toY) / 2
  
  return `M ${fromX} ${fromY} C ${fromX} ${midY}, ${toX} ${midY}, ${toX} ${toY}`
}

onMounted(() => {
  window.addEventListener('workflow-event', handleWorkflowEvent as EventListener)
})

onUnmounted(() => {
  window.removeEventListener('workflow-event', handleWorkflowEvent as EventListener)
})
</script>

<template>
  <Teleport to="body" :disabled="!!embedded">
    <div v-if="visible || embedded" :class="[embedded ? 'workflow-overlay-embedded' : 'workflow-overlay']" @click="embedded ? null : emit('close')">
      <div :class="[embedded ? 'workflow-panel-embedded' : 'workflow-panel']" @click.stop>
        <!-- Header -->
        <div class="workflow-header">
          <div class="header-left">
            <span class="workflow-icon">🔀</span>
            <h3 class="workflow-title">工作流</h3>
            <span v-if="workflowState.status === 'running'" class="status-badge running">
              <span class="pulse-dot"></span>
              运行中
            </span>
          </div>
          <button class="workflow-close" @click="emit('close')">✕</button>
        </div>
        
        <!-- Progress Section -->
        <div v-if="workflowState.status === 'running'" class="progress-section">
          <div class="progress-header">
            <div class="progress-stats">
              <span class="stat-item">
                <span class="stat-label">进度</span>
                <span class="stat-value">{{ progress }}%</span>
              </span>
              <span class="stat-divider">|</span>
              <span class="stat-item">
                <span class="stat-label">任务</span>
                <span class="stat-value">{{ doneTasks.length + failedTasks.length }} / {{ Object.keys(workflowState.tasks).length }}</span>
              </span>
            </div>
          </div>
          <div class="progress-bar-container">
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: progress + '%' }">
                <div class="progress-shine"></div>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Tabs -->
        <div class="panel-tabs">
          <button :class="['panel-tab', { active: activeTab === 'running' }]" @click="activeTab = 'running'">
            <span class="tab-icon">▶</span>
            <span>执行中</span>
            <span v-if="runningTasks.length > 0" class="tab-badge running">{{ runningTasks.length }}</span>
          </button>
          <button :class="['panel-tab', { active: activeTab === 'history' }]" @click="activeTab = 'history'">
            <span class="tab-icon">📜</span>
            <span>历史</span>
            <span v-if="historyList.length > 0" class="tab-badge">{{ historyList.length }}</span>
          </button>
          <button :class="['panel-tab', { active: activeTab === 'graph' }]" @click="activeTab = 'graph'">
            <span class="tab-icon">📊</span>
            <span>图谱</span>
          </button>
        </div>
        
        <!-- Running Tab -->
        <div v-if="activeTab === 'running'" class="workflow-list">
          <div v-if="!hasWorkflow" class="workflow-empty">
            <div class="empty-icon">🔀</div>
            <div class="empty-text">暂无运行中的工作流</div>
            <div class="empty-hint">在对话中让 AI 调用 <code>run_workflow</code> 启动工作流</div>
          </div>
          
          <!-- Pending Tasks -->
          <div v-if="pendingTasks.length > 0" class="task-section">
            <div class="section-header">
              <span class="section-icon">⏳</span>
              <span class="section-title">等待中</span>
              <span class="section-count">{{ pendingTasks.length }}</span>
            </div>
            <div class="task-grid">
              <div v-for="task in pendingTasks" :key="task.id" class="task-card pending">
                <div class="task-status-bar" :style="{ background: statusGradient(task.status) }"></div>
                <div class="task-body">
                  <div class="task-header">
                    <span class="task-icon">{{ getAgentIcon(task.agent) }}</span>
                    <div class="task-meta">
                      <span class="task-id">{{ task.id }}</span>
                      <span class="task-agent">{{ task.agent }}</span>
                    </div>
                  </div>
                  <div v-if="task.prompt" class="task-prompt-preview">{{ task.prompt.slice(0, 60) }}{{ task.prompt.length > 60 ? '...' : '' }}</div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Running Tasks -->
          <div v-if="runningTasks.length > 0" class="task-section">
            <div class="section-header">
              <span class="section-icon pulse">⚡</span>
              <span class="section-title">执行中</span>
              <span class="section-count running">{{ runningTasks.length }}</span>
            </div>
            <div class="task-grid">
              <div v-for="task in runningTasks" :key="task.id" class="task-card running">
                <div class="task-status-bar" :style="{ background: statusGradient(task.status) }">
                  <div class="status-bar-animation"></div>
                </div>
                <div class="task-body">
                  <div class="task-header">
                    <span class="task-icon spinning">{{ getAgentIcon(task.agent) }}</span>
                    <div class="task-meta">
                      <span class="task-id">{{ task.id }}</span>
                      <span class="task-agent">{{ task.agent }}</span>
                    </div>
                    <div class="task-spinner"></div>
                  </div>
                  <div v-if="task.prompt" class="task-prompt-preview">{{ task.prompt.slice(0, 80) }}{{ task.prompt.length > 80 ? '...' : '' }}</div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Done Tasks -->
          <div v-if="doneTasks.length > 0" class="task-section">
            <div class="section-header">
              <span class="section-icon">✓</span>
              <span class="section-title">已完成</span>
              <span class="section-count success">{{ doneTasks.length }}</span>
            </div>
            <div class="task-grid">
              <div v-for="task in doneTasks" :key="task.id" :class="['task-card', task.status]" @click="toggleExpand(task.id)">
                <div class="task-status-bar" :style="{ background: statusGradient(task.status) }"></div>
                <div class="task-body">
                  <div class="task-header">
                    <span class="task-icon">{{ getAgentIcon(task.agent) }}</span>
                    <div class="task-meta">
                      <span class="task-id">{{ task.id }}</span>
                      <span class="task-agent">{{ task.agent }}</span>
                    </div>
                    <span class="task-toggle">{{ expandedTasks.has(task.id) ? '▾' : '▸' }}</span>
                  </div>
                  <div v-if="task.prompt" class="task-prompt-preview">{{ task.prompt.slice(0, 60) }}{{ task.prompt.length > 60 ? '...' : '' }}</div>
                  <div v-if="expandedTasks.has(task.id) && task.result" class="task-result">
                    <div class="result-label">执行结果</div>
                    <div class="result-content">{{ task.result }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Failed Tasks -->
          <div v-if="failedTasks.length > 0" class="task-section">
            <div class="section-header">
              <span class="section-icon">✗</span>
              <span class="section-title">失败</span>
              <span class="section-count failed">{{ failedTasks.length }}</span>
            </div>
            <div class="task-grid">
              <div v-for="task in failedTasks" :key="task.id" class="task-card failed" @click="toggleExpand(task.id)">
                <div class="task-status-bar" :style="{ background: statusGradient(task.status) }"></div>
                <div class="task-body">
                  <div class="task-header">
                    <span class="task-icon">{{ getAgentIcon(task.agent) }}</span>
                    <div class="task-meta">
                      <span class="task-id">{{ task.id }}</span>
                      <span class="task-agent">{{ task.agent }}</span>
                    </div>
                    <span class="task-toggle">{{ expandedTasks.has(task.id) ? '▾' : '▸' }}</span>
                  </div>
                  <div v-if="task.prompt" class="task-prompt-preview">{{ task.prompt.slice(0, 60) }}{{ task.prompt.length > 60 ? '...' : '' }}</div>
                  <div v-if="expandedTasks.has(task.id) && task.result" class="task-result error">
                    <div class="result-label">错误信息</div>
                    <div class="result-content">{{ task.result }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Summary -->
          <div v-if="workflowState.status !== 'running' && workflowState.elapsed" class="workflow-summary">
            <div class="summary-header">
              <span class="summary-icon">{{ workflowState.status === 'done' ? '🎉' : '⚠️' }}</span>
              <span class="summary-title">工作流{{ workflowState.status === 'done' ? '完成' : '结束' }}</span>
            </div>
            <div class="summary-stats">
              <div class="summary-stat">
                <span class="stat-icon">⏱</span>
                <span class="stat-label">耗时</span>
                <span class="stat-value">{{ workflowState.elapsed }}s</span>
              </div>
              <div class="summary-stat">
                <span class="stat-icon">✓</span>
                <span class="stat-label">成功</span>
                <span class="stat-value success">{{ workflowState.completed || doneTasks.length }}</span>
              </div>
              <div v-if="workflowState.failed || failedTasks.length > 0" class="summary-stat">
                <span class="stat-icon">✗</span>
                <span class="stat-label">失败</span>
                <span class="stat-value failed">{{ workflowState.failed || failedTasks.length }}</span>
              </div>
            </div>
          </div>
        </div>
        
        <!-- History Tab -->
        <div v-if="activeTab === 'history'" class="workflow-list">
          <div v-if="historyList.length > 0" class="history-clear-bar">
            <button class="history-clear-btn" @click="clearHistory">
              <span>🗑</span>
              <span>清空历史</span>
            </button>
          </div>
          <div v-if="historyList.length === 0" class="workflow-empty">
            <div class="empty-icon">📜</div>
            <div class="empty-text">暂无历史记录</div>
            <div class="empty-hint">已完成的工作流会显示在这里</div>
          </div>
          <div class="history-list">
            <div v-for="(item, idx) in historyList" :key="idx" class="history-card">
              <div class="history-header">
                <span :class="['history-status-badge', item.state.status]">
                  {{ item.state.status === 'done' ? '✓ 完成' : '✗ 失败' }}
                </span>
                <span class="history-time">{{ item.time }}</span>
              </div>
              <div class="history-stats">
                <div class="history-stat">
                  <span class="hs-label">任务</span>
                  <span class="hs-value">{{ item.state.completed || 0 }} / {{ item.state.total || 0 }}</span>
                </div>
                <div class="history-stat">
                  <span class="hs-label">耗时</span>
                  <span class="hs-value">{{ item.state.elapsed }}s</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Graph Tab -->
        <div v-if="activeTab === 'graph'" class="graph-container">
          <div v-if="!hasWorkflow" class="workflow-empty">
            <div class="empty-icon">📊</div>
            <div class="empty-text">暂无工作流图谱</div>
            <div class="empty-hint">启动工作流后可在此查看任务依赖关系</div>
          </div>
          
          <svg v-else 
               :width="graphSize.width" 
               :height="graphSize.height" 
               class="graph-canvas">
            <!-- 定义箭头标记 -->
            <defs>
              <marker id="arrowhead" markerWidth="10" markerHeight="10"
                      refX="9" refY="3" orient="auto">
                <path d="M0,0 L0,6 L9,3 z" fill="#9e9e9e" />
              </marker>
              <marker id="arrowhead-pending" markerWidth="10" markerHeight="10"
                      refX="9" refY="3" orient="auto">
                <path d="M0,0 L0,6 L9,3 z" fill="#ff9800" />
              </marker>
              <marker id="arrowhead-running" markerWidth="10" markerHeight="10"
                      refX="9" refY="3" orient="auto">
                <path d="M0,0 L0,6 L9,3 z" fill="#2196f3" />
              </marker>
              <marker id="arrowhead-done" markerWidth="10" markerHeight="10" 
                      refX="9" refY="3" orient="auto">
                <path d="M0,0 L0,6 L9,3 z" fill="#4caf50" />
              </marker>
              <marker id="arrowhead-failed" markerWidth="10" markerHeight="10"
                      refX="9" refY="3" orient="auto">
                <path d="M0,0 L0,6 L9,3 z" fill="#f44336" />
              </marker>
              <marker id="arrowhead-skipped" markerWidth="10" markerHeight="10"
                      refX="9" refY="3" orient="auto">
                <path d="M0,0 L0,6 L9,3 z" fill="#9e9e9e" />
              </marker>
            </defs>
            
            <!-- 连线 -->
            <g class="edges">
              <path v-for="edge in graphEdges" 
                    :key="`${edge.from}-${edge.to}`"
                    :d="generateEdgePath(edge)"
                    :class="['graph-edge', edge.status]"
                    :marker-end="`url(#arrowhead-${edge.status})`" />
            </g>
            
            <!-- 节点 -->
            <g class="nodes">
              <g v-for="node in graphNodes" 
                 :key="node.id"
                 :transform="`translate(${node.x}, ${node.y})`"
                 class="graph-node"
                 @click="toggleExpand(node.id)">
                <!-- 节点背景 -->
                <rect class="node-bg"
                      :class="node.status"
                      width="140"
                      height="70"
                      rx="8"
                      ry="8" />
                
                <!-- 顶部状态条 -->
                <rect class="node-status-bar"
                      :class="node.status"
                      width="140"
                      height="4"
                      rx="2"
                      ry="2" />
                
                <!-- 节点内容 -->
                <text class="node-icon" x="12" y="28">{{ getAgentIcon(node.agent) }}</text>
                <text class="node-id" x="36" y="26">{{ node.id }}</text>
                <text class="node-agent" x="36" y="44">{{ node.agent }}</text>
                
                <!-- 状态图标 -->
                <text class="node-status-icon" x="120" y="30">
                  {{ statusIcon(node.status) }}
                </text>
                
                <!-- 执行中动画 -->
                <g v-if="node.status === 'running'" class="node-spinner">
                  <circle cx="120" cy="50" r="6" 
                          fill="none" 
                          stroke="#2196f3" 
                          stroke-width="2"
                          stroke-dasharray="12"
                          stroke-linecap="round">
                    <animateTransform attributeName="transform"
                                      type="rotate"
                                      from="0 120 50"
                                      to="360 120 50"
                                      dur="1s"
                                      repeatCount="indefinite" />
                  </circle>
                </g>
              </g>
            </g>
          </svg>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.workflow-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  backdrop-filter: blur(4px);
  z-index: 200;
  animation: fadeIn 0.2s ease;
}

.workflow-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 380px;
  background: var(--bg);
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 32px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

@keyframes slideIn { 
  from { transform: translateX(100%); opacity: 0.8; } 
  to { transform: translateX(0); opacity: 1; } 
}

@keyframes fadeIn { 
  from { opacity: 0; } 
  to { opacity: 1; } 
}

/* Header */
.workflow-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.2rem 1.4rem;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg) 100%);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.workflow-icon {
  font-size: 1.3rem;
}

.workflow-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--fg);
  margin: 0;
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.25rem 0.6rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 500;
}

.status-badge.running {
  background: linear-gradient(135deg, #2196f3 0%, #42a5f5 100%);
  color: white;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  background: white;
  border-radius: 50%;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

.workflow-close {
  width: 32px;
  height: 32px;
  border: none;
  background: var(--bg);
  color: var(--fg-dim);
  font-size: 1.1rem;
  cursor: pointer;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.workflow-close:hover {
  background: var(--bg-thinking);
  color: var(--fg);
  transform: rotate(90deg);
}

/* Progress Section */
.progress-section {
  padding: 1rem 1.4rem;
  background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg) 100%);
  border-bottom: 1px solid var(--border);
}

.progress-header {
  margin-bottom: 0.6rem;
}

.progress-stats {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.stat-item {
  display: flex;
  align-items: baseline;
  gap: 0.3rem;
}

.stat-label {
  font-size: 0.75rem;
  color: var(--fg-dim);
}

.stat-value {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--fg);
}

.stat-divider {
  color: var(--border);
  font-size: 0.8rem;
}

.progress-bar-container {
  position: relative;
}

.progress-bar {
  height: 8px;
  background: var(--bg-thinking);
  border-radius: 4px;
  overflow: hidden;
  box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #4caf50 0%, #8bc34a 50%, #cddc39 100%);
  border-radius: 4px;
  transition: width 0.4s ease;
  position: relative;
  overflow: hidden;
}

.progress-shine {
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.3) 50%, transparent 100%);
  animation: shine 2s ease-in-out infinite;
}

@keyframes shine {
  0% { left: -100%; }
  100% { left: 100%; }
}

/* Tabs */
.panel-tabs {
  display: flex;
  gap: 4px;
  padding: 0.6rem 1.4rem;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.panel-tab {
  flex: 1;
  padding: 0.5rem 0.8rem;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  font-weight: 500;
}

.panel-tab:hover {
  background: var(--bg-thinking);
  color: var(--fg);
}

.panel-tab.active {
  background: var(--accent);
  color: white;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.3);
}

.tab-icon {
  font-size: 0.9rem;
}

.tab-badge {
  font-size: 0.7rem;
  padding: 0.15rem 0.4rem;
  border-radius: 10px;
  min-width: 18px;
  text-align: center;
  background: var(--bg-thinking);
  color: var(--fg-dim);
  font-weight: 600;
}

.tab-badge.running {
  background: rgba(33, 150, 243, 0.2);
  color: #2196f3;
  animation: badgePulse 2s ease-in-out infinite;
}

@keyframes badgePulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.panel-tab.active .tab-badge {
  background: rgba(255, 255, 255, 0.25);
  color: white;
}

/* Workflow List */
.workflow-list {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 1.2rem;
}

.workflow-empty {
  padding: 3rem 2rem;
  text-align: center;
}

.empty-icon {
  font-size: 3rem;
  opacity: 0.3;
  margin-bottom: 1rem;
}

.empty-text {
  font-size: 0.95rem;
  color: var(--fg-dim);
  margin-bottom: 0.5rem;
}

.empty-hint {
  font-size: 0.8rem;
  color: var(--fg-dim);
  opacity: 0.7;
}

.empty-hint code {
  background: var(--bg-thinking);
  padding: 0.2rem 0.4rem;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
}

/* Task Section */
.task-section {
  margin-bottom: 1.2rem;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
  padding: 0 0.2rem;
}

.section-icon {
  font-size: 1rem;
}

.section-icon.pulse {
  animation: iconPulse 1.5s ease-in-out infinite;
}

@keyframes iconPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.section-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--fg-dim);
}

.section-count {
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 10px;
  background: var(--bg-thinking);
  color: var(--fg-dim);
  font-weight: 600;
}

.section-count.running {
  background: rgba(33, 150, 243, 0.15);
  color: #2196f3;
}

.section-count.success {
  background: rgba(76, 175, 80, 0.15);
  color: #4caf50;
}

.section-count.failed {
  background: rgba(244, 67, 54, 0.15);
  color: #f44336;
}

/* Task Grid */
.task-grid {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

/* Task Card */
.task-card {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 10px;
  overflow: hidden;
  transition: all 0.2s ease;
  position: relative;
}

.task-card:hover {
  border-color: var(--accent);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.task-card.running {
  border-color: #2196f3;
  box-shadow: 0 4px 16px rgba(33, 150, 243, 0.2);
}

.task-card.done {
  opacity: 0.85;
}

.task-card.skipped {
  opacity: 0.75;
  border-style: dashed;
  border-color: #9e9e9e;
}

.task-card.failed {
  border-color: #f44336;
}

.task-status-bar {
  height: 3px;
  position: relative;
  overflow: hidden;
}

.status-bar-animation {
  position: absolute;
  top: 0;
  left: -100%;
  width: 200%;
  height: 100%;
  background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.5) 50%, transparent 100%);
  animation: slideRight 2s linear infinite;
}

@keyframes slideRight {
  0% { left: -100%; }
  100% { left: 100%; }
}

.task-body {
  padding: 0.8rem 1rem;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.4rem;
}

.task-icon {
  font-size: 1.3rem;
  line-height: 1;
}

.task-icon.spinning {
  animation: spin 2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.task-meta {
  flex: 1;
  min-width: 0;
}

.task-id {
  display: block;
  font-weight: 600;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9rem;
  color: var(--fg);
  margin-bottom: 0.15rem;
}

.task-agent {
  display: block;
  font-size: 0.75rem;
  color: var(--fg-dim);
}

.task-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--border);
  border-top-color: #2196f3;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.task-toggle {
  font-size: 0.8rem;
  color: var(--fg-dim);
  cursor: pointer;
  transition: transform 0.2s ease;
}

.task-prompt-preview {
  font-size: 0.78rem;
  color: var(--fg-dim);
  padding: 0.5rem;
  background: var(--bg);
  border-radius: 6px;
  margin-top: 0.4rem;
  line-height: 1.4;
}

.task-result {
  margin-top: 0.6rem;
  padding: 0.6rem;
  background: var(--bg);
  border-radius: 6px;
  border-left: 3px solid #4caf50;
}

.task-result.error {
  border-left-color: #f44336;
  background: rgba(244, 67, 54, 0.05);
}

.result-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 0.3rem;
}

.result-content {
  font-size: 0.82rem;
  color: var(--fg);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 150px;
  overflow-y: auto;
}

/* Workflow Summary */
.workflow-summary {
  margin-top: 1rem;
  padding: 1rem;
  background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg) 100%);
  border-radius: 12px;
  border: 1px solid var(--border);
}

.summary-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.8rem;
}

.summary-icon {
  font-size: 1.3rem;
}

.summary-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--fg);
}

.summary-stats {
  display: flex;
  gap: 1rem;
}

.summary-stat {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.6rem;
  background: var(--bg);
  border-radius: 8px;
  flex: 1;
}

.summary-stat .stat-icon {
  font-size: 1rem;
}

.summary-stat .stat-label {
  font-size: 0.75rem;
  color: var(--fg-dim);
}

.summary-stat .stat-value {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--fg);
  margin-left: auto;
}

.summary-stat .stat-value.success {
  color: #4caf50;
}

.summary-stat .stat-value.failed {
  color: #f44336;
}

/* History */
.history-clear-bar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 0.8rem;
}

.history-clear-btn {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  border: none;
  background: var(--bg-card);
  color: var(--fg-dim);
  font-size: 0.78rem;
  cursor: pointer;
  padding: 0.4rem 0.8rem;
  border-radius: 8px;
  border: 1px solid var(--border-light);
  transition: all 0.2s;
}

.history-clear-btn:hover {
  color: #f44336;
  border-color: #f44336;
  background: rgba(244, 67, 54, 0.05);
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.history-card {
  padding: 0.8rem 1rem;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 10px;
  transition: all 0.2s ease;
}

.history-card:hover {
  border-color: var(--accent);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.6rem;
}

.history-status-badge {
  padding: 0.25rem 0.6rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 600;
}

.history-status-badge.done {
  background: rgba(76, 175, 80, 0.15);
  color: #4caf50;
}

.history-status-badge.failed {
  background: rgba(244, 67, 54, 0.15);
  color: #f44336;
}

.history-time {
  font-size: 0.75rem;
  color: var(--fg-dim);
}

.history-stats {
  display: flex;
  gap: 1rem;
}

.history-stat {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.hs-label {
  font-size: 0.75rem;
  color: var(--fg-dim);
}

.hs-value {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--fg);
}

/* ── Embedded 模式 ── */
.workflow-overlay-embedded {
  position: static !important;
  background: transparent !important;
  inset: auto !important;
  z-index: auto !important;
  display: flex !important;
  height: 100% !important;
  pointer-events: auto !important;
  backdrop-filter: none !important;
}

.workflow-panel-embedded {
  position: static !important;
  width: 100% !important;
  height: 100% !important;
  max-height: 100% !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  overflow-y: auto !important;
}

.workflow-panel-embedded .workflow-header,
.workflow-panel-embedded .workflow-close {
  display: none;
}

/* ── Graph Styles ── */
.graph-container {
  flex: 1;
  overflow: auto;
  padding: 1.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.graph-canvas {
  display: block;
  margin: 0 auto;
}

.graph-edge {
  fill: none;
  stroke-width: 2;
  stroke: #9e9e9e;
  transition: all 0.3s ease;
}

.graph-edge:hover {
  stroke-width: 3;
}

.graph-edge.pending {
  stroke: #ff9800;
  stroke-dasharray: 5, 5;
}

.graph-edge.running {
  stroke: #2196f3;
  stroke-width: 3;
  animation: edgePulse 1.5s ease-in-out infinite;
}

@keyframes edgePulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.graph-edge.done {
  stroke: #4caf50;
}

.graph-edge.failed {
  stroke: #f44336;
  stroke-dasharray: 5, 5;
}

.graph-edge.skipped {
  stroke: #9e9e9e;
  stroke-dasharray: 5, 5;
}

.graph-node {
  cursor: pointer;
}

.graph-node:hover .node-bg {
  stroke-width: 2;
  filter: drop-shadow(0 4px 12px rgba(0, 0, 0, 0.15));
}

.node-bg {
  fill: var(--bg-card);
  stroke: var(--border);
  stroke-width: 1;
  transition: all 0.3s ease;
}

.node-bg.pending {
  stroke: #ff9800;
  stroke-dasharray: 3, 3;
}

.node-bg.running {
  stroke: #2196f3;
  stroke-width: 2;
  filter: drop-shadow(0 4px 12px rgba(33, 150, 243, 0.3));
}

.node-bg.done {
  stroke: #4caf50;
}

.node-bg.failed {
  stroke: #f44336;
  stroke-width: 2;
}

.node-bg.skipped {
  stroke: #9e9e9e;
  stroke-dasharray: 3, 3;
}

.node-status-bar {
  transition: fill 0.3s ease;
}

.node-status-bar.pending {
  fill: #ff9800;
}

.node-status-bar.running {
  fill: #2196f3;
  animation: statusBarPulse 2s ease-in-out infinite;
}

@keyframes statusBarPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

.node-status-bar.done {
  fill: #4caf50;
}

.node-status-bar.failed {
  fill: #f44336;
}

.node-status-bar.skipped {
  fill: #9e9e9e;
}

.node-icon {
  font-size: 18px;
  dominant-baseline: middle;
}

.node-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  fill: var(--fg);
  dominant-baseline: middle;
}

.node-agent {
  font-family: 'Source Sans 3', sans-serif;
  font-size: 11px;
  fill: var(--fg-dim);
  dominant-baseline: middle;
}

.node-status-icon {
  font-size: 14px;
  dominant-baseline: middle;
  text-anchor: middle;
}
</style>
