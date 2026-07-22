<template>
  <header class="workspace-header">
    <div class="brand-lockup">
      <div class="brand-mark" aria-hidden="true">
        <i class="fas fa-wave-square"></i>
      </div>
      <div>
        <div class="brand-name">HealthTrace <span>Beta</span></div>
        <div class="brand-subtitle">个人健康档案与智能咨询</div>
      </div>
    </div>

    <div class="workspace-actions">
      <button class="toolbar-btn" aria-label="新建对话" @click="newChat">
        <i class="far fa-message"></i>
        <span>新建对话</span>
      </button>
      <button class="toolbar-btn" aria-label="健康档案" :class="{ active: chatStore.activeNav === 'healthRecord' }" @click="openHealthRecord">
        <i class="fas fa-notes-medical"></i>
        <span>健康档案</span>
      </button>
      <button v-if="authStore.isAdmin" class="toolbar-btn" aria-label="知识库管理" @click="openKnowledgeBase">
        <i class="fas fa-database"></i>
        <span>知识库管理</span>
      </button>
      <button v-if="authStore.isAdmin" class="toolbar-btn" aria-label="导入文档" @click="openKnowledgeBase">
        <i class="fas fa-arrow-up-from-bracket"></i>
        <span>导入文档</span>
      </button>
      <button v-if="authStore.isAdmin" class="toolbar-btn" aria-label="运行监控" :class="{ active: chatStore.activeNav === 'observability' }" @click="openObservability">
        <i class="fas fa-chart-line"></i>
        <span>运行监控</span>
      </button>
    </div>

    <div class="workspace-status">
      <div
        class="api-state"
        :class="{ offline: healthState === 'offline', degraded: healthState === 'degraded' }"
        :title="healthTitle"
      >
        <span class="status-dot"></span>
        <span>{{ healthLabel }}</span>
      </div>
      <div class="engine-state" title="当前检索引擎">
        <i class="fas fa-code-branch"></i>
        <span>KG + Hybrid RAG</span>
      </div>
      <div class="header-user">
        <div class="user-avatar"><i class="far fa-user"></i></div>
        <div>
          <strong>{{ authStore.currentUser?.username || '用户' }}</strong>
          <small>{{ authStore.currentUser?.role || 'user' }}</small>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useAuthStore } from '@/stores/auth';
import { useChatStore } from '@/stores/chat';
import { useSessionStore } from '@/stores/sessions';

const authStore = useAuthStore();
const chatStore = useChatStore();
const sessionStore = useSessionStore();
const healthState = ref<'checking' | 'online' | 'degraded' | 'offline'>('checking');
const optionalIssues = ref<string[]>([]);
const coreIssues = ref<string[]>([]);

const healthLabel = computed(() => {
  if (healthState.value === 'online' && optionalIssues.value.length) return '核心正常 / KG 降级';
  if (healthState.value === 'online') return '服务正常';
  if (healthState.value === 'degraded') return '核心服务异常';
  if (healthState.value === 'offline') return 'API 未连接';
  return '正在检查';
});

const healthTitle = computed(() => {
  const parts = [];
  if (coreIssues.value.length) parts.push(`核心异常：${coreIssues.value.join('、')}`);
  if (optionalIssues.value.length) parts.push(`增强服务异常：${optionalIssues.value.join('、')}`);
  return parts.join('；') || '核心服务已连接';
});

const checkHealth = async () => {
  try {
    const response = await fetch('/health');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = await response.json();
    const core = health.core_services || health.services || {};
    const optional = health.optional_services || {};
    coreIssues.value = Object.entries(core)
      .filter(([, value]: any) => !value?.ok)
      .map(([key]) => key);
    optionalIssues.value = Object.entries(optional)
      .filter(([, value]: any) => !value?.ok)
      .map(([key]) => key === 'neo4j' ? 'Neo4j KG 未连接' : key);
    healthState.value = health.ready ? 'online' : 'degraded';
  } catch (_) {
    healthState.value = 'offline';
    coreIssues.value = ['FastAPI'];
    optionalIssues.value = [];
  }
};

const newChat = () => {
  chatStore.handleNewChat();
};

const openKnowledgeBase = () => {
  chatStore.activeNav = 'settings';
  sessionStore.showHistorySidebar = false;
};

const openHealthRecord = () => {
  chatStore.activeNav = 'healthRecord';
  sessionStore.showHistorySidebar = false;
};

const openObservability = () => {
  chatStore.activeNav = 'observability';
  sessionStore.showHistorySidebar = false;
};

onMounted(checkHealth);
</script>
