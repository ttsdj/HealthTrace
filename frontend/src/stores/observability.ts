import { defineStore } from 'pinia';
import api from '@/utils/api';
import type { ObservabilitySummary, OperationalAlerts } from '@/types/observability';

export const useObservabilityStore = defineStore('observability', {
  state: () => ({
    summary: null as ObservabilitySummary | null,
    alerts: null as OperationalAlerts | null,
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
        const [summaryResponse, alertResponse] = await Promise.all([
          api.get('/observability/summary', { params: { hours: selectedHours } }),
          api.get('/observability/alerts', { params: { hours: selectedHours } }),
        ]);
        this.summary = summaryResponse.data;
        this.alerts = alertResponse.data;
      } catch (error: any) {
        this.error = error?.response?.data?.detail || error?.message || '加载运行指标失败';
      } finally {
        this.loading = false;
      }
    },
  },
});
