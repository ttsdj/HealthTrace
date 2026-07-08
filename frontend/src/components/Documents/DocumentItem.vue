<template>
  <div 
    class="document-item" 
    :class="{ deleting: documentStore.deleteJobs[doc.filename]?.status === 'running' }"
  >
    <div class="document-main">
      <div class="document-row">
        <div class="document-info">
          <div class="document-icon">
            <i :class="getFileIcon(doc.file_type)"></i>
          </div>
          <div class="document-details">
            <div class="document-name">{{ doc.filename }}</div>
            <div class="document-meta">
              <span>{{ doc.file_type }}</span>
              <span>{{ doc.chunk_count }} 个文本片段</span>
            </div>
          </div>
        </div>
        <button 
          @click="onDelete" 
          class="btn-danger"
          title="删除文档"
          :disabled="documentStore.isDeleteActionLocked(doc.filename)"
        >
          <i :class="documentStore.getDeleteButtonIcon(doc.filename)"></i>
        </button>
      </div>

      <!-- Deletion Progress Tracking -->
      <div
        v-if="documentStore.deleteJobs[doc.filename]"
        class="upload-progress delete-progress"
        :class="{ collapsed: documentStore.deleteJobs[doc.filename].collapsed }"
      >
        <button type="button" class="upload-progress-header" @click="onToggleCollapse">
          <span class="upload-message">{{ documentStore.deleteJobs[doc.filename].message || '删除进度' }}</span>
          <span class="upload-toggle">{{ documentStore.deleteJobs[doc.filename].collapsed ? '展开' : '收起' }}</span>
        </button>
        
        <div v-show="!documentStore.deleteJobs[doc.filename].collapsed" class="upload-step-list">
          <div
            v-for="step in documentStore.deleteJobs[doc.filename].steps"
            :key="step.key"
            class="upload-step"
            :class="`upload-step-${step.status}`"
          >
            <div class="upload-step-header">
              <span class="upload-step-label">{{ step.label }}</span>
              <span class="upload-step-percent">{{ step.percent }}%</span>
            </div>
            <div class="upload-step-bar">
              <div class="upload-step-fill" :style="{ width: step.percent + '%' }"></div>
            </div>
            <div v-if="step.message" class="upload-step-message">{{ step.message }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useDocumentStore } from '@/stores/documents';
import type { DocumentItem } from '@/types/document';

const props = defineProps<{
  doc: DocumentItem;
}>();

const documentStore = useDocumentStore();

const getFileIcon = (fileType: string) => {
  if (fileType === 'PDF') {
    return 'fas fa-file-pdf';
  } else if (fileType === 'Word') {
    return 'fas fa-file-word';
  } else if (fileType === 'Excel') {
    return 'fas fa-file-excel';
  }
  return 'fas fa-file';
};

const onDelete = async () => {
  try {
    await documentStore.deleteDocument(props.doc.filename);
  } catch (error: any) {
    alert(error.message);
  }
};

const onToggleCollapse = () => {
  documentStore.toggleDeleteJobCollapsed(props.doc.filename);
};
</script>
