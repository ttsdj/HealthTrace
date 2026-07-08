<template>
  <div class="welcome-screen">
    <div class="welcome-symbol"><i class="fas fa-wave-square"></i></div>
    <span class="welcome-kicker">MEDICAL RETRIEVAL WORKSPACE</span>
    <h1>从可信证据开始问诊</h1>
    <p>系统会联合检索医学问答、知识图谱与会话记忆，并在右侧展示完整检索过程。</p>
    <div class="prompt-grid">
      <button v-for="prompt in prompts" :key="prompt.title" @click="selectPrompt(prompt.query)">
        <i :class="prompt.icon"></i>
        <span>
          <strong>{{ prompt.title }}</strong>
          <small>{{ prompt.description }}</small>
        </span>
        <i class="fas fa-arrow-right"></i>
      </button>
    </div>
    <div class="welcome-boundary">
      <i class="fas fa-shield-heart"></i>
      <span>系统用于医学知识检索与辅助解释，不替代临床诊断和处方。</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chat';

const chatStore = useChatStore();
const prompts = [
  {
    title: '症状关联',
    description: '结合向量与图谱检索可能相关疾病',
    query: '持续咳嗽可能与哪些疾病有关？',
    icon: 'fas fa-lungs',
  },
  {
    title: '用药知识',
    description: '检索药品用途、限制条件和相关证据',
    query: '高血压患者用药时通常需要注意哪些问题？',
    icon: 'fas fa-capsules',
  },
  {
    title: '检查与科室',
    description: '查询疾病相关检查和就诊科室',
    query: '反复胃痛通常需要做哪些检查，应该挂什么科？',
    icon: 'fas fa-flask-vial',
  },
];

const selectPrompt = (query: string) => {
  chatStore.userInput = query;
};
</script>
