<template>
  <aside class="retrieval-panel">
    <div class="retrieval-panel-header">
      <div>
        <span class="panel-kicker">RAG OBSERVABILITY</span>
        <h2>检索过程</h2>
      </div>
      <span class="trace-state" :class="{ active: !!trace }">
        {{ trace ? '已完成' : '等待查询' }}
      </span>
    </div>

    <div class="retrieval-tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <div v-if="!trace" class="retrieval-empty">
      <div class="empty-radar">
        <i class="fas fa-crosshairs"></i>
      </div>
      <strong>暂无检索记录</strong>
      <p>发送问题后，这里会显示召回、重排、知识图谱、记忆命中和安全边界。</p>
    </div>

    <template v-else>
      <div v-if="activeTab === 'retrieval'" class="retrieval-content">
        <section class="metrics-section">
          <div class="panel-section-title">
            <span>检索统计</span>
            <small>{{ modeLabel }}</small>
          </div>
          <div class="metric-grid">
            <div class="metric-cell">
              <span>候选池</span>
              <strong>{{ displayNumber(trace.candidate_k) }}</strong>
            </div>
            <div class="metric-cell">
              <span>召回</span>
              <strong>{{ displayNumber(trace.recall_count) }}</strong>
            </div>
            <div class="metric-cell">
              <span>合并后</span>
              <strong>{{ displayNumber(trace.post_merge_candidate_count) }}</strong>
            </div>
            <div class="metric-cell">
              <span>最终证据</span>
              <strong>{{ chunks.length }}</strong>
            </div>
          </div>
        </section>

        <section class="trace-path-section">
          <div class="panel-section-title">
            <span>检索路径</span>
            <small>{{ trace.retrieval_stage || 'initial' }}</small>
          </div>
          <div class="trace-path">
            <span
              v-for="(attempt, index) in trace.retrieval_attempts || []"
              :key="`${attempt.mode}-${index}`"
              :class="['path-chip', attempt.status?.includes('error') ? 'failed' : 'passed']"
            >
              {{ attempt.mode }}
              <i :class="attempt.status?.includes('error') ? 'fas fa-xmark' : 'fas fa-check'"></i>
            </span>
            <span v-if="!(trace.retrieval_attempts || []).length" class="path-chip neutral">
              {{ trace.retrieval_mode || '未记录' }}
            </span>
          </div>
        </section>

        <section class="result-section">
          <div class="panel-section-title">
            <span>检索结果 Top {{ Math.min(chunks.length, 5) }}</span>
            <small>{{ trace.rerank_applied ? '已重排' : 'RRF / 原始排名' }}</small>
          </div>
          <div v-if="chunks.length" class="retrieval-result-list">
            <article
              v-for="(chunk, index) in chunks.slice(0, 5)"
              :key="`${chunk.filename}-${chunk.page_number}-${index}`"
              class="retrieval-result"
            >
              <div class="result-rank">{{ index + 1 }}</div>
              <div class="result-copy">
                <div class="result-title-row">
                  <strong>{{ chunk.filename || '未命名来源' }}</strong>
                  <span v-if="scoreLabel(chunk)" class="result-score">{{ scoreLabel(chunk) }}</span>
                </div>
                <p>{{ excerpt(chunk.text) }}</p>
                <div class="result-tags">
                  <span v-if="chunk.page_number">第 {{ chunk.page_number }} 页</span>
                  <span v-if="chunk.rrf_rank">RRF #{{ chunk.rrf_rank }}</span>
                  <span>{{ trace.retrieval_mode || 'hybrid' }}</span>
                </div>
              </div>
            </article>
          </div>
          <div v-else class="inline-empty">本轮没有召回可展示的证据。</div>
        </section>
      </div>

      <div v-else-if="activeTab === 'rerank'" class="retrieval-content">
        <section class="status-block">
          <div class="status-icon"><i class="fas fa-arrow-down-wide-short"></i></div>
          <div>
            <span>Rerank 配置</span>
            <strong>{{ trace.rerank_enabled ? '已配置' : '未配置' }}</strong>
          </div>
        </section>
        <div class="definition-list">
          <div><span>执行状态</span><strong>{{ trace.rerank_applied ? '已执行' : '未执行' }}</strong></div>
          <div><span>模型</span><strong>{{ trace.rerank_model || '未记录' }}</strong></div>
          <div><span>输入候选</span><strong>{{ displayNumber(trace.candidate_count) }}</strong></div>
          <div><span>输出 Top-K</span><strong>{{ displayNumber(trace.retrieval_top_k) }}</strong></div>
        </div>
        <div v-if="trace.rerank_error" class="panel-warning">
          <i class="fas fa-circle-exclamation"></i>
          <span>{{ trace.rerank_error }}</span>
        </div>
      </div>

      <div v-else class="retrieval-content">
        <section class="status-block">
          <div class="status-icon"><i class="fas fa-share-nodes"></i></div>
          <div>
            <span>Neo4j 医疗知识图谱</span>
            <strong>{{ trace.kg_available === false ? '降级不可用' : '已接入' }}</strong>
          </div>
        </section>
        <div class="definition-list">
          <div><span>结构化证据</span><strong>{{ displayNumber(trace.kg_hit_count) }} 条</strong></div>
          <div><span>医学意图</span><strong>{{ trace.medical_intent || '未识别' }}</strong></div>
          <div><span>情景记忆</span><strong>{{ trace.memory_hits?.episodic_memory ?? 0 }} 条</strong></div>
          <div><span>语义记忆</span><strong>{{ trace.memory_hits?.semantic_memory ?? 0 }} 条</strong></div>
        </div>
        <div v-if="entities.length" class="entity-strip">
          <span v-for="entity in entities" :key="entity">{{ entity }}</span>
        </div>
        <div v-if="trace.kg_vector_conflict_detected || trace.conflict_detected" class="panel-warning">
          <i class="fas fa-triangle-exclamation"></i>
          <span>{{ trace.kg_vector_conflict_summary || trace.conflict_summary }}</span>
        </div>
      </div>

      <div class="safety-summary">
        <div class="safety-summary-icon">
          <i class="fas fa-shield-heart"></i>
        </div>
        <div>
          <strong>医疗安全边界</strong>
          <p>{{ safetyLabel }}</p>
        </div>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useChatStore } from '@/stores/chat';
