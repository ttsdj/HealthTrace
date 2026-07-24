<template>
  <section class="ops-workspace">
    <header class="ops-header">
      <div>
        <span class="ops-kicker">SYSTEM OBSERVABILITY</span>
        <h1>Health Agent 运行监控</h1>
        <p>聚合检索、工具和长期任务状态，不展示对话正文或患者标识。</p>
      </div>
      <div class="ops-commands">
        <div class="period-control" aria-label="统计时间范围">
          <button v-for="item in periods" :key="item.hours" :class="{ active: store.hours === item.hours }" @click="store.load(item.hours)">{{ item.label }}</button>
        </div>
        <button class="refresh-command" title="刷新运行指标" :disabled="store.loading" @click="store.load()">
          <i class="fas fa-rotate" :class="{ 'fa-spin': store.loading }"></i>
        </button>
      </div>
    </header>

    <div v-if="store.error" class="ops-error"><i class="fas fa-circle-exclamation"></i>{{ store.error }}</div>
    <div v-else-if="!summary" class="ops-empty">正在读取聚合指标...</div>
    <template v-else>
      <div v-if="store.alerts?.alert_count" class="ops-alerts" :class="store.alerts.status">
        <div><i class="fas fa-triangle-exclamation"></i><strong>运行告警 {{ store.alerts.alert_count }} 项</strong></div>
        <span v-for="item in store.alerts.alerts" :key="item.code">{{ alertLabel(item.code) }}</span>
      </div>
      <div v-else class="ops-alerts ok">
        <div><i class="fas fa-circle-check"></i><strong>当前没有触发运行告警</strong></div>
      </div>
      <div class="ops-metrics">
        <div><span>可观测回合</span><strong>{{ summary.chat.trace_turns }}</strong><small>{{ rangeLabel }}</small></div>
        <div><span>检索降级率</span><strong>{{ percent(summary.chat.fallback_rate) }}</strong><small>{{ summary.chat.fallback_count }} 次降级</small></div>
        <div><span>工具成功率</span><strong>{{ percent(summary.tools.success_rate) }}</strong><small>{{ summary.tools.call_count }} 次调用</small></div>
        <div><span>工具 P95</span><strong>{{ duration(summary.tools.latency_p95_ms) }}</strong><small>P50 {{ duration(summary.tools.latency_p50_ms) }}</small></div>
        <div><span>任务重试率</span><strong>{{ percent(summary.tasks.retry_rate) }}</strong><small>{{ summary.tasks.run_count }} 次运行</small></div>
        <div><span>持久化后台作业</span><strong>{{ summary.background_jobs.count }}</strong><small>{{ summary.background_jobs.retry_count }} 次发生重试</small></div>
      </div>

      <div class="ops-grid">
        <section class="ops-section">
          <div class="section-title"><div><small>EVIDENCE FLOW</small><h2>证据状态与行动</h2></div><span>{{ summary.chat.no_evidence_count }} 次无证据</span></div>
          <div class="distribution-columns">
            <DistributionList title="证据状态" :values="summary.chat.evidence_states" :total="summary.chat.trace_turns" />
            <DistributionList title="Agent 行动" :values="summary.chat.actions" :total="summary.chat.trace_turns" />
          </div>
        </section>

        <section class="ops-section">
          <div class="section-title"><div><small>RETRIEVAL</small><h2>检索模式分布</h2></div><span>{{ summary.chat.high_risk_count }} 次高风险升级</span></div>
          <DistributionList title="执行路径" :values="summary.chat.retrieval_modes" :total="summary.chat.trace_turns" />
        </section>

        <section class="ops-section quality-section">
          <div class="section-title"><div><small>RUNTIME SIGNAL</small><h2>回答质量信号</h2></div><span class="heuristic-tag">启发式监控</span></div>
          <div class="quality-row">
            <div v-for="item in qualityItems" :key="item.key"><span>{{ item.label }}</span><strong>{{ score(item.value) }}</strong><i><b :style="{ width: `${(item.value || 0) * 100}%` }"></b></i></div>
          </div>
          <p>此处是无人工 reference 的 ragas_lite 运行信号，不等同于离线正式 RAGAS 评测结果。</p>
        </section>

        <section class="ops-section">
          <div class="section-title"><div><small>BACKGROUND JOBS</small><h2>长期任务执行</h2></div><span>{{ summary.notifications.count }} 条站内通知</span></div>
          <div class="job-strip">
            <div v-for="(count, status) in summary.tasks.status_counts" :key="status"><span>{{ statusLabel(status) }}</span><strong>{{ count }}</strong></div>
            <div v-if="!Object.keys(summary.tasks.status_counts).length"><span>暂无运行记录</span><strong>0</strong></div>
          </div>
        </section>

        <section class="ops-section">
          <div class="section-title"><div><small>DURABLE QUEUE</small><h2>后台队列与审计</h2></div><span>{{ summary.audit.event_count }} 条审计事件</span></div>
          <div class="distribution-columns">
            <DistributionList title="后台作业状态" :values="summary.background_jobs.status_counts" :total="summary.background_jobs.count" />
            <DistributionList title="审计结果" :values="summary.audit.outcome_counts" :total="summary.audit.event_count" />
          </div>
        </section>
      </div>

      <footer class="privacy-foot"><i class="fas fa-shield-halved"></i><span>聚合接口已关闭提示词正文与患者标识输出</span><time>更新于 {{ updatedAt }}</time></footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, type PropType } from 'vue';
