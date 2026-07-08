<template>
  <div class="upload-section">
    <h3><i class="fas fa-upload"></i> 上传文档</h3>
    <div class="upload-area">
      <input 
        type="file" 
        ref="fileInputRef" 
        @change="onFileSelect"
        accept=".pdf,.doc,.docx,.xls,.xlsx,.html,.htm,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff"
        style="display: none"
      />
      <button @click="triggerFileSelect" class="upload-btn">
        <i class="fas fa-cloud-upload-alt"></i> 选择文件
      </button>
      
      <div v-if="documentStore.selectedFile" class="selected-file">
        <i class="fas fa-file"></i> {{ documentStore.selectedFile.name }}
        <button @click="onUpload" class="btn-primary" :disabled="documentStore.isUploading">
          <i class="fas fa-upload"></i> {{ documentStore.isUploading ? '上传中...' : '开始上传' }}
        </button>
      </div>

      <div v-if="documentStore.uploadSteps.length" class="upload-progress" :class="{ collapsed: documentStore.uploadProgressCollapsed }">
        <button type="button" class="upload-progress-header" @click="onToggleCollapse">
          <span v-if="documentStore.uploadProgress" class="upload-message">{{ documentStore.uploadProgress }}</span>
          <span v-else class="upload-message">上传进度</span>
          <span class="upload-toggle">{{ documentStore.uploadProgressCollapsed ? '展开' : '收起' }}</span>
        </button>
        
        <div v-show="!documentStore.uploadProgressCollapsed" class="upload-step-list">
          <div
            v-for="step in documentStore.uploadSteps"
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
import { ref } from 'vue';
import { useDocumentStore } from '@/stores/documents';

const documentStore = useDocumentStore();
const fileInputRef = ref<HTMLInputElement | null>(null);

const triggerFileSelect = () => {
  fileInputRef.value?.click();
};

const onFileSelect = (event: Event) => {
  const files = (event.target as HTMLInputElement).files;
  if (files && files.length > 0) {
    documentStore.selectedFile = files[0];
    documentStore.uploadProgress = '';
    documentStore.uploadSteps = documentStore.createUploadSteps();
    documentStore.uploadProgressCollapsed = false;
    documentStore.activeUploadJobId = '';
  }
};

const onUpload = async () => {
  try {
    await documentStore.uploadDocument();
  } catch (error: any) {
    alert('上传文档失败: ' + error.message);
  }
};

const onToggleCollapse = () => {
  documentStore.uploadProgressCollapsed = !documentStore.uploadProgressCollapsed;
};
</script>
