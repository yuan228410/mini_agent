<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { getSkills, deleteSkill, getMcpStatus, type SkillInfo, type McpConnectedServer, type McpConfiguredServer } from '../api'

const props = defineProps<{ visible: boolean, username?: string, workspace?: string }>()
const emit = defineEmits(['close', 'use'])
const skills = ref<SkillInfo[]>([])
const mcpEnabled = ref(false)
const mcpConfigured = ref<McpConfiguredServer[]>([])
const mcpConnected = ref<McpConnectedServer[]>([])
const activeTab = ref<'skills' | 'mcp'>('skills')

onMounted(async () => {
  await refresh()
})

watch(() => [props.visible, props.username, props.workspace], async ([vis]) => {
  if (vis) await refresh()
})

async function refresh() {
  try {
    const resp = await getSkills(props.username, props.workspace)
    if (Array.isArray(resp.skills)) {
      skills.value = resp.skills
    }
  } catch {}

  try {
    const mcp = await getMcpStatus()
    mcpEnabled.value = mcp.enabled
    mcpConfigured.value = mcp.configured || []
    mcpConnected.value = mcp.connected || []
    if (mcp.enabled) activeTab.value = 'mcp'
  } catch {}
}

function useSkill(name: string) {
  emit('use', name)
  emit('close')
}

