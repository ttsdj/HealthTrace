<template>
  <section class="health-record-workspace">
    <header class="record-header">
      <div>
        <span class="record-kicker">PATIENT RECORD</span>
        <h1>我的健康档案</h1>
        <p>患者 {{ maskedPatientId }} · 已确认事实由本人审核后写入</p>
      </div>
      <button class="record-icon-button" title="刷新健康档案" :disabled="store.loading" @click="refresh">
        <i class="fas fa-rotate" :class="{ 'fa-spin': store.loading }"></i>
      </button>
    </header>

    <div class="record-metrics" aria-label="健康档案概览">
      <button v-for="item in metrics" :key="item.key" :class="{ active: activeTab === item.key }" @click="activeTab = item.key">
        <i :class="item.icon"></i>
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </button>
    </div>

    <nav class="record-tabs" aria-label="健康档案栏目">
      <button v-for="tab in tabs" :key="tab.key" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key">
        {{ tab.label }}
        <span v-if="tab.badge">{{ tab.badge }}</span>
      </button>
    </nav>

    <div v-if="store.lastError" class="record-alert error">
      <i class="fas fa-circle-exclamation"></i><span>{{ store.lastError }}</span>
    </div>

    <div v-if="activeTab === 'documents'" class="record-pane">
      <div class="pane-toolbar">
        <div>
          <h2>患者资料</h2>
          <p>原始文件进入患者私有域；上传后先执行本地规则抽取。</p>
        </div>
        <label class="primary-command" :class="{ disabled: store.uploading }">
          <i class="fas fa-paperclip"></i>
          <span>{{ store.uploading ? '处理中...' : '上传资料' }}</span>
          <input type="file" :disabled="store.uploading" accept=".pdf,.docx,.xlsx,.html,.htm,.png,.jpg,.jpeg" @change="onUpload" />
        </label>
      </div>

      <div class="privacy-control">
        <label>
          <input v-model="externalConsent" type="checkbox" />
          <span>我同意在点击“模型增强提取”后，将该文档文本发送给当前配置的外部模型服务。</span>
        </label>
        <small>不勾选时仅使用本地规则，不会向外部模型发送患者资料。</small>
      </div>

      <div v-if="store.documents.length" class="record-table">
        <div class="record-table-head documents-grid">
          <span>文档</span><span>处理状态</span><span>事实提取</span><span>操作</span>
        </div>
        <div v-for="doc in store.documents" :key="doc.document_id" class="record-table-row documents-grid">
          <div class="record-primary">
            <i class="far fa-file-lines"></i>
            <div><strong>{{ doc.filename }}</strong><small>{{ doc.file_type }} · {{ formatDate(doc.created_at) }}</small></div>
          </div>
          <div><span class="status-badge" :class="doc.status">{{ documentStatus(doc.status) }}</span><small>{{ doc.chunks_processed }} 个检索片段</small></div>
          <div><strong>{{ doc.fact_candidate_count }} 条候选</strong><small>{{ extractionLabel(doc) }}</small></div>
          <div class="row-actions">
            <button title="本地规则提取" :disabled="busy(`extract:${doc.document_id}`)" @click="extract(doc.document_id, false)"><i class="fas fa-wand-magic-sparkles"></i></button>
            <button title="模型增强提取" :disabled="!externalConsent || busy(`extract:${doc.document_id}`)" @click="extract(doc.document_id, true)"><i class="fas fa-brain"></i></button>
            <button class="danger" title="删除患者文档" :disabled="busy(`document:${doc.document_id}`)" @click="removeDocument(doc.document_id)"><i class="far fa-trash-can"></i></button>
          </div>
        </div>
      </div>
      <div v-else class="record-empty"><i class="far fa-folder-open"></i><strong>还没有患者资料</strong><span>上传检查报告、病历或用药记录后开始整理。</span></div>
    </div>

    <div v-else-if="activeTab === 'candidates'" class="record-pane">
      <div class="pane-toolbar"><div><h2>待确认事实</h2><p>抽取结果只是候选；核对内容和日期后才会进入正式健康档案。</p></div></div>
      <div v-if="store.pendingCandidates.length" class="candidate-list">
        <article v-for="candidate in store.pendingCandidates" :key="candidate.candidate_id" class="candidate-row">
          <div class="candidate-source">
            <span class="confidence">置信度 {{ Math.round(candidate.confidence * 100) }}%</span>
            <blockquote>{{ candidate.evidence_text }}</blockquote>
            <small>{{ candidate.extraction_method === 'llm' ? '模型提取' : '本地规则' }} · {{ sourceName(candidate.document_id) }}</small>
          </div>
          <div class="candidate-editor">
            <label>资源类型<select v-model="draft(candidate).resource_type"><option v-for="type in resourceTypes" :key="type" :value="type">{{ type }}</option></select></label>
            <label>事实描述<input v-model="draft(candidate).display" /></label>
            <label>发生日期<input v-model="draft(candidate).effective_start" type="date" /></label>
            <label class="wide">结构化值<textarea v-model="draft(candidate).value_json" rows="2"></textarea></label>
            <div class="candidate-actions">
              <button class="secondary-command" @click="reject(candidate.candidate_id)"><i class="fas fa-xmark"></i>忽略</button>
              <button class="primary-command" :disabled="busy(`candidate:${candidate.candidate_id}`)" @click="confirmCandidate(candidate)"><i class="fas fa-check"></i>确认入档</button>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="record-empty"><i class="fas fa-check-double"></i><strong>没有待确认项</strong><span>文档提取出的候选事实会出现在这里。</span></div>
    </div>

    <div v-else-if="activeTab === 'facts'" class="record-pane">
      <div class="pane-toolbar"><div><h2>已确认事实</h2><p>仅展示由用户确认或主动录入的结构化患者事实。</p></div></div>
      <div v-if="store.facts.length" class="fact-list">
        <div v-for="fact in store.facts" :key="fact.fact_id" class="fact-row">
          <span class="resource-icon"><i :class="factIcon(fact.resource_type)"></i></span>
          <div><small>{{ fact.resource_type }}</small><strong>{{ fact.display }}</strong><p>{{ formatValue(fact.value) }}</p></div>
          <div class="fact-date"><strong>{{ formatDate(fact.effective_start) }}</strong><small>{{ fact.verification_status }}</small></div>
          <button class="record-icon-button danger" title="撤回事实" @click="retract(fact.fact_id)"><i class="fas fa-rotate-left"></i></button>
        </div>
      </div>
      <div v-else class="record-empty"><i class="fas fa-clipboard-check"></i><strong>暂无已确认事实</strong><span>请先在“待确认”中核对候选事实。</span></div>
    </div>

    <div v-else-if="activeTab === 'timeline'" class="record-pane">
      <div class="pane-toolbar"><div><h2>健康时间轴</h2><p>按医学事件的发生时间排序，不使用文档上传时间代替临床时间。</p></div></div>
      <div v-if="store.timeline.length" class="timeline-list">
        <div v-for="event in store.timeline" :key="event.event_id" class="timeline-row">
          <time>{{ formatDate(event.effective_at) }}</time><span class="timeline-dot"></span>
          <div><small>{{ event.event_type }} · {{ event.time_precision }}</small><strong>{{ event.title }}</strong><p>{{ event.summary }}</p></div>
        </div>
      </div>
      <div v-else class="record-empty"><i class="far fa-calendar"></i><strong>时间轴还是空的</strong><span>确认带有临床日期的事实后会自动生成事件。</span></div>
    </div>

    <div v-else class="record-pane">
      <div class="pane-toolbar"><div><h2>长期健康任务</h2><p>任务先进入待确认状态；后端负责持久化执行、失败重试和跨会话恢复。</p></div></div>

      <section v-if="store.notifications.length" class="task-section">
        <div class="section-heading"><div><small>INBOX</small><h3>站内健康通知</h3></div><span>{{ store.unreadNotifications.length }} 条未读</span></div>
        <div class="notification-list">
          <article v-for="notice in store.notifications.slice(0, 6)" :key="notice.notification_id" :class="['notification-row', notice.status]">
            <span class="resource-icon"><i class="far fa-envelope"></i></span>
            <div><small>{{ formatDateTime(notice.created_at) }} · {{ notice.notification_type }}</small><strong>{{ notice.title }}</strong><p>{{ notice.body }}</p><div v-if="notice.deliveries?.length" class="delivery-statuses"><span v-for="delivery in notice.deliveries" :key="delivery.delivery_id" :class="delivery.status">{{ delivery.channel }} · {{ deliveryStatus(delivery.status) }}</span></div></div>
            <button v-if="notice.status === 'unread'" class="record-icon-button" title="标为已读" @click="store.markNotificationRead(notice.notification_id)"><i class="fas fa-check"></i></button>
          </article>
        </div>
      </section>

      <section class="task-section">
        <div class="section-heading"><div><small>GOALS</small><h3>健康目标</h3></div><span>{{ store.goals.length }} 个进行中</span></div>
        <form class="goal-form" @submit.prevent="createGoal">
          <label>目标名称<input v-model="goalDraft.title" required placeholder="例如：控制收缩压" /></label>
          <label>目标值<input v-model.number="goalDraft.target_value" required type="number" placeholder="130" /></label>
          <label>单位<input v-model="goalDraft.unit" placeholder="mmHg" /></label>
          <label>截止日期<input v-model="goalDraft.due_at" type="date" /></label>
          <button class="primary-command" type="submit"><i class="fas fa-bullseye"></i>创建目标</button>
        </form>
        <div v-if="store.goals.length" class="goal-list">
          <div v-for="goal in store.goals" :key="goal.goal_id" class="goal-row">
            <span class="resource-icon"><i class="fas fa-bullseye"></i></span>
            <div><strong>{{ goal.title }}</strong><small>目标 {{ formatValue(goal.target) }} · 进度 {{ formatValue(goal.progress) }}</small></div>
            <button class="record-icon-button" title="归档目标" @click="store.archiveGoal(goal.goal_id)"><i class="fas fa-box-archive"></i></button>
          </div>
        </div>
      </section>

      <section class="task-section">
      <div class="section-heading"><div><small>AUTOMATION</small><h3>任务计划</h3></div><span>{{ store.activeTasks.length }} 个活动任务</span></div>
      <form class="task-form" @submit.prevent="createTask">
        <label>任务名称<input v-model="taskDraft.title" required placeholder="例如：复查血压" /></label>
        <label>执行时间<input v-model="taskDraft.due_at" required type="datetime-local" /></label>
        <label>任务类型<select v-model="taskDraft.task_type"><option value="reminder">健康提醒</option><option value="follow_up">症状随访</option><option value="measurement_plan">测量计划</option><option value="periodic_summary">周期摘要</option><option value="health_goal_check">目标检查</option></select></label>
        <label>重复周期<select v-model.number="taskDraft.interval_seconds"><option :value="null">仅一次</option><option :value="86400">每天</option><option :value="604800">每周</option><option :value="2592000">每 30 天</option></select></label>
        <label>通知渠道<select v-model="taskDraft.notification_channel"><option value="in_app">仅站内</option><option value="webhook">站内 + Webhook</option><option value="email">站内 + 邮件</option></select></label>
        <label v-if="taskDraft.notification_channel === 'email'">接收邮箱<input v-model="taskDraft.notification_email" type="email" required placeholder="name@example.com" /></label>
        <label v-if="taskDraft.notification_channel !== 'in_app'" class="external-consent"><input v-model="taskDraft.external_consent" type="checkbox" required /><span>同意将本任务通知内容发送到已配置的外部服务</span></label>
        <button class="primary-command" type="submit"><i class="fas fa-plus"></i>创建草稿</button>
      </form>
      <div v-if="store.tasks.length" class="task-list">
        <div v-for="task in store.tasks" :key="task.task_id" class="task-row">
          <span class="resource-icon"><i class="far fa-bell"></i></span>
          <div><strong>{{ task.title }}</strong><small>{{ formatDateTime(task.next_run_at || task.due_at) }} · {{ task.status }}</small></div>
          <div class="row-actions"><button v-if="task.status === 'waiting_confirmation'" title="确认并激活" @click="store.confirmTask(task.task_id)"><i class="fas fa-check"></i></button><button v-if="!['cancelled', 'completed'].includes(task.status)" class="danger" title="取消任务" @click="store.cancelTask(task.task_id)"><i class="fas fa-ban"></i></button></div>
        </div>
      </div>
      <div v-else class="record-empty compact"><i class="far fa-bell-slash"></i><strong>暂无健康任务</strong></div>
      </section>

      <section v-if="store.taskRuns.length" class="task-section">
        <div class="section-heading"><div><small>EXECUTION</small><h3>最近运行</h3></div><span>持久化执行记录</span></div>
        <div class="run-list">
          <div v-for="run in store.taskRuns.slice(0, 8)" :key="run.run_id" class="run-row">
            <span :class="['status-badge', run.status]">{{ run.status }}</span>
            <div><strong>{{ taskName(run.task_id) }}</strong><small>{{ formatDateTime(run.scheduled_for) }} · 尝试 {{ run.attempt_count }}/{{ run.max_attempts }}</small><p v-if="run.error_message">{{ run.error_message }}</p></div>
            <div class="row-actions"><button v-if="['failed', 'retry_wait'].includes(run.status)" title="立即重试" @click="store.retryTaskRun(run.run_id)"><i class="fas fa-rotate-right"></i></button><button v-if="run.status === 'waiting_input'" title="补录测量值" @click="submitRunInput(run.run_id)"><i class="fas fa-pen"></i></button></div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useAuthStore } from '@/stores/auth';
