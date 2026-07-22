import { defineStore } from 'pinia';
import api from '@/utils/api';
import type {
  FactCandidate,
  HealthGoal,
  HealthNotification,
  HealthTask,
  HealthTaskRun,
  PatientDocument,
  PatientFact,
  TimelineEvent,
} from '@/types/healthRecord';

const messageFromError = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback;

export const useHealthRecordStore = defineStore('healthRecords', {
  state: () => ({
    documents: [] as PatientDocument[],
    candidates: [] as FactCandidate[],
    facts: [] as PatientFact[],
    timeline: [] as TimelineEvent[],
    tasks: [] as HealthTask[],
    taskRuns: [] as HealthTaskRun[],
    goals: [] as HealthGoal[],
    notifications: [] as HealthNotification[],
    loading: false,
    uploading: false,
    activeOperations: {} as Record<string, boolean>,
    lastError: '',
  }),

  getters: {
    pendingCandidates: (state) => state.candidates.filter((item) => item.status === 'pending'),
    activeTasks: (state) => state.tasks.filter((item) => !['cancelled', 'completed', 'failed'].includes(item.status)),
    unreadNotifications: (state) => state.notifications.filter((item) => item.status === 'unread'),
  },

  actions: {
    async loadAll() {
      this.loading = true;
      this.lastError = '';
      try {
        const [documents, candidates, facts, timeline, tasks, taskRuns, goals, notifications] = await Promise.all([
          api.get('/patient/documents'),
          api.get('/patient/fact-candidates'),
          api.get('/patient/facts'),
          api.get('/patient/timeline'),
          api.get('/patient/tasks'),
          api.get('/patient/tasks/runs'),
          api.get('/patient/goals'),
          api.get('/patient/notifications'),
        ]);
        this.documents = documents.data.documents || [];
        this.candidates = candidates.data.candidates || [];
        this.facts = facts.data.facts || [];
        this.timeline = timeline.data.events || [];
        this.tasks = tasks.data.tasks || [];
        this.taskRuns = taskRuns.data.runs || [];
        this.goals = goals.data.goals || [];
        this.notifications = notifications.data.notifications || [];
      } catch (error: any) {
        this.lastError = messageFromError(error, '加载健康档案失败');
        throw new Error(this.lastError);
      } finally {
        this.loading = false;
      }
    },

    async uploadDocument(file: File) {
      this.uploading = true;
      this.lastError = '';
      try {
        const form = new FormData();
        form.append('file', file);
        const response = await api.post('/patient/documents/upload', form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 600000,
        });
        await this.extractCandidates(response.data.document_id, false, false);
        return response.data;
      } catch (error: any) {
        this.lastError = messageFromError(error, '患者文档上传失败');
        throw new Error(this.lastError);
      } finally {
        this.uploading = false;
      }
    },

    async deleteDocument(documentId: string) {
      this.activeOperations = { ...this.activeOperations, [`document:${documentId}`]: true };
      try {
        await api.delete(`/patient/documents/${encodeURIComponent(documentId)}`, { timeout: 300000 });
        await this.loadAll();
      } finally {
        delete this.activeOperations[`document:${documentId}`];
      }
    },

    async extractCandidates(documentId: string, useLlm: boolean, consent: boolean) {
      const key = `extract:${documentId}`;
      this.activeOperations = { ...this.activeOperations, [key]: true };
      try {
        const response = await api.post(
          `/patient/documents/${encodeURIComponent(documentId)}/fact-candidates/extract`,
          { use_llm: useLlm, consent_external_processing: consent },
          { timeout: 300000 },
        );
        const [documents, candidates] = await Promise.all([
          api.get('/patient/documents'),
          api.get('/patient/fact-candidates'),
        ]);
        this.documents = documents.data.documents || [];
        this.candidates = candidates.data.candidates || [];
        return response.data;
      } catch (error: any) {
        throw new Error(messageFromError(error, '事实候选提取失败'));
      } finally {
        delete this.activeOperations[key];
      }
    },

    async confirmCandidate(candidateId: string, payload: Record<string, unknown>) {
      const key = `candidate:${candidateId}`;
      this.activeOperations = { ...this.activeOperations, [key]: true };
      try {
        await api.post(`/patient/fact-candidates/${encodeURIComponent(candidateId)}/confirm`, payload);
        await this.loadAll();
      } catch (error: any) {
        throw new Error(messageFromError(error, '确认事实失败'));
      } finally {
        delete this.activeOperations[key];
      }
    },

    async rejectCandidate(candidateId: string) {
      await api.post(`/patient/fact-candidates/${encodeURIComponent(candidateId)}/reject`);
      await this.loadAll();
    },

    async retractFact(factId: string) {
      await api.delete(`/patient/facts/${encodeURIComponent(factId)}`);
      await this.loadAll();
    },

    async createTask(payload: Record<string, unknown>) {
      await api.post('/patient/tasks', payload);
      await this.loadAll();
    },

    async confirmTask(taskId: string) {
      await api.post(`/patient/tasks/${encodeURIComponent(taskId)}/confirm`);
      await this.loadAll();
    },

    async cancelTask(taskId: string) {
      await api.post(`/patient/tasks/${encodeURIComponent(taskId)}/cancel`);
      await this.loadAll();
    },

    async createGoal(payload: Record<string, unknown>) {
      await api.post('/patient/goals', payload);
      await this.loadAll();
    },

    async archiveGoal(goalId: string) {
      await api.post(`/patient/goals/${encodeURIComponent(goalId)}/archive`);
      await this.loadAll();
    },

    async markNotificationRead(notificationId: string) {
      await api.post(`/patient/notifications/${encodeURIComponent(notificationId)}/read`);
      await this.loadAll();
    },

    async retryTaskRun(runId: string) {
      await api.post(`/patient/tasks/runs/${encodeURIComponent(runId)}/retry`);
      await this.loadAll();
    },

    async submitTaskRunInput(runId: string, values: Record<string, unknown>) {
      await api.post(`/patient/tasks/runs/${encodeURIComponent(runId)}/input`, { values });
      await this.loadAll();
    },
  },
});
