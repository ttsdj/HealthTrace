import { defineStore } from 'pinia';
import { useAuthStore } from './auth';
import { useSessionStore } from './sessions';
import api from '@/utils/api';
import type { Message, RagStep, GroupedRagStep, RequestLocation } from '@/types/chat';

const createSessionId = () =>
  `session_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [] as Message[],
    userInput: '',
    isLoading: false,
    activeNav: 'newChat' as 'newChat' | 'history' | 'settings' | 'healthRecord',
    sessionId: createSessionId(),
    abortController: null as AbortController | null,
    activeControllers: {} as Record<string, AbortController>,
    pendingSessions: {} as Record<string, boolean>,
    loadSessionRequestId: 0,
    locationContext: null as RequestLocation | null,
    locationStatus: 'idle' as 'idle' | 'requesting' | 'ready' | 'denied' | 'unsupported',
  }),

  actions: {
    syncLoadingState() {
      this.isLoading = Boolean(this.pendingSessions[this.sessionId]);
      this.abortController = this.activeControllers[this.sessionId] || null;
    },

    appendRagStepToGroups(prev: GroupedRagStep[], step: RagStep): GroupedRagStep[] {
      const groups = prev ? [...prev] : [];
      const group = step.group || null;

      if (group) {
        const idx = groups.findIndex((item) => item.group === group);
        if (idx >= 0) {
          const existing = groups[idx];
          groups[idx] = {
            group: existing.group,
            label: existing.label,
            steps: [...existing.steps, step],
            collapsed: existing.collapsed,
          };
          return groups;
        }
        return [...groups, { group, label: group, steps: [step], collapsed: true }];
      }

      const last = groups.length > 0 ? groups[groups.length - 1] : null;
      if (last && last.group === null) {
        groups[groups.length - 1] = { ...last, steps: [...last.steps, step] };
        return groups;
      }
      return [...groups, { group: null, label: null, steps: [step], collapsed: false }];
    },

    groupRagSteps(steps: RagStep[]): GroupedRagStep[] {
      if (!steps || !steps.length) return [];
      return steps.reduce((groups: GroupedRagStep[], step) => this.appendRagStepToGroups(groups, step), []);
    },

    toggleStepGroup(msgIndex: number, groupIndex: number) {
      const msg = this.messages[msgIndex];
      if (!msg || !msg._groupedSteps || !msg._groupedSteps[groupIndex]) return;
      msg._groupedSteps[groupIndex].collapsed = !msg._groupedSteps[groupIndex].collapsed;
    },

    handleNewChat() {
      this.messages = [];
      this.userInput = '';
      this.sessionId = createSessionId();
      this.activeNav = 'newChat';
      this.clearLocation();
      this.syncLoadingState();
      const sessionStore = useSessionStore();
      sessionStore.showHistorySidebar = false;
    },

    handleClearChat() {
      if (confirm('确定要清空当前医疗问答会话吗？')) {
        this.messages = [];
        this.clearLocation();
      }
    },

    async loadSession(sessionId: string) {
      const requestId = ++this.loadSessionRequestId;
      this.sessionId = sessionId;
      this.activeNav = 'newChat';
      this.userInput = '';
      this.clearLocation();
      this.syncLoadingState();
      const sessionStore = useSessionStore();
      sessionStore.showHistorySidebar = false;

      try {
        const response = await api.get(`/sessions/${encodeURIComponent(sessionId)}`);
        if (requestId !== this.loadSessionRequestId || this.sessionId !== sessionId) {
          return;
        }
        const data = response.data;
        this.messages = (data.messages || []).map((msg: any) => ({
          text: msg.content,
          isUser: msg.type === 'human',
          ragTrace: msg.rag_trace || null,
        }));
        if (this.pendingSessions[sessionId]) {
          const hasThinking = this.messages.some((message) => !message.isUser && message.isThinking);
          if (!hasThinking) {
            this.messages.push({
              text: '',
              isUser: false,
              isThinking: true,
              ragTrace: null,
              ragSteps: [],
              _groupedSteps: [],
            });
          }
        }
      } catch (error: any) {
        const errMsg = error.response?.data?.detail || error.message || '加载会话失败';
        this.messages = [];
        throw new Error(errMsg);
      }
    },

    handleStop() {
      const controller = this.activeControllers[this.sessionId];
      if (controller) {
        controller.abort();
        delete this.activeControllers[this.sessionId];
        delete this.pendingSessions[this.sessionId];
        this.syncLoadingState();
      }
    },

    clearLocation() {
      this.locationContext = null;
      this.locationStatus = 'idle';
    },

    async requestLocation() {
      if (this.locationContext) {
        this.clearLocation();
        return;
      }
      if (!window.isSecureContext || !navigator.geolocation) {
        this.locationStatus = 'unsupported';
        throw new Error('当前浏览器环境不支持安全定位，请使用 localhost 或 HTTPS');
      }
      const accepted = confirm(
        '是否授权 HealthTrace 获取一次当前位置，用于搜索附近医院和计算距离？坐标仅随下一条消息发送，不写入长期记忆。'
      );
      if (!accepted) return;

      this.locationStatus = 'requesting';
      try {
        const position = await new Promise<GeolocationPosition>((resolve, reject) => {
          navigator.geolocation.getCurrentPosition(resolve, reject, {
            enableHighAccuracy: false,
            timeout: 10000,
            maximumAge: 60000,
          });
        });
        this.locationContext = {
          authorized: true,
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy_meters: position.coords.accuracy,
        };
        this.locationStatus = 'ready';
      } catch (error) {
        this.locationContext = null;
        this.locationStatus = 'denied';
        throw new Error('未获得定位权限，可在浏览器地址栏权限设置中重新允许');
      }
    },

    async handleSend() {
      const authStore = useAuthStore();
      const sessionStore = useSessionStore();

      if (!authStore.isAuthenticated) {
        alert('请先登录');
        return;
      }

      const text = this.userInput.trim();
      const sendSessionId = this.sessionId;
      if (!text || this.pendingSessions[sendSessionId]) return;
      const requestLocation = this.locationContext ? { ...this.locationContext } : null;

      this.messages.push({
        text,
        isUser: true,
      });

      if (this.messages.length === 1) {
        const tempTitle = text.length > 10 ? text.substring(0, 10) + '...' : text;
        const existingSession = sessionStore.sessions.find((s) => s.session_id === sendSessionId);
        if (!existingSession) {
          sessionStore.sessions.unshift({
            session_id: sendSessionId,
            title: tempTitle,
            message_count: 1,
            updated_at: new Date().toISOString(),
          });
        }
      }

      this.userInput = '';
      this.pendingSessions = { ...this.pendingSessions, [sendSessionId]: true };
      this.syncLoadingState();

      const assistantMessage: Message = {
        text: '',
        isUser: false,
        isThinking: true,
        ragTrace: null,
        ragSteps: [],
        _groupedSteps: [],
      };
      this.messages.push(assistantMessage);

      const controller = new AbortController();
      this.activeControllers = { ...this.activeControllers, [sendSessionId]: controller };
      this.syncLoadingState();

      const updateVisibleAssistant = (updater: (message: Message) => void) => {
        if (this.sessionId !== sendSessionId) return;
        const msg = this.messages.includes(assistantMessage)
          ? assistantMessage
          : [...this.messages].reverse().find((item) => !item.isUser && item.isThinking);
        if (!msg) return;
        updater(msg);
      };

      try {
        const response = await fetch('/chat/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${authStore.token}`,
          },
          body: JSON.stringify({
            message: text,
            session_id: sendSessionId,
            location: requestLocation,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          if (response.status === 401) {
            authStore.handleLogout();
            throw new Error('登录已过期，请重新登录');
          }
          throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('无法读取响应流');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          let eventEndIndex;
          while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
            const eventStr = buffer.slice(0, eventEndIndex);
            buffer = buffer.slice(eventEndIndex + 2);

            if (!eventStr.startsWith('data: ')) continue;
            const dataStr = eventStr.slice(6);
            if (dataStr === '[DONE]') continue;
            try {
              const data = JSON.parse(dataStr);
              if (data.type === 'content') {
                updateVisibleAssistant((msg) => {
                  msg.isThinking = false;
                  msg.text += data.content;
                });
              } else if (data.type === 'content_replace') {
                updateVisibleAssistant((msg) => {
                  msg.isThinking = false;
                  msg.text = data.content;
                });
              } else if (data.type === 'trace') {
                updateVisibleAssistant((msg) => {
                  msg.ragTrace = data.rag_trace;
                });
              } else if (data.type === 'rag_step') {
                updateVisibleAssistant((msg) => {
                  if (!msg.ragSteps) msg.ragSteps = [];
                  msg.ragSteps.push(data.step);
                  msg._groupedSteps = this.appendRagStepToGroups(msg._groupedSteps || [], data.step);
                });
              } else if (data.type === 'session_title') {
                const s = sessionStore.sessions.find((item) => item.session_id === data.session_id);
                if (s) {
                  s.title = data.title;
                  s.updated_at = new Date().toISOString();
                  s.message_count = this.sessionId === sendSessionId ? this.messages.length : s.message_count;
                }
              } else if (data.type === 'error') {
                updateVisibleAssistant((msg) => {
                  msg.isThinking = false;
                  msg.text += `\n[Error: ${data.content}]`;
                });
              }
            } catch (e) {
              console.warn('SSE parse error:', e);
            }
          }
        }
      } catch (error: any) {
        if (error.name === 'AbortError') {
          updateVisibleAssistant((msg) => {
            msg.isThinking = false;
            msg.text = msg.text ? `${msg.text}\n\n_(回答已被终止)_` : '(已终止回答)';
          });
        } else {
          updateVisibleAssistant((msg) => {
            msg.isThinking = false;
            msg.text = `系统处理失败：${error.message}`;
          });
        }
      } finally {
        delete this.pendingSessions[sendSessionId];
        delete this.activeControllers[sendSessionId];
        if (this.sessionId === sendSessionId) {
          this.clearLocation();
        }
        this.syncLoadingState();
        try {
          await sessionStore.fetchSessions();
          if (this.sessionId === sendSessionId) {
            const response = await api.get(`/sessions/${encodeURIComponent(sendSessionId)}`);
            const data = response.data;
            this.messages = (data.messages || []).map((msg: any) => ({
              text: msg.content,
              isUser: msg.type === 'human',
              ragTrace: msg.rag_trace || null,
            }));
          }
        } catch (refreshError) {
          console.warn('Session refresh failed:', refreshError);
        }
      }
    },
  },
});