import { useHealthRecordStore } from '@/stores/healthRecords';
import type { FactCandidate, PatientDocument } from '@/types/healthRecord';

type RecordTab = 'documents' | 'candidates' | 'facts' | 'timeline' | 'tasks';
const store = useHealthRecordStore();
const authStore = useAuthStore();
const activeTab = ref<RecordTab>('documents');
const externalConsent = ref(false);
const drafts = reactive<Record<string, { resource_type: string; display: string; effective_start: string; value_json: string }>>({});
const resourceTypes = ['Condition', 'Observation', 'MedicationStatement', 'AllergyIntolerance', 'DiagnosticReport', 'Procedure', 'Immunization'];
const taskDraft = reactive({ task_type: 'reminder', title: '', due_at: '', interval_seconds: null as number | null, notification_channel: 'in_app', notification_email: '', external_consent: false });
const goalDraft = reactive<{ title: string; target_value: number | null; unit: string; due_at: string }>({ title: '', target_value: null, unit: '', due_at: '' });

const maskedPatientId = computed(() => {
  const id = authStore.currentUser?.patient_id || '未分配';
  return id.length > 10 ? `${id.slice(0, 6)}...${id.slice(-4)}` : id;
});
const tabs = computed(() => [
  { key: 'documents' as RecordTab, label: '患者资料', badge: store.documents.length },
  { key: 'candidates' as RecordTab, label: '待确认', badge: store.pendingCandidates.length },
  { key: 'facts' as RecordTab, label: '健康事实', badge: store.facts.length },
  { key: 'timeline' as RecordTab, label: '时间轴', badge: store.timeline.length },
  { key: 'tasks' as RecordTab, label: '健康任务', badge: store.activeTasks.length },
]);
const metrics = computed(() => [
  { key: 'documents' as RecordTab, label: '私有资料', value: store.documents.length, icon: 'far fa-folder-open' },
  { key: 'candidates' as RecordTab, label: '待本人确认', value: store.pendingCandidates.length, icon: 'fas fa-user-check' },
  { key: 'facts' as RecordTab, label: '已确认事实', value: store.facts.length, icon: 'fas fa-notes-medical' },
  { key: 'tasks' as RecordTab, label: '进行中任务', value: store.activeTasks.length, icon: 'far fa-bell' },
]);

