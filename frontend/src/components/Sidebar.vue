<template>
  <aside class="sidebar">
    <button class="new-chat-primary" @click="onNewChat">
      <i class="fas fa-plus"></i>
      <span>新建对话</span>
    </button>

    <div class="conversation-section">
      <div class="section-label">对话历史</div>
      <div class="conversation-search">
        <i class="fas fa-magnifying-glass"></i>
        <input v-model="searchQuery" type="search" placeholder="搜索对话..." />
      </div>

      <div class="conversation-list">
        <div v-if="filteredSessions.length === 0" class="conversation-empty">
          暂无历史会话
        </div>
        <button
          v-for="session in filteredSessions"
          :key="session.session_id"
          class="conversation-item"
          :class="{ active: session.session_id === chatStore.sessionId }"
          @click="loadSession(session.session_id)"
        >
          <span class="conversation-title">{{ session.title || '未命名会话' }}</span>
          <span class="conversation-time">{{ formatSessionTime(session.updated_at) }}</span>
          <i class="fas fa-ellipsis-vertical conversation-more"></i>
        </button>
      </div>
    </div>

    <div class="sidebar-footer">
      <button @click="chatStore.handleClearChat" class="sidebar-action">
        <i class="far fa-trash-can"></i>
        <span>清空当前对话</span>
      </button>
      <button v-if="authStore.isAuthenticated" @click="authStore.handleLogout" class="sidebar-action">
        <i class="fas fa-arrow-right-from-bracket"></i>
        <span>退出登录</span>
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useAuthStore } from '@/stores/auth';
import { useChatStore } from '@/stores/chat';
import { useSessionStore } from '@/stores/sessions';
import { formatSessionTime } from '@/utils/time';

const authStore = useAuthStore();
const chatStore = useChatStore();
const sessionStore = useSessionStore();
const searchQuery = ref('');

const filteredSessions = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return sessionStore.sessions;
  return sessionStore.sessions.filter((session) =>
    (session.title || session.session_id).toLowerCase().includes(query)
  );
});

const onNewChat = () => {
  chatStore.handleNewChat();
};

const loadSession = async (sessionId: string) => {
  try {
    await chatStore.loadSession(sessionId);
  } catch (error: any) {
    alert(`加载会话失败：${error.message}`);
  }
};

onMounted(async () => {
  if (!authStore.isAuthenticated) return;
  try {
    await sessionStore.fetchSessions();
  } catch (error: any) {
    console.error('加载会话历史失败:', error.message);
  }
});
</script>
