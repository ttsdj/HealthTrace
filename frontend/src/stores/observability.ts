import { defineStore } from 'pinia';
import api from '@/utils/api';
import type { ObservabilitySummary } from '@/types/observability';

export const useObservabilityStore = defineStore('observability', {
  state: () => ({
    summary: null as ObservabilitySummary | null,
    hours: 24,
    loading: false,
    error: '',
  }),
  actions: {
    async load(hours?: number) {
      const selectedHours = hours ?? this.hours;
      this.loading = true;
      this.error = '';
      this.hours = selectedHours;
      try {
        const response = await api.get('/observability/summary', { params: { hours: selectedHours } });
        this.summary = response.data;
      } catch (error: any) {
        this.error = error?.response?.data?.detail || error?.message || '加载运行指标失败';
      } finally {
        this.loading = false;
      }
    },
  },
});