import { useObservabilityStore } from '@/stores/observability';

const store = useObservabilityStore();
const summary = computed(() => store.summary);
const periods = [{ label: '24 小时', hours: 24 }, { label: '7 天', hours: 168 }, { label: '30 天', hours: 720 }];
const percent = (value: number | null) => value == null ? '—' : `${Math.round(value * 100)}%`;
const score = (value: number | null) => value == null ? '—' : value.toFixed(3);
const duration = (value: number | null) => value == null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
const rangeLabel = computed(() => store.hours === 24 ? '最近 24 小时' : store.hours === 168 ? '最近 7 天' : '最近 30 天');
const updatedAt = computed(() => summary.value ? new Date(summary.value.window.end_at).toLocaleString('zh-CN', { hour12: false }) : '');
const qualityItems = computed(() => summary.value ? [
  { key: 'context', label: '上下文相关性', value: summary.value.quality.ragas_context_relevance },
  { key: 'faith', label: '忠实度', value: summary.value.quality.ragas_faithfulness },
  { key: 'answer', label: '答案相关性', value: summary.value.quality.ragas_answer_relevance },
  { key: 'overall', label: '综合信号', value: summary.value.quality.ragas_quality_score },
] : []);
const statusLabel = (status: string) => ({ completed: '已完成', failed: '失败', retry_wait: '等待重试', running: '运行中', ready: '待执行', waiting_input: '等待输入' }[status] || status);
const alertLabel = (code: string) => ({
  rag_fallback_rate_high: '检索降级率偏高',
  tool_success_rate_low: '工具成功率偏低',
  tool_latency_p95_high: '工具延迟偏高',
  background_jobs_failed: '后台作业失败',
  notification_deliveries_failed: '外部通知失败',
  audited_requests_error: '接口出现服务端错误',
}[code] || code);

const DistributionList = defineComponent({
  props: { title: { type: String, required: true }, values: { type: Object as PropType<Record<string, number>>, required: true }, total: { type: Number, required: true } },
  setup(props) {
    return () => h('div', { class: 'distribution-list' }, [
      h('h3', props.title),
      ...Object.entries(props.values).map(([label, count]) => h('div', { class: 'distribution-row' }, [
        h('span', label), h('i', [h('b', { style: { width: `${props.total ? count / props.total * 100 : 0}%` } })]), h('strong', String(count)),
      ])),
      ...(!Object.keys(props.values).length ? [h('p', '暂无数据')] : []),
    ]);
  },
});

onMounted(() => store.load());
</script>