async function onDelete(name: string, tier: string) {
  if (tier === 'extra') {
    alert('扩展路径技能为只读，不可删除')
    return
  }
  if (tier === 'global') {
    if (!confirm(`确定删除全局技能 "${name}"？这将影响所有用户。`)) return
  }
  try {
    const result = await deleteSkill(name, props.username, props.workspace)
    if (result.ok) {
      await refresh()
    } else {
      alert(result.error || '删除失败')
    }
  } catch (e) {
    alert('删除失败')
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="skill-overlay" @click="emit('close')">
      <div class="skill-panel" @click.stop>
        <div class="skill-header">
          <h3 class="skill-title">工具</h3>
          <button class="skill-close" @click="emit('close')">✕</button>
        </div>
        <div class="panel-tabs">
          <button :class="['panel-tab', { active: activeTab === 'skills' }]" @click="activeTab = 'skills'">技能</button>
          <button :class="['panel-tab', { active: activeTab === 'mcp' }]" @click="activeTab = 'mcp'">MCP</button>
        </div>

        <!-- Skills tab -->
        <div v-if="activeTab === 'skills'" class="skill-list">
          <div v-if="skills.length === 0" class="skill-empty">暂无技能</div>
          <div
            v-for="skill in skills"
            :key="skill.name"
            class="skill-item"
          >
            <div class="skill-item-main" @click="useSkill(skill.name)">
              <div class="skill-item-row">
                <span class="skill-item-name">{{ skill.name }}</span>
                <span v-if="skill.tier" class="skill-tier-badge" :class="skill.tier">{{ skill.tier }}</span>
              </div>
              <span v-if="skill.description" class="skill-item-desc">{{ skill.description }}</span>
            </div>
            <button class="skill-delete-btn" @click.stop="onDelete(skill.name, skill.tier)" title="删除技能">🗑</button>
          </div>
        </div>

        <!-- MCP tab -->
        <div v-if="activeTab === 'mcp'" class="skill-list">
          <div v-if="!mcpEnabled" class="skill-empty">
            <div>MCP 未启用</div>
            <div class="mcp-hint">在 config.yaml 中配置 mcp.enabled: true</div>
          </div>
          <template v-else>
            <div v-if="mcpConfigured.length === 0" class="skill-empty">
              <div>未配置 MCP 服务器</div>
              <div class="mcp-hint">在 config.yaml 中添加 servers</div>
            </div>
            <div v-for="srv in mcpConfigured" :key="srv.name" class="mcp-server">
              <div class="mcp-server-header">
                <span class="mcp-server-name">{{ srv.name }}</span>
                <span class="mcp-server-badge" :class="srv.type">{{ srv.type }}</span>
                <span v-if="srv.disabled" class="mcp-server-status disabled">已禁用</span>
                <span v-else-if="mcpConnected.find(c => c.name === srv.name)" class="mcp-server-status connected">已连接</span>
                <span v-else class="mcp-server-status disconnected">未连接</span>
              </div>
              <div v-if="mcpConnected.find(c => c.name === srv.name)" class="mcp-tools">
                <div v-for="tool in (mcpConnected.find(c => c.name === srv.name)?.tools || [])" :key="tool.name" class="mcp-tool">
                  <span class="mcp-tool-name">{{ tool.name }}</span>
                  <span v-if="tool.description" class="mcp-tool-desc">{{ tool.description }}</span>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.skill-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  z-index: 200;
  animation: fadeIn 0.2s ease;
}
.skill-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 340px;
  background: var(--bg);
  border-left: 0.5px solid var(--border);
  box-shadow: -4px 0 20px var(--shadow);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.25s ease;
}
@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.skill-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
}
.skill-title {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--fg);
}
.skill-close {
  width: 28px; height: 28px;
  border: none; background: none;
  color: var(--fg-dim); font-size: 1rem;
  cursor: pointer; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s ease;
}
.skill-close:hover { background: var(--bg-card); color: var(--fg); }
.panel-tabs {
  display: flex;
  gap: 2px;
  padding: 0.5rem 1.2rem;
  border-bottom: 0.5px solid var(--border);
  background: var(--bg);
}
.panel-tab {
  flex: 1;
  padding: 0.35rem 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--fg-dim);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.panel-tab.active {
  background: var(--accent);
  color: #fff;
  font-weight: 500;
}
.panel-tab:not(.active):hover { background: var(--bg-thinking); }
.skill-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}
.skill-empty {
  padding: 2rem;
  text-align: center;
  color: var(--fg-dim);
  font-size: 0.9rem;
}
.mcp-hint {
  font-size: 0.78rem;
  color: var(--fg-dim);
  margin-top: 0.4rem;
  opacity: 0.7;
}
.skill-item {
  display: flex;
  align-items: center;
  padding: 0.7rem 1.2rem;
  border-bottom: 0.5px solid var(--border-light);
  transition: background 0.15s ease;
}
.skill-item:hover { background: var(--bg-thinking); }
.skill-item-main {
  flex: 1;
  cursor: pointer;
  min-width: 0;
}
.skill-item-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.skill-item-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--fg);
}
.skill-tier-badge {
  font-size: 0.68rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
}
.skill-tier-badge.global {
  background: #6b728018;
  color: #6b7280;
  border: 1px solid #6b728030;
}
.skill-tier-badge.user {
  background: #3b82f618;
  color: #3b82f6;
  border: 1px solid #3b82f630;
}
.skill-tier-badge.workspace {
  background: #10a37f18;
  color: #10a37f;
  border: 1px solid #10a37f30;
}
.skill-item-desc {
  font-size: 0.8rem;
  color: var(--fg-dim);
  display: block;
  margin-top: 0.2rem;
}
.skill-delete-btn {
  flex-shrink: 0;
  width: 28px; height: 28px;
  border: none; background: none;
  color: var(--fg-dim);
  font-size: 0.85rem;
  cursor: pointer;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s ease;
  margin-left: 0.3rem;
  opacity: 0;
}
.skill-item:hover .skill-delete-btn { opacity: 1; }
.skill-delete-btn:hover { background: #ef444420; color: #ef4444; }
.mcp-server {
  padding: 0.7rem 1.2rem;
  border-bottom: 0.5px solid var(--border-light);
}
.mcp-server-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.mcp-server-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--fg);
}
.mcp-server-badge {
  font-size: 0.68rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
}
.mcp-server-badge.stdio {
  background: #10a37f18;
  color: #10a37f;
  border: 1px solid #10a37f30;
}
.mcp-server-badge.streamable_http {
  background: #6366f118;
  color: #6366f1;
  border: 1px solid #6366f130;
}
.mcp-server-status {
  font-size: 0.7rem;
  margin-left: auto;
}
.mcp-server-status.connected { color: #4CAF50; }
.mcp-server-status.disconnected { color: var(--fg-dim); }
.mcp-server-status.disabled { color: #e55; }
.mcp-tools {
  margin-top: 0.4rem;
  padding-left: 0.8rem;
  border-left: 2px solid var(--border);
}
.mcp-tool {
  padding: 0.25rem 0;
}
.mcp-tool-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  color: var(--fg);
}
.mcp-tool-desc {
  font-size: 0.72rem;
  color: var(--fg-dim);
  display: block;
  margin-top: 0.1rem;
}
</style>
