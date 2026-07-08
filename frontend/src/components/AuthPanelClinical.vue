<template>
  <section class="auth-landing clinical-auth">
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
        登录后可进行医学问答、查看 RAG 检索路径、管理会话历史；管理员可维护文档知识库与向量检索数据。
      </p>

      <div class="auth-feature-grid">
        <span><i class="fas fa-database"></i> Milvus 混合检索</span>
        <span><i class="fas fa-share-nodes"></i> Neo4j KG 降级可用</span>
        <span><i class="fas fa-shield-heart"></i> 医疗安全边界</span>
        <span><i class="fas fa-clock-rotate-left"></i> 会话隔离与记忆</span>
      </div>
    </div>

    <div class="auth-panel">
      <span class="auth-kicker">{{ isLogin ? 'SIGN IN' : 'CREATE ACCOUNT' }}</span>
      <h2>{{ isLogin ? '登录工作台' : '注册账号' }}</h2>
      <p>
        {{ isLogin
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

        <label v-if="!isLogin">
          <span>账号角色</span>
          <select v-model="authStore.authForm.role">
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </label>

        <label v-if="!isLogin && authStore.authForm.role === 'admin'">
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
          <span>{{ authStore.authLoading ? '提交中...' : (isLogin ? '登录' : '注册') }}</span>
        </button>

        <button class="auth-switch" @click="toggleAuthMode">
          {{ isLogin ? '没有账号？去注册' : '已有账号？去登录' }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useAuthStore } from '@/stores/auth';

const authStore = useAuthStore();
const isLogin = computed(() => authStore.authMode === 'login');

const toggleAuthMode = () => {
  authStore.authMode = isLogin.value ? 'register' : 'login';
};

const onSubmit = async () => {
  try {
    await authStore.handleAuthSubmit();
  } catch (error: any) {
    alert(error?.message || '认证失败，请稍后重试');
  }
};
</script>
