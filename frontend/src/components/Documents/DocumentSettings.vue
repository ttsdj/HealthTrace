<template>
  <div class="settings-panel">
    <div class="settings-header">
      <h2><i class="fas fa-database"></i> 医疗知识库管理</h2>
      <p>上传医学文档并写入 med_medical_qa_v2；情景记忆和语义记忆由对话流程自动维护。</p>
    </div>

    <!-- Upload Section -->
    <UploadSection />

    <!-- Documents List Section -->
    <div class="documents-section">
      <h3><i class="fas fa-list"></i> medical_qa 文档库</h3>
      <button @click="onRefresh" class="btn-secondary" :disabled="documentStore.documentsLoading">
        <i class="fas fa-sync" :class="{ 'fa-spin': documentStore.documentsLoading }"></i> 刷新列表
      </button>
      
      <div v-if="documentStore.documentsLoading" class="loading-indicator">
        加载中...
      </div>
      
      <div v-else-if="documentStore.documents.length === 0" class="empty-documents">
        <i class="fas fa-inbox"></i>
        <p>暂无文档</p>
      </div>
      
      <div v-else class="documents-list">
        <DocumentItem 
          v-for="doc in documentStore.documents" 
          :key="doc.filename" 
          :doc="doc" 
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import UploadSection from './UploadSection.vue';
import DocumentItem from './DocumentItem.vue';
import { useDocumentStore } from '@/stores/documents';

const documentStore = useDocumentStore();

const onRefresh = async () => {
  try {
    await documentStore.loadDocuments();
  } catch (error: any) {
    alert(error.message);
  }
};

onMounted(() => {
  documentStore.loadDocuments();
});

onUnmounted(() => {
  // Clean up any active pollers on settings close to avoid memory leaks
  documentStore.stopUploadJobPolling();
  documentStore.stopAllDeleteJobPolling();
});
</script>
