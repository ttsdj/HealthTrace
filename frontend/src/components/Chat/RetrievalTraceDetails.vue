<template>
  <div v-if="msg.ragTrace" class="message-meta">
    <details class="reasoning-details">
      <summary>检索过程</summary>
      <div class="reasoning-content">
        <div class="trace-line">
          工具：{{ msg.ragTrace.tool_used ? msg.ragTrace.tool_name : '未使用' }}
        </div>
        <div v-if="msg.ragTrace.retrieval_stage" class="trace-line">
          检索阶段：{{ msg.ragTrace.retrieval_stage }}
        </div>
        <div v-if="msg.ragTrace.grade_score" class="trace-line">
          相关性评分：{{ msg.ragTrace.grade_score }}
        </div>
        <div v-if="msg.ragTrace.grade_route" class="trace-line">
          评分决策：{{ msg.ragTrace.grade_route }}
        </div>
        <div v-if="msg.ragTrace.rewrite_needed !== null && msg.ragTrace.rewrite_needed !== undefined" class="trace-line">
          是否需要重写：{{ msg.ragTrace.rewrite_needed ? '是' : '否' }}
        </div>
        <div v-if="msg.ragTrace.rewrite_strategy" class="trace-line">
          重写策略：{{ msg.ragTrace.rewrite_strategy }}
        </div>
        <div v-if="msg.ragTrace.rewrite_query" class="trace-line">
          重写查询：{{ msg.ragTrace.rewrite_query }}
        </div>
        <div v-if="msg.ragTrace.retrieval_pipeline" class="trace-line">
          检索流水线：{{ msg.ragTrace.retrieval_pipeline }}
        </div>
        <div v-if="msg.ragTrace.retrieval_mode" class="trace-line">
          检索模式：{{ msg.ragTrace.retrieval_mode }}
        </div>
        <div v-if="msg.ragTrace.kg_available !== undefined" class="trace-line trace-kg">
          Neo4j知识图谱：{{ msg.ragTrace.kg_available ? '可用' : '不可用' }}，
          命中 {{ msg.ragTrace.kg_hit_count ?? 0 }} 条结构化证据
        </div>
        <div v-if="msg.ragTrace.medical_intent" class="trace-line trace-kg">
          医学意图：{{ msg.ragTrace.medical_intent }}
          <span v-if="msg.ragTrace.medical_ner?.matched_terms?.length">
            ，实体：{{ msg.ragTrace.medical_ner.matched_terms.join('、') }}
          </span>
        </div>
        <div v-if="msg.ragTrace.memory_hits" class="trace-line trace-memory">
          记忆命中：
          情景 {{ msg.ragTrace.memory_hits.episodic_memory ?? 0 }} 条，
          语义 {{ msg.ragTrace.memory_hits.semantic_memory ?? 0 }} 条
        </div>
        <div v-if="msg.ragTrace.retrieval_attempts && msg.ragTrace.retrieval_attempts.length" class="trace-line trace-funnel">
          降级链路：
          <span v-for="(attempt, aIdx) in msg.ragTrace.retrieval_attempts" :key="aIdx">
            {{ attempt.mode }}({{ attempt.status }})<span v-if="aIdx < msg.ragTrace.retrieval_attempts.length - 1"> → </span>
          </span>
        </div>
        <div v-if="msg.ragTrace.evidence_compression_applied !== null && msg.ragTrace.evidence_compression_applied !== undefined" class="trace-line trace-compression">
          证据压缩：{{ msg.ragTrace.evidence_compression_applied ? '已压缩' : '未触发' }}
          <span v-if="msg.ragTrace.evidence_original_chars">
            ，{{ msg.ragTrace.evidence_original_chars }} → {{ msg.ragTrace.evidence_compressed_chars }} 字
          </span>
        </div>
        <div v-if="msg.ragTrace.history_compression_enabled" class="trace-line trace-compression">
          历史压缩：保留 {{ msg.ragTrace.history_kept_messages }} / {{ msg.ragTrace.history_total_messages }} 条消息
        </div>
        <div v-if="msg.ragTrace.safety_guard_enabled" class="trace-line trace-safety">
          安全边界：
          <span v-if="msg.ragTrace.high_risk_medical">高风险医学描述</span>
          <span v-if="msg.ragTrace.dosage_guard_triggered"> 药品剂量问题</span>
          <span v-if="msg.ragTrace.privacy_redaction_applied"> 隐私已脱敏</span>
          <span v-if="!msg.ragTrace.high_risk_medical && !msg.ragTrace.dosage_guard_triggered && !msg.ragTrace.privacy_redaction_applied">未触发</span>
        </div>
        <div v-if="msg.ragTrace.conflict_detected" class="trace-line trace-conflict">
          证据冲突提示：{{ msg.ragTrace.conflict_summary }}
        </div>
        <div v-if="msg.ragTrace.kg_vector_conflict_detected" class="trace-line trace-conflict">
          KG/向量限制提示：{{ msg.ragTrace.kg_vector_conflict_summary }}
        </div>
        <div v-if="msg.ragTrace.candidate_k !== null && msg.ragTrace.candidate_k !== undefined" class="trace-line">
          {{ formatCandidateKLabel(msg.ragTrace) }}
        </div>
        <div v-if="hasRetrievalFunnel(msg.ragTrace)" class="trace-line trace-funnel">
          检索漏斗：Milvus 召回 {{ msg.ragTrace.recall_count ?? '—' }}
          → 合并后 {{ msg.ragTrace.post_merge_candidate_count ?? '—' }}
          → 精排输入 {{ msg.ragTrace.candidate_count ?? '—' }}
          → 输出 top{{ msg.ragTrace.retrieval_top_k ?? (msg.ragTrace.retrieved_chunks || []).length }}
          （{{ (msg.ragTrace.retrieved_chunks || []).length }} 条）
        </div>
        <div v-if="msg.ragTrace.leaf_retrieve_level" class="trace-line">
          叶子召回层级：L{{ msg.ragTrace.leaf_retrieve_level }}
        </div>
        <div v-if="msg.ragTrace.auto_merge_enabled !== null && msg.ragTrace.auto_merge_enabled !== undefined" class="trace-line">
          Auto-merging启用：{{ msg.ragTrace.auto_merge_enabled ? '是' : '否' }}
        </div>
        <div v-if="msg.ragTrace.auto_merge_applied !== null && msg.ragTrace.auto_merge_applied !== undefined" class="trace-line">
          Auto-merging应用：{{ msg.ragTrace.auto_merge_applied ? '是' : '否' }}
        </div>
        <div v-if="msg.ragTrace.auto_merge_threshold" class="trace-line">
          合并阈值：{{ msg.ragTrace.auto_merge_threshold }}
        </div>
        <div v-if="msg.ragTrace.auto_merge_replaced_chunks" class="trace-line">
          合并替换片段：{{ msg.ragTrace.auto_merge_replaced_chunks }}
        </div>
        <div v-if="msg.ragTrace.auto_merge_steps" class="trace-line">
          合并轮次：{{ msg.ragTrace.auto_merge_steps }}
        </div>
        <div v-if="msg.ragTrace.rerank_enabled !== null && msg.ragTrace.rerank_enabled !== undefined" class="trace-line">
          Rerank已配置：{{ msg.ragTrace.rerank_enabled ? '是' : '否' }}
        </div>
        <div v-if="msg.ragTrace.rerank_applied !== null && msg.ragTrace.rerank_applied !== undefined" class="trace-line">
          Rerank已执行：{{ msg.ragTrace.rerank_applied ? '是' : '否' }}
        </div>
        <div v-if="msg.ragTrace.rerank_model" class="trace-line">
          Rerank模型：{{ msg.ragTrace.rerank_model }}
        </div>
        <div v-if="msg.ragTrace.rerank_error" class="trace-line">
          Rerank状态：{{ msg.ragTrace.rerank_error }}
        </div>
        <div v-if="msg.ragTrace.expansion_type" class="trace-line">
          扩展策略：{{ msg.ragTrace.expansion_type }}
        </div>
        <div v-if="msg.ragTrace.step_back_question" class="trace-line">
          退步问题：{{ msg.ragTrace.step_back_question }}
        </div>
        <div v-if="msg.ragTrace.expanded_query" class="trace-line">
          扩展查询：{{ msg.ragTrace.expanded_query }}
        </div>
        <div v-if="msg.ragTrace.hypothetical_doc" class="trace-line">
          HyDE 文档：{{ msg.ragTrace.hypothetical_doc }}
        </div>
        
        <!-- 复杂度路由信息 -->
        <div v-if="msg.ragTrace.complexity" class="trace-line trace-complexity">
          问题复杂度：
          <span :class="msg.ragTrace.complexity === 'complex' ? 'tag-complex' : 'tag-simple'">
            {{ msg.ragTrace.complexity === 'complex' ? '复杂' : '简单' }}
          </span>
          <span v-if="msg.ragTrace.complexity_reason" class="trace-detail">（{{ msg.ragTrace.complexity_reason }}）</span>
        </div>
        
        <div v-if="msg.ragTrace.sub_questions && msg.ragTrace.sub_questions.length" class="trace-line">
          <div class="trace-sub-questions">
            <div class="trace-sub-qs-header">子问题分解（{{ msg.ragTrace.sub_questions.length }} 个）：</div>
            <ul class="trace-sub-qs-list">
              <li v-for="(sq, sqIdx) in msg.ragTrace.sub_questions" :key="sqIdx" class="trace-sub-q-item">
                <span class="trace-sub-q-index">{{ sqIdx + 1 }}.</span> {{ sq }}
              </li>
            </ul>
          </div>
        </div>
        <div v-if="msg.ragTrace.sub_agent_count" class="trace-line">
          并行子 Agent 数量：{{ msg.ragTrace.sub_agent_count }}
        </div>
        <div v-if="msg.ragTrace.synthesis_merged_count" class="trace-line">
          合成合并文档数：{{ msg.ragTrace.synthesis_merged_count }}
        </div>
        
        <!-- 子 Agent 检索详情（可折叠） -->
        <div v-if="msg.ragTrace.sub_traces && msg.ragTrace.sub_traces.length" class="trace-sub-traces">
          <details class="sub-traces-details">
            <summary class="sub-traces-title">
              <i class="fas fa-layer-group"></i> 子 Agent 检索详情（{{ msg.ragTrace.sub_traces.length }} 个）
            </summary>
            <div v-for="(st, stIdx) in msg.ragTrace.sub_traces" :key="stIdx" class="sub-trace-block">
              <div class="sub-trace-header">
                子问题 {{ stIdx + 1 }}：{{ msg.ragTrace.sub_questions?.[stIdx] || st.query || '—' }}
              </div>
              <div v-if="st.retrieval_stage" class="trace-line">检索阶段：{{ st.retrieval_stage }}</div>
              <div v-if="st.rewrite_strategy" class="trace-line">重写策略：{{ st.rewrite_strategy }}</div>
              <div v-if="st.retrieval_mode" class="trace-line">检索模式：{{ st.retrieval_mode }}</div>
              <div v-if="st.grade_score" class="trace-line">相关性评分：{{ st.grade_score }}</div>
              <div v-if="st.retrieved_chunks && st.retrieved_chunks.length" class="sub-trace-chunks">
                检索到 {{ st.retrieved_chunks.length }} 个片段
              </div>
            </div>
          </details>
        </div>
        
        <div v-if="msg.ragTrace.initial_retrieved_chunks && msg.ragTrace.initial_retrieved_chunks.length" class="sources">
          <div class="sources-title">初次检索结果</div>
          <ul class="sources-list">
            <li v-for="(chunk, sIndex) in msg.ragTrace.initial_retrieved_chunks" :key="sIndex" class="source-item">
              <div class="source-title-line">
                <span class="source-file">{{ chunk.filename }}</span>
                <span v-if="chunk.page_number" class="source-page">（第 {{ chunk.page_number }} 页）</span>
              </div>
              <div class="source-meta-line">
                <span class="source-page">RRF名次：#{{ chunk.rrf_rank || (sIndex + 1) }}</span>
                <span v-if="chunk.rerank_score !== null && chunk.rerank_score !== undefined" class="source-page">
                  Rerank分数：{{ Number(chunk.rerank_score).toFixed(4) }}
                </span>
              </div>
              <div v-if="chunk.text" class="source-excerpt">{{ chunk.text }}</div>
            </li>
          </ul>
        </div>
        
        <div v-if="msg.ragTrace.expanded_retrieved_chunks && msg.ragTrace.expanded_retrieved_chunks.length" class="sources">
          <div class="sources-title">重写后检索结果</div>
          <ul class="sources-list">
            <li v-for="(chunk, sIndex) in msg.ragTrace.expanded_retrieved_chunks" :key="sIndex" class="source-item">
              <div class="source-title-line">
                <span class="source-file">{{ chunk.filename }}</span>
                <span v-if="chunk.page_number" class="source-page">（第 {{ chunk.page_number }} 页）</span>
              </div>
              <div class="source-meta-line">
                <span class="source-page">RRF名次：#{{ chunk.rrf_rank || (sIndex + 1) }}</span>
                <span v-if="chunk.rerank_score !== null && chunk.rerank_score !== undefined" class="source-page">
                  Rerank分数：{{ Number(chunk.rerank_score).toFixed(4) }}
                </span>
              </div>
              <div v-if="chunk.text" class="source-excerpt">{{ chunk.text }}</div>
            </li>
          </ul>
        </div>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import type { Message, RagTrace } from '@/types/chat';

defineProps<{
  msg: Message;
}>();

const formatCandidateKLabel = (trace: RagTrace) => {
  if (!trace || trace.candidate_k === null || trace.candidate_k === undefined) {
    return '';
  }
  const k = trace.candidate_k;
  if (trace.candidate_k_config_error) {
    return `Milvus 候选池：${k}（${trace.candidate_k_config_error}，已回退倍数计算）`;
  }
  if (trace.candidate_k_source === 'env') {
    return `Milvus 候选池：${k}（环境变量 RETRIEVAL_CANDIDATE_K）`;
  }
  const multiplier = trace.retrieval_candidate_multiplier;
  if (multiplier !== null && multiplier !== undefined) {
    return `Milvus 候选池：${k}（top_k × ${multiplier}）`;
  }
  return `Milvus 候选池：${k}`;
};

const hasRetrievalFunnel = (trace: RagTrace) => {
  if (!trace) {
    return false;
  }
  return trace.recall_count !== null && trace.recall_count !== undefined
    || trace.post_merge_candidate_count !== null && trace.post_merge_candidate_count !== undefined
    || trace.candidate_count !== null && trace.candidate_count !== undefined;
};
</script>