const refresh = async () => { try { await store.loadAll(); } catch (error: any) { console.error(error.message); } };
const busy = (key: string) => Boolean(store.activeOperations[key]);
const formatDate = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeZone: 'Asia/Shanghai' }).format(new Date(value)) : '日期待确认';
const formatDateTime = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Shanghai' }).format(new Date(value)) : '-';
const documentStatus = (value: string) => ({ indexed: '可检索', failed: '处理失败', parsing: '解析中', uploading: '上传中' }[value] || value);
const extractionLabel = (doc: PatientDocument) => doc.fact_extraction_status === 'completed' ? `${doc.fact_extraction_method || 'local_rules'} 已完成` : '尚未提取';
const sourceName = (documentId: string) => store.documents.find((item) => item.document_id === documentId)?.filename || documentId;
const formatValue = (value: Record<string, unknown>) => Object.keys(value || {}).length ? JSON.stringify(value, null, 0) : '无附加结构化值';
const factIcon = (type: string) => ({ Condition: 'fas fa-stethoscope', Observation: 'fas fa-chart-line', MedicationStatement: 'fas fa-pills', AllergyIntolerance: 'fas fa-triangle-exclamation', Procedure: 'fas fa-syringe', Immunization: 'fas fa-shield-virus' }[type] || 'fas fa-file-medical');
const taskName = (taskId: string) => store.tasks.find((item) => item.task_id === taskId)?.title || taskId;