<style scoped>
.ops-workspace{height:100%;overflow:auto;background:#f5f8fb;color:#12243c;padding:32px 38px 44px;font-family:"Source Han Sans SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif}.ops-header{display:flex;align-items:flex-start;justify-content:space-between;gap:28px;padding-bottom:24px;border-bottom:1px solid #ccd9e5}.ops-kicker,.section-title small{color:#087b9d;font-size:12px;font-weight:900;letter-spacing:0}.ops-header h1{margin:7px 0 6px;font-size:30px;line-height:1.2}.ops-header p,.quality-section p{margin:0;color:#60738a;font-size:14px}.ops-commands{display:flex;gap:10px;align-items:center}.period-control{display:flex;border:1px solid #cbd8e5;border-radius:6px;background:#fff;overflow:hidden}.period-control button{height:39px;padding:0 13px;border:0;border-right:1px solid #dbe4ed;background:#fff;color:#50647c;font-weight:700;cursor:pointer}.period-control button:last-child{border-right:0}.period-control button.active{background:#e7f6f8;color:#067a95}.refresh-command{width:40px;height:40px;border:1px solid #cbd8e5;border-radius:6px;background:#fff;color:#087b9d;cursor:pointer}.ops-error,.ops-empty{margin-top:20px;padding:16px;border:1px solid #efc7c3;background:#fff6f5;color:#a23028;border-radius:6px}.ops-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));margin:24px 0;border:1px solid #d4dfe9;background:#fff;border-radius:7px;overflow:hidden}.ops-metrics>div{min-height:102px;padding:17px 18px;border-right:1px solid #e0e7ee}.ops-metrics>div:last-child{border-right:0}.ops-metrics span,.ops-metrics small,.quality-row span{display:block;color:#63758b;font-size:13px}.ops-metrics strong{display:block;margin:5px 0 3px;color:#102a46;font-size:27px}.ops-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(320px,.65fr);border-top:1px solid #d4dfe9}.ops-section{padding:24px 0;border-bottom:1px solid #d4dfe9}.ops-section:nth-child(odd){padding-right:28px}.ops-section:nth-child(even){padding-left:28px;border-left:1px solid #d4dfe9}.quality-section{grid-column:1/-1!important;padding:24px 0!important;border-left:0!important}.section-title{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px}.section-title h2{margin:4px 0 0;font-size:20px}.section-title>span{color:#65768b;font-size:13px}.distribution-columns{display:grid;grid-template-columns:1fr 1fr;gap:32px}.distribution-list h3{margin:0 0 13px;font-size:14px}.distribution-row{display:grid;grid-template-columns:minmax(100px,1fr) minmax(90px,1.4fr) 32px;align-items:center;gap:10px;margin:10px 0;font-size:13px}.distribution-row i,.quality-row i{height:7px;overflow:hidden;background:#dce5ed;border-radius:2px}.distribution-row b,.quality-row b{display:block;height:100%;background:#0a8eaa}.distribution-row strong{text-align:right}.distribution-list p{color:#7a899a}.heuristic-tag{padding:5px 8px;border:1px solid #e9cc8a;border-radius:5px;background:#fff8e8;color:#8a6200!important;font-weight:800}.quality-row{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid #d4dfe9;background:#fff}.quality-row>div{padding:17px 18px;border-right:1px solid #e0e7ee}.quality-row>div:last-child{border-right:0}.quality-row strong{display:block;margin:6px 0 10px;font-size:23px}.quality-section p{margin-top:10px}.job-strip{display:flex;flex-wrap:wrap;border:1px solid #d4dfe9;background:#fff}.job-strip>div{min-width:120px;flex:1;padding:15px;border-right:1px solid #e0e7ee}.job-strip span,.job-strip strong{display:block}.job-strip span{color:#63758b;font-size:12px}.job-strip strong{margin-top:4px;font-size:21px}.privacy-foot{display:flex;align-items:center;gap:9px;padding-top:18px;color:#557087;font-size:13px}.privacy-foot i{color:#0b8b7d}.privacy-foot time{margin-left:auto}.ops-error{display:flex;gap:8px}@media(max-width:1050px){.ops-metrics{grid-template-columns:repeat(2,1fr)}.ops-metrics>div{border-bottom:1px solid #e0e7ee}.ops-grid{grid-template-columns:1fr}.ops-section,.ops-section:nth-child(odd),.ops-section:nth-child(even){padding:22px 0;border-left:0}.quality-section{grid-column:1}.quality-row{grid-template-columns:1fr 1fr}}@media(max-width:700px){.ops-workspace{padding:22px 16px}.ops-header{display:block}.ops-commands{margin-top:16px}.ops-metrics,.distribution-columns,.quality-row{grid-template-columns:1fr}.period-control{flex:1}.period-control button{flex:1}.privacy-foot{align-items:flex-start;flex-wrap:wrap}.privacy-foot time{width:100%;margin-left:25px}}
.ops-alerts{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:20px;padding:13px 15px;border:1px solid #eacb88;border-radius:7px;background:#fff8e8;color:#795700}.ops-alerts>div{display:flex;align-items:center;gap:8px}.ops-alerts span{padding:3px 7px;border-radius:4px;background:rgba(255,255,255,.75);font-size:12px;font-weight:700}.ops-alerts.critical{border-color:#efb4af;background:#fff1f0;color:#a52b23}.ops-alerts.ok{border-color:#a7ddca;background:#edf9f4;color:#087557}.ops-metrics{grid-template-columns:repeat(6,minmax(0,1fr))}@media(max-width:1200px){.ops-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:1050px){.ops-metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.ops-metrics{grid-template-columns:1fr}}
</style>
