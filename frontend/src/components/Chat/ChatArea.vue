<template>
  <div class="chat-area">
    <header class="chat-header">
      <div class="chat-context">
        <span class="chat-context-icon"><i class="fas fa-stethoscope"></i></span>
        <div>
          <strong>医疗问答工作区</strong>
          <small>基于向量检索、知识图谱与会话记忆生成回答</small>
        </div>
      </div>
      <div class="model-badge">
        <span class="status-dot"></span>
        <span>Agent RAG</span>
      </div>
    </header>

    <div class="chat-container" ref="chatContainerRef">
      <WelcomeScreen v-if="chatStore.messages.length === 0" />
      
      <!-- Messages List -->
      <MessageItem 
        v-for="(msg, index) in chatStore.messages" 
        :key="index" 
        :msg="msg" 
        :msg-index="index" 
        :ref="(el) => { if (el) messageItemRefs[index] = el; }"
        @cite-click="scrollToChunk"
      />
    </div>

    <!-- Bottom Input Area -->
    <ChatInput />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onBeforeUpdate, onMounted } from 'vue';
import WelcomeScreen from './WelcomeScreen.vue';
import MessageItem from './MessageItem.vue';
import ChatInput from './ChatInput.vue';
import { useChatStore } from '@/stores/chat';

const chatStore = useChatStore();
const chatContainerRef = ref<HTMLDivElement | null>(null);
const messageItemRefs = ref<any[]>([]);

onBeforeUpdate(() => {
  messageItemRefs.value = [];
});

const scrollToBottom = () => {
  if (chatContainerRef.value) {
    chatContainerRef.value.scrollTop = chatContainerRef.value.scrollHeight;
  }
};

const scrollToChunk = async (msgIndex: number, chunkIndex: number) => {
  const msgItem = messageItemRefs.value[msgIndex];
  if (!msgItem) return;

  // Expand References section
  msgItem.openReferences();

  await nextTick();
  const chunkEl = document.getElementById(`chunk-${msgIndex}-${chunkIndex}`);
  if (chunkEl) {
    chunkEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    chunkEl.classList.add('highlight-chunk');
    setTimeout(() => {
      chunkEl.classList.remove('highlight-chunk');
    }, 2000);
  }
};

// Scroll to bottom when messages list changes (e.g. streaming responses)
watch(
  () => chatStore.messages,
  () => {
    nextTick(() => {
      scrollToBottom();
    });
  },
  { deep: true }
);

onMounted(() => {
  scrollToBottom();
});
</script>
