<template>
  <section class="auth-landing">
    <div class="auth-hero">
      <div class="auth-brand-row">
        <div class="auth-logo">
          <i class="fas fa-wave-square"></i>
        </div>
        <div>
          <strong>MedRetrieve V2</strong>
          <span>Medical RAG Workbench</span>
        </div>
      </div>

      <h1>面向医疗知识检索的 RAG 工作台</h1>
      <p>
        登录后可进行医学问答、查看 RAG 检索路径、管理会话历史；
        管理员可维护文档知识库与向量检索数据。
      </p>

      <div class="auth-feature-grid">
        <span><i class="fas fa-database"></i> Milvus 混合检索</span>
        <span><i class="fas fa-share-nodes"></i> Neo4j KG 降级可用</span>
        <span><i class="fas fa-shield-heart"></i> 医疗安全边界</span>
        <span><i class="fas fa-clock-rotate-left"></i> 会话隔离与记忆</span>
      </div>
    </div>

    <div class="auth-panel">
      <span class="auth-kicker">{{ authStore.authMode === 'login' ? 'SIGN IN' : 'CREATE ACCOUNT' }}</span>
      <h2>{{ authStore.authMode === 'login' ? '登录工作台' : '注册账号' }}</h2>
      <p>
        {{ authStore.authMode === 'login'
          ? '使用账号进入医疗问答与知识库管理界面。'
          : '创建普通用户账号；如需管理员权限，请填写邀请码。' }}
      </p>

      <div class="auth-form">
        <label>
          <span>用户名</span>
          <input
            v-model="authStore.authForm.username"
            type="text"
            autocomplete="username"
            placeholder="请输入用户名"
            @keyup.enter="onSubmit"
          />
        </label>

        <label>
          <span>密码</span>
          <input
            v-model="authStore.authForm.password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            @keyup.enter="onSubmit"
          />
        </label>

        <label v-if="authStore.authMode === 'register'">
          <span>账号角色</span>
          <select v-model="authStore.authForm.role">
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </label>

        <label v-if="authStore.authMode === 'register' && authStore.authForm.role === 'admin'">
          <span>管理员邀请码</span>
          <input
            v-model="authStore.authForm.admin_code"
            type="password"
            placeholder="请输入管理员邀请码"
            @keyup.enter="onSubmit"
          />
        </label>

        <button class="auth-submit" :disabled="authStore.authLoading" @click="onSubmit">
          <i :class="authStore.authLoading ? 'fas fa-spinner fa-spin' : 'fas fa-arrow-right-to-bracket'"></i>
          <span>{{ authStore.authLoading ? '提交中...' : (authStore.authMode === 'login' ? '登录' : '注册') }}</span>
        </button>

        <button class="auth-switch" @click="toggleAuthMode">
          {{ authStore.authMode === 'login' ? '没有账号？去注册' : '已有账号？去登录' }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useAuthStore } from '@/stores/auth';

const authStore = useAuthStore();

const toggleAuthMode = () => {
  authStore.authMode = authStore.authMode === 'login' ? 'register' : 'login';
};

const onSubmit = async () => {
  try {
    await authStore.handleAuthSubmit();
  } catch (error: any) {
    alert(error.message);
  }
};
</script>
