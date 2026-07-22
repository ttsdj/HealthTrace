<template>
  <AuthPanel v-if="!authStore.isAuthenticated" />

  <div
    v-else
    :class="[
      'app-wrapper',
      'authenticated',
      {
        'left-collapsed': leftCollapsed,
        'right-collapsed': rightCollapsed,
      },
    ]"
  >
    <WorkspaceHeader />

    <div class="workspace-shell" :class="{ 'health-record-mode': ['healthRecord', 'observability'].includes(chatStore.activeNav) }">
      <Sidebar v-if="!leftCollapsed" />

      <button
        class="panel-toggle panel-toggle-left"
        :title="leftCollapsed ? '展开会话栏' : '收起会话栏'"
        @click="leftCollapsed = !leftCollapsed"
      >
        <i :class="leftCollapsed ? 'fas fa-chevron-right' : 'fas fa-chevron-left'"></i>
      </button>

      <main class="main-content">
        <DocumentSettings v-if="chatStore.activeNav === 'settings'" />
        <HealthRecordWorkspace v-else-if="chatStore.activeNav === 'healthRecord'" />
        <ObservabilityWorkspace v-else-if="chatStore.activeNav === 'observability'" />
        <template v-else>
          <HistorySidebar />
          <ChatArea />
        </template>
      </main>

      <button
        v-if="!['settings', 'healthRecord', 'observability'].includes(chatStore.activeNav)"
        class="panel-toggle panel-toggle-right"
        :title="rightCollapsed ? '展开检索面板' : '收起检索面板'"
        @click="rightCollapsed = !rightCollapsed"
      >
        <i :class="rightCollapsed ? 'fas fa-chevron-left' : 'fas fa-chevron-right'"></i>
      </button>

      <RetrievalPanel v-if="!['settings', 'healthRecord', 'observability'].includes(chatStore.activeNav) && !rightCollapsed" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import Sidebar from '@/components/Sidebar.vue';
import AuthPanel from '@/components/AuthPanelClinical.vue';
import HistorySidebar from '@/components/HistorySidebar.vue';
import WorkspaceHeader from '@/components/WorkspaceHeader.vue';
import RetrievalPanel from '@/components/RetrievalPanelClinical.vue';
import ChatArea from '@/components/Chat/ChatArea.vue';
import DocumentSettings from '@/components/Documents/DocumentSettings.vue';
import HealthRecordWorkspace from '@/components/HealthRecord/HealthRecordWorkspace.vue';
import ObservabilityWorkspace from '@/components/Observability/ObservabilityWorkspace.vue';

import { useAuthStore } from '@/stores/auth';
import { useChatStore } from '@/stores/chat';

const authStore = useAuthStore();
const chatStore = useChatStore();
const leftCollapsed = ref(false);
const rightCollapsed = ref(false);

const handleUnauthorized = () => {
  authStore.handleLogout();
  alert('登录已过期，请重新登录');
};

onMounted(async () => {
  window.addEventListener('unauthorized', handleUnauthorized);

  if (authStore.token) {
    try {
      await authStore.fetchMe();
    } catch (_) {
      authStore.handleLogout();
    }
  }
});

onUnmounted(() => {
  window.removeEventListener('unauthorized', handleUnauthorized);
});
</script>
