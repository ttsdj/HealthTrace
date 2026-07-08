<template>
  <div class="input-area-wrapper">
    <div class="input-area">
      <button class="attach-btn" title="打开文档导入" @click="openDocumentImport">
        <i class="fas fa-paperclip"></i>
      </button>

      <button
        class="attach-btn location-btn"
        :class="{ active: chatStore.locationStatus === 'ready' }"
        :disabled="chatStore.locationStatus === 'requesting'"
        :title="locationButtonTitle"
        @click="toggleLocation"
      >
        <i
          :class="chatStore.locationStatus === 'requesting'
            ? 'fas fa-spinner fa-spin'
            : 'fas fa-location-crosshairs'"
        ></i>
      </button>
      
      <textarea 
        v-model="chatStore.userInput" 
        @keydown="handleKeyDown"
        @compositionstart="handleCompositionStart"
        @compositionend="handleCompositionEnd"
        @input="autoResize"
        placeholder="请输入医疗问题、症状描述或文档检索需求... (Shift+Enter 换行)"
        rows="1"
        ref="textareaRef"
      ></textarea>
      
      <button 
        v-if="chatStore.isLoading" 
        @click="chatStore.handleStop" 
        class="send-btn stop-btn" 
        title="终止回答"
      >
        <i class="fas fa-stop"></i>
      </button>
      
      <button 
        v-else 
        @click="onSend" 
        class="send-btn" 
        title="发送"
      >
        <i class="fas fa-paper-plane"></i>
      </button>
    </div>
    <div class="input-meta-row">
      <span class="retrieval-mode-pill">
        <i class="fas fa-diagram-project"></i>
        混合检索 RAG
      </span>
      <span v-if="chatStore.locationStatus === 'ready'" class="location-consent-pill">
        <i class="fas fa-location-dot"></i>
        定位已授权，仅用于下一条消息
      </span>
      <span class="footer-text">内容由 AI 生成，仅作医学知识参考</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, computed } from 'vue';
import { useAuthStore } from '@/stores/auth';
import { useChatStore } from '@/stores/chat';
import { useSessionStore } from '@/stores/sessions';

const chatStore = useChatStore();
const authStore = useAuthStore();
const sessionStore = useSessionStore();
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const isComposing = ref(false);
const locationButtonTitle = computed(() => {
  if (chatStore.locationStatus === 'requesting') return '正在获取位置';
  if (chatStore.locationStatus === 'ready') return '撤回本次定位授权';
  return '授权下一条消息搜索附近医院';
});

const handleCompositionStart = () => {
  isComposing.value = true;
};

const handleCompositionEnd = () => {
  isComposing.value = false;
};

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && !event.shiftKey && !isComposing.value) {
    event.preventDefault();
    onSend();
  }
};

const autoResize = () => {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto';
    textareaRef.value.style.height = textareaRef.value.scrollHeight + 'px';
  }
};

const resetTextareaHeight = () => {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto';
  }
};

const openDocumentImport = () => {
  if (!authStore.isAdmin) {
    alert('仅管理员可导入知识库文档');
    return;
  }
  chatStore.activeNav = 'settings';
  sessionStore.showHistorySidebar = false;
};

const toggleLocation = async () => {
  try {
    await chatStore.requestLocation();
  } catch (error: any) {
    alert(error.message || '定位失败');
  }
};

const onSend = async () => {
  const text = chatStore.userInput.trim();
  if (!text || chatStore.isLoading || isComposing.value) return;

  await chatStore.handleSend();
  
  await nextTick();
  resetTextareaHeight();
};
</script>
