export interface RetrievedChunk {
  filename: string;
  page_number?: number;
  rrf_rank?: number;
  rerank_score?: number | null;
  score?: number | null;
  text?: string;
}

export interface RequestLocation {
  authorized: boolean;
  latitude: number;
  longitude: number;
  accuracy_meters?: number;
}

export interface RagTrace {
  tool_used?: boolean;
  tool_name?: string;
  retrieval_stage?: string;
  grade_score?: number;
  grade_route?: string;
  rewrite_needed?: boolean;
  rewrite_strategy?: string;
  rewrite_query?: string;
  retrieval_pipeline?: string;
  retrieval_mode?: string;
  retrieval_attempts?: Array<{ mode: string; status: string; raw_count?: number; error?: string }>;
  retrieval_degraded?: boolean;
  retrieval_failure_reason?: string;
  candidate_k?: number;
  candidate_k_config_error?: string;
  candidate_k_source?: string;
  retrieval_candidate_multiplier?: number;
  recall_count?: number | null;
  post_merge_candidate_count?: number | null;
  candidate_count?: number | null;
  retrieval_top_k?: number;
  retrieved_chunks?: RetrievedChunk[];
  leaf_retrieve_level?: number;
  auto_merge_enabled?: boolean | null;
  auto_merge_applied?: boolean | null;
  auto_merge_threshold?: number;
  auto_merge_replaced_chunks?: number;
  auto_merge_steps?: number;
  rerank_enabled?: boolean | null;
  rerank_applied?: boolean | null;
  rerank_model?: string;
  rerank_error?: string;
  expansion_type?: string;
  step_back_question?: string;
  expanded_query?: string;
  hypothetical_doc?: string;
  complexity?: 'simple' | 'complex' | string;
  complexity_reason?: string;
  sub_questions?: string[];
  sub_agent_count?: number;
  synthesis_merged_count?: number;
  sub_traces?: any[];
  initial_retrieved_chunks?: RetrievedChunk[];
  expanded_retrieved_chunks?: RetrievedChunk[];
  kg_available?: boolean;
  kg_hit_count?: number;
  medical_intent?: string;
  medical_ner?: { matched_terms?: string[]; [key: string]: any };
  memory_hits?: Record<string, number>;
  fusion_mode?: string;
  conflict_detected?: boolean;
  conflict_summary?: string;
  kg_vector_conflict_detected?: boolean;
  kg_vector_conflict_summary?: string;
  context_compression_enabled?: boolean;
  evidence_compression_applied?: boolean;
  evidence_original_chars?: number;
  evidence_compressed_chars?: number;
  history_compression_enabled?: boolean;
  history_total_messages?: number;
  history_kept_messages?: number;
  safety_guard_enabled?: boolean;
  high_risk_medical?: boolean;
  high_risk_terms?: string[];
  dosage_guard_triggered?: boolean;
  dosage_terms?: string[];
  sensitive_confirmation_required?: boolean;
  safety_notice?: string;
  privacy_redaction_applied?: boolean;
  privacy_redaction_count?: number;
  care_navigation_required?: boolean;
  care_navigation_recommended?: boolean;
  care_navigation_reason?: string;
  recommended_department?: string;
  location_authorized?: boolean;
  care_navigation_result_count?: number;
  care_navigation_status?: string;
  ragas_context_relevance?: number | null;
  ragas_faithfulness?: number | null;
  ragas_answer_relevance?: number | null;
  ragas_quality_score?: number | null;
  ragas_evaluation_mode?: string;
  ragas_evaluation_note?: string;
}

export interface RagStep {
  key?: string;
  group?: string | null;
  label: string;
  icon?: string;
  detail?: string;
  status?: string;
  percent?: number;
  message?: string;
}

export interface GroupedRagStep {
  group: string | null;
  label: string | null;
  steps: RagStep[];
  collapsed: boolean;
}

export interface Message {
  text: string;
  isUser: boolean;
  isThinking?: boolean;
  ragTrace?: RagTrace | null;
  ragSteps?: RagStep[];
  _groupedSteps?: GroupedRagStep[];
}

export interface ChatSession {
  session_id: string;
  title?: string;
  message_count: number;
  updated_at: string;
}
