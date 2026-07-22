export interface ObservabilitySummary {
  window: { hours: number; start_at: string; end_at: string };
  chat: {
    trace_turns: number;
    evidence_states: Record<string, number>;
    actions: Record<string, number>;
    retrieval_modes: Record<string, number>;
    fallback_count: number;
    fallback_rate: number | null;
    high_risk_count: number;
    no_evidence_count: number;
  };
  tools: {
    call_count: number;
    success_count: number;
    failure_count: number;
    success_rate: number | null;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
  };
  quality: {
    ragas_context_relevance: number | null;
    ragas_faithfulness: number | null;
    ragas_answer_relevance: number | null;
    ragas_quality_score: number | null;
    mode: string;
    is_official_ragas: boolean;
  };
  tasks: {
    run_count: number;
    status_counts: Record<string, number>;
    retried_run_count: number;
    retry_rate: number | null;
  };
  notifications: {
    count: number;
    status_counts: Record<string, number>;
    external_delivery_count: number;
    delivery_status_counts: Record<string, number>;
    delivery_channel_counts: Record<string, number>;
  };
  privacy: { contains_prompt_text: boolean; contains_patient_identifiers: boolean };
}
