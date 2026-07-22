export interface PatientDocument {
  document_id: string;
  filename: string;
  file_type: string;
  status: string;
  created_at: string;
  updated_at: string;
  chunks_processed: number;
  fact_candidate_count: number;
  fact_extraction_status: string;
  fact_extraction_method: string;
  fact_extraction_warning: string;
}

export interface FactCandidate {
  candidate_id: string;
  document_id: string;
  resource_type: string;
  display: string;
  value: Record<string, unknown>;
  clinical_status: string;
  effective_start: string | null;
  effective_end: string | null;
  time_precision: string;
  confidence: number;
  source_page: number | null;
  source_chunk_id: string | null;
  evidence_text: string;
  extraction_method: string;
  status: 'pending' | 'confirmed' | 'rejected';
  confirmed_fact_id: string | null;
  created_at: string;
}

export interface PatientFact {
  fact_id: string;
  resource_type: string;
  display: string;
  value: Record<string, unknown>;
  clinical_status: string;
  verification_status: string;
  confidence: number;
  effective_start: string | null;
  effective_end: string | null;
  source_type: string;
  source_document_id: string | null;
  source_page: number | null;
  source_chunk_id: string | null;
}

export interface TimelineEvent {
  event_id: string;
  event_type: string;
  title: string;
  summary: string;
  effective_at: string;
  effective_end: string | null;
  recorded_at: string;
  time_precision: string;
  verification_status: string;
  source_fact_id: string | null;
  source_document_id: string | null;
  source_page: number | null;
  source_chunk_id: string | null;
}

export interface HealthTask {
  task_id: string;
  task_type: string;
  title: string;
  description: string;
  status: string;
  due_at: string;
  next_run_at: string | null;
  timezone: string;
  interval_seconds: number | null;
  confirmation_required: boolean;
}