import type { RetrievedChunk } from '@/types/chat';

const chatStore = useChatStore();
const activeTab = ref<'retrieval' | 'rerank' | 'knowledge'>('retrieval');
const tabs = [
  { key: 'retrieval' as const, label: '检索结果' },
  { key: 'rerank' as const, label: '重排状态' },
  { key: 'knowledge' as const, label: '知识回溯' },
];

const lastAssistantMessage = computed(() =>
  [...chatStore.messages].reverse().find((message) => !message.isUser && message.ragTrace)
);
const trace = computed(() => lastAssistantMessage.value?.ragTrace || null);
const chunks = computed(() => trace.value?.retrieved_chunks || []);
const entities = computed(() => trace.value?.medical_ner?.matched_terms || []);
const modeLabel = computed(() =>
  trace.value?.retrieval_mode ? trace.value.retrieval_mode.replaceAll('_', ' ') : '未记录'
);
const safetyLabel = computed(() => {
  if (!trace.value?.safety_guard_enabled) return '本轮未记录安全检测状态';
  const flags = [];
  if (trace.value.high_risk_medical) flags.push('高风险医学提示');
  if (trace.value.dosage_guard_triggered) flags.push('药品剂量边界');
  if (trace.value.privacy_redaction_applied) flags.push('隐私已脱敏');
  return flags.length ? flags.join(' / ') : '未触发高风险规则';
});

const displayNumber = (value?: number | null) =>
  value === null || value === undefined ? '-' : value;

const excerpt = (text?: string) => {
  if (!text) return '该结果未返回文本摘要。';
  return text.length > 96 ? `${text.slice(0, 96)}...` : text;
};

const scoreLabel = (chunk: RetrievedChunk) => {
  if (chunk.rerank_score !== null && chunk.rerank_score !== undefined) {
    return `Rerank ${Number(chunk.rerank_score).toFixed(3)}`;
  }
  if (chunk.score !== null && chunk.score !== undefined) {
    return `Score ${Number(chunk.score).toFixed(3)}`;
  }
  return '';
};
</script>