const draft = (candidate: FactCandidate) => {
  if (!drafts[candidate.candidate_id]) drafts[candidate.candidate_id] = {
    resource_type: candidate.resource_type,
    display: candidate.display,
    effective_start: candidate.effective_start?.slice(0, 10) || '',
    value_json: JSON.stringify(candidate.value || {}, null, 2),
  };
  return drafts[candidate.candidate_id];
};

const onUpload = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  try { await store.uploadDocument(file); activeTab.value = 'candidates'; }
  catch (error: any) { alert(error.message); }
  finally { input.value = ''; }
};
const extract = async (documentId: string, useLlm: boolean) => {
  if (useLlm && !externalConsent.value) return;
  try { await store.extractCandidates(documentId, useLlm, useLlm && externalConsent.value); activeTab.value = 'candidates'; }
  catch (error: any) { alert(error.message); }
};
const removeDocument = async (documentId: string) => {
  if (!confirm('删除该患者原始文档及其检索片段？已确认的健康事实会保留审计来源标识。')) return;
  try { await store.deleteDocument(documentId); } catch (error: any) { alert(error.message); }
};
const confirmCandidate = async (candidate: FactCandidate) => {
  const item = draft(candidate);
  if (!item.effective_start) { alert('请确认该医学事件的发生日期。'); return; }
  try {
    const value = JSON.parse(item.value_json || '{}');
    await store.confirmCandidate(candidate.candidate_id, { resource_type: item.resource_type, display: item.display, effective_start: new Date(`${item.effective_start}T00:00:00+08:00`).toISOString(), time_precision: 'day', value });
  } catch (error: any) { alert(error instanceof SyntaxError ? '结构化值必须是合法 JSON。' : error.message); }
};
const reject = async (id: string) => { try { await store.rejectCandidate(id); } catch (error: any) { alert(error.message); } };
const retract = async (id: string) => { if (confirm('撤回这条已确认健康事实？')) await store.retractFact(id); };
const createTask = async () => {
  const dueAt = new Date(taskDraft.due_at);
  if (Number.isNaN(dueAt.getTime())) return;
  try {
    const channels = taskDraft.notification_channel === 'in_app' ? ['in_app'] : ['in_app', taskDraft.notification_channel];
    await store.createTask({ task_type: taskDraft.task_type, title: taskDraft.title, due_at: dueAt.toISOString(), timezone: 'Asia/Shanghai', interval_seconds: taskDraft.interval_seconds, payload: { notification_channels: channels, external_notification_consent: taskDraft.external_consent, notification_email: taskDraft.notification_channel === 'email' ? taskDraft.notification_email : '' }, idempotency_key: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` });
    taskDraft.task_type = 'reminder'; taskDraft.title = ''; taskDraft.due_at = ''; taskDraft.interval_seconds = null; taskDraft.notification_channel = 'in_app'; taskDraft.notification_email = ''; taskDraft.external_consent = false;
  } catch (error: any) { alert(error.message); }
};
const deliveryStatus = (status: string) => ({ pending: '待投递', delivered: '已送达', retry_wait: '等待重试', failed: '失败', skipped: '未启用' }[status] || status);
const createGoal = async () => {
  if (!goalDraft.title || goalDraft.target_value === null) return;
  try {
    await store.createGoal({ title: goalDraft.title, target: { operator: 'lte', value: goalDraft.target_value, unit: goalDraft.unit }, due_at: goalDraft.due_at ? new Date(`${goalDraft.due_at}T23:59:59+08:00`).toISOString() : null, idempotency_key: `goal-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` });
    goalDraft.title = ''; goalDraft.target_value = null; goalDraft.unit = ''; goalDraft.due_at = '';
  } catch (error: any) { alert(error.message); }
};
const submitRunInput = async (runId: string) => {
  const raw = prompt('请输入本次测量值，例如：126/82 或 6.1');
  if (!raw) return;
  try { await store.submitTaskRunInput(runId, { value: raw }); } catch (error: any) { alert(error.message); }
};

onMounted(refresh);
</script>

<style scoped>
.health-record-workspace{height:100%;overflow:auto;background:#f7f9fc;color:#102343;padding:30px 34px 48px}.record-header{display:flex;align-items:flex-start;justify-content:space-between;border-bottom:1px solid #dce5f0;padding-bottom:22px}.record-kicker{color:#087b9d;font-size:12px;font-weight:800}.record-header h1{font-size:30px;line-height:1.2;margin:6px 0;color:#102343}.record-header p,.pane-toolbar p{margin:0;color:#64748b;font-size:14px}.record-icon-button,.row-actions button{width:38px;height:38px;border:1px solid #d5dfeb;border-radius:6px;background:#fff;color:#28527a;cursor:pointer}.record-icon-button:disabled,.row-actions button:disabled,.primary-command.disabled{opacity:.5;cursor:not-allowed}.record-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid #dce5f0;border-radius:8px;background:#fff;margin:22px 0 0;overflow:hidden}.record-metrics button{min-height:84px;display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:8px;padding:16px 18px;border:0;border-right:1px solid #e5ebf3;background:#fff;text-align:left;color:#64748b;cursor:pointer}.record-metrics button:last-child{border-right:0}.record-metrics button.active{background:#eff8fb;color:#087b9d}.record-metrics i{font-size:18px}.record-metrics strong{font-size:25px;color:#102343}.record-tabs{display:flex;gap:24px;border-bottom:1px solid #dce5f0;margin-top:18px}.record-tabs button{padding:15px 2px 12px;border:0;border-bottom:3px solid transparent;background:transparent;color:#64748b;font-size:15px;font-weight:700;cursor:pointer}.record-tabs button.active{color:#087b9d;border-bottom-color:#0a91b8}.record-tabs span{display:inline-grid;place-items:center;min-width:22px;height:22px;margin-left:5px;border-radius:11px;background:#e8eef5;font-size:12px}.record-pane{margin-top:22px}.pane-toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.pane-toolbar h2{margin:0 0 5px;font-size:21px}.primary-command,.secondary-command{min-height:40px;display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:0 15px;border:1px solid #0a86aa;border-radius:6px;background:#0a86aa;color:#fff;font-weight:700;cursor:pointer}.secondary-command{background:#fff;color:#425b76;border-color:#cfd9e5}.primary-command input{display:none}.privacy-control{padding:14px 16px;border:1px solid #b8dfe9;border-radius:8px;background:#effbfe;margin-bottom:18px}.privacy-control label{display:flex;align-items:flex-start;gap:10px;color:#183b55;font-size:14px}.privacy-control small{display:block;margin:7px 0 0 24px;color:#64748b}.record-table{border:1px solid #dce5f0;border-radius:8px;background:#fff;overflow:hidden}.documents-grid{display:grid;grid-template-columns:minmax(240px,1.5fr) minmax(130px,.7fr) minmax(150px,.8fr) 150px;gap:16px;align-items:center}.record-table-head{padding:11px 16px;background:#eef3f8;color:#53657a;font-size:12px;font-weight:800}.record-table-row{min-height:74px;padding:13px 16px;border-top:1px solid #e6edf4}.record-table-row:first-of-type{border-top:0}.record-primary{display:flex;align-items:center;gap:12px;min-width:0}.record-primary>i{color:#0a86aa;font-size:21px}.record-primary strong,.record-primary small,.record-table-row>div>small{display:block;overflow:hidden;text-overflow:ellipsis}.record-primary strong{white-space:nowrap}.record-primary small,.record-table-row>div>small{margin-top:5px;color:#718096;font-size:12px}.status-badge{display:inline-flex;padding:4px 8px;border-radius:5px;background:#e7f7f1;color:#087f5b;font-size:12px;font-weight:800}.status-badge.failed{background:#fff0f0;color:#b42318}.row-actions{display:flex;justify-content:flex-end;gap:7px}.row-actions .danger,.record-icon-button.danger{color:#b42318}.record-empty{min-height:260px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:9px;border:1px dashed #cbd7e4;border-radius:8px;color:#64748b;background:#fff}.record-empty i{font-size:28px;color:#7ca5bb}.record-empty strong{color:#273e58}.record-empty.compact{min-height:140px}.candidate-list,.fact-list,.task-list{display:grid;gap:10px}.candidate-row{display:grid;grid-template-columns:minmax(230px,.9fr) minmax(420px,1.6fr);border:1px solid #dce5f0;border-radius:8px;background:#fff;overflow:hidden}.candidate-source{padding:18px;background:#f3f7fa;border-right:1px solid #e0e8f1}.confidence{display:inline-flex;padding:4px 8px;border-radius:5px;background:#dff5f5;color:#087b7b;font-size:12px;font-weight:800}.candidate-source blockquote{margin:14px 0;padding-left:12px;border-left:3px solid #0a91b8;color:#2f475f;line-height:1.65;font-size:14px}.candidate-source small{color:#718096}.candidate-editor{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:18px}.candidate-editor label,.task-form label{display:grid;gap:6px;color:#42556b;font-size:12px;font-weight:800}.candidate-editor input,.candidate-editor select,.candidate-editor textarea,.task-form input,.task-form select{width:100%;border:1px solid #cfd9e5;border-radius:6px;background:#fff;padding:9px 10px;color:#102343;font:inherit}.candidate-editor .wide{grid-column:1/-1}.candidate-actions{grid-column:1/-1;display:flex;justify-content:flex-end;gap:8px}.fact-row,.task-row{display:grid;grid-template-columns:44px minmax(0,1fr) 190px 42px;align-items:center;gap:12px;padding:15px 16px;border:1px solid #dce5f0;border-radius:8px;background:#fff}.resource-icon{width:40px;height:40px;display:grid;place-items:center;border-radius:7px;background:#eaf7fa;color:#087b9d}.fact-row small,.fact-row strong,.fact-row p,.task-row small,.task-row strong{display:block}.fact-row small,.task-row small{color:#718096;font-size:12px}.fact-row strong,.task-row strong{margin-top:3px}.fact-row p{margin:4px 0 0;color:#53657a;font-size:13px}.fact-date{text-align:right}.timeline-list{position:relative}.timeline-row{display:grid;grid-template-columns:120px 22px minmax(0,1fr);gap:12px;min-height:92px}.timeline-row time{padding-top:4px;text-align:right;color:#53657a;font-size:13px;font-weight:700}.timeline-dot{position:relative;width:12px;height:12px;margin:5px;border-radius:50%;background:#0a91b8;box-shadow:0 0 0 4px #dff5fa}.timeline-dot:after{content:"";position:absolute;left:5px;top:15px;width:2px;height:72px;background:#cfe1e9}.timeline-row:last-child .timeline-dot:after{display:none}.timeline-row small,.timeline-row strong{display:block}.timeline-row small{color:#718096;font-size:12px}.timeline-row strong{margin:5px 0}.timeline-row p{margin:0;color:#53657a}.task-form{display:grid;grid-template-columns:1.2fr 1fr .8fr .8fr auto;align-items:end;gap:12px;padding:16px;border:1px solid #dce5f0;border-radius:8px;background:#fff;margin-bottom:16px}.task-row{grid-template-columns:44px minmax(0,1fr) auto}.record-alert{display:flex;gap:9px;padding:12px 14px;margin-top:16px;border-radius:7px}.record-alert.error{background:#fff1f0;color:#a61b1b;border:1px solid #ffd1cc}@media(max-width:1000px){.record-metrics{grid-template-columns:1fr 1fr}.record-metrics button:nth-child(2){border-right:0}.record-metrics button:nth-child(-n+2){border-bottom:1px solid #e5ebf3}.candidate-row{grid-template-columns:1fr}.candidate-source{border-right:0;border-bottom:1px solid #e0e8f1}.documents-grid{grid-template-columns:1fr}.record-table-head{display:none}.record-table-row{gap:10px}.row-actions{justify-content:flex-start}.task-form{grid-template-columns:1fr 1fr}.task-form button{align-self:end}}@media(max-width:700px){.health-record-workspace{padding:20px 16px}.record-metrics{grid-template-columns:1fr}.record-metrics button{border-right:0;border-bottom:1px solid #e5ebf3}.record-tabs{overflow:auto}.candidate-editor{grid-template-columns:1fr}.candidate-editor .wide,.candidate-actions{grid-column:1}.fact-row{grid-template-columns:42px 1fr}.fact-date,.fact-row>button{grid-column:2;text-align:left}.task-form{grid-template-columns:1fr}}
.record-tabs button,.primary-command,.secondary-command{white-space:nowrap}.record-tabs button{flex:0 0 auto}@media(max-width:700px){.record-tabs{gap:18px}}
.task-section{margin-bottom:24px;padding-bottom:24px;border-bottom:1px solid #dce5f0}.task-section:last-child{border-bottom:0}.section-heading{display:flex;align-items:flex-end;justify-content:space-between;margin:0 0 12px}.section-heading small{display:block;color:#087b9d;font-size:11px;font-weight:800}.section-heading h3{margin:3px 0 0;font-size:18px}.section-heading>span{color:#64748b;font-size:13px}.goal-form{display:grid;grid-template-columns:1.3fr .7fr .7fr .9fr auto;align-items:end;gap:12px;padding:16px;border:1px solid #dce5f0;border-radius:8px;background:#fff;margin-bottom:12px}.goal-form label{display:grid;gap:6px;color:#42556b;font-size:12px;font-weight:800}.goal-form input{width:100%;border:1px solid #cfd9e5;border-radius:6px;background:#fff;padding:9px 10px;color:#102343;font:inherit}.goal-list,.notification-list,.run-list{display:grid;gap:9px}.goal-row,.notification-row,.run-row{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:12px;padding:14px 16px;border:1px solid #dce5f0;border-radius:8px;background:#fff}.notification-row.unread{border-left:3px solid #0a91b8}.notification-row p,.run-row p{margin:5px 0 0;color:#53657a;font-size:13px}.notification-row small,.goal-row small,.run-row small{display:block;color:#718096;font-size:12px}.notification-row strong,.goal-row strong,.run-row strong{display:block;margin-top:3px}.run-row{grid-template-columns:100px minmax(0,1fr) auto}.status-badge.retry_wait,.status-badge.waiting_input{background:#fff6df;color:#976800}.status-badge.running,.status-badge.ready{background:#e8f2ff;color:#1959a6}.status-badge.completed{background:#e7f7f1;color:#087f5b}@media(max-width:1100px){.goal-form,.task-form{grid-template-columns:1fr 1fr}.goal-form button,.task-form button{align-self:end}}@media(max-width:700px){.goal-form{grid-template-columns:1fr}.goal-row,.notification-row,.run-row{grid-template-columns:42px 1fr}.goal-row>button,.notification-row>button,.run-row>.row-actions{grid-column:2;justify-content:flex-start}}
.external-consent{display:flex!important;align-items:center;gap:8px;min-width:210px}.external-consent input{width:18px!important;height:18px}.delivery-statuses{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.delivery-statuses span{padding:3px 6px;border:1px solid #d5dfeb;border-radius:4px;background:#f4f7fa;color:#52677d;font-size:11px;font-weight:700}.delivery-statuses span.delivered{border-color:#a7dfca;background:#ecf8f3;color:#087a59}.delivery-statuses span.failed{border-color:#f0b8b3;background:#fff2f1;color:#a72c24}.delivery-statuses span.retry_wait{border-color:#ead18e;background:#fff9e8;color:#8a6505}
</style>
