from typing import List, Optional

from pydantic import BaseModel

from backend.schemas.care_navigation import RequestLocation


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default_session"
    location: Optional[RequestLocation] = None


class RetrievedChunk(BaseModel):
    filename: str
    page_number: Optional[str | int] = None
    text: Optional[str] = None
    score: Optional[float] = None
    rrf_rank: Optional[int] = None
    rerank_score: Optional[float] = None


class RagTrace(BaseModel):
    tool_used: Optional[bool] = None
    tool_name: Optional[str] = None
    query: Optional[str] = None
    expanded_query: Optional[str] = None
    step_back_question: Optional[str] = None
    step_back_answer: Optional[str] = None
    expansion_type: Optional[str] = None
    hypothetical_doc: Optional[str] = None
    retrieval_stage: Optional[str] = None
    grade_score: Optional[str] = None
    grade_route: Optional[str] = None
    rewrite_needed: Optional[bool] = None
    rewrite_strategy: Optional[str] = None
    rewrite_query: Optional[str] = None
    rerank_enabled: Optional[bool] = None
    rerank_applied: Optional[bool] = None
    rerank_model: Optional[str] = None
    rerank_endpoint: Optional[str] = None
    rerank_error: Optional[str] = None
    retrieval_mode: Optional[str] = None
    retrieval_pipeline: Optional[str] = None
    candidate_k: Optional[int] = None
    candidate_k_source: Optional[str] = None
    candidate_k_config_error: Optional[str] = None
    retrieval_candidate_multiplier: Optional[int] = None
    retrieval_top_k: Optional[int] = None
    recall_count: Optional[int] = None
    post_merge_candidate_count: Optional[int] = None
    candidate_count: Optional[int] = None
    leaf_retrieve_level: Optional[int] = None
    auto_merge_enabled: Optional[bool] = None
    auto_merge_applied: Optional[bool] = None
    auto_merge_threshold: Optional[int] = None
    auto_merge_replaced_chunks: Optional[int] = None
    auto_merge_steps: Optional[int] = None
    retrieved_chunks: Optional[List[RetrievedChunk]] = None
    initial_retrieved_chunks: Optional[List[RetrievedChunk]] = None
    expanded_retrieved_chunks: Optional[List[RetrievedChunk]] = None
    # 复杂度路由新增字段
    complexity: Optional[str] = None
    complexity_reason: Optional[str] = None
    sub_questions: Optional[List[str]] = None
    sub_agent_count: Optional[int] = None
    synthesis_merged_count: Optional[int] = None
    sub_traces: Optional[List[dict]] = None
    kg_available: Optional[bool] = None
    kg_hit_count: Optional[int] = None
    medical_intent: Optional[str] = None
    medical_ner: Optional[dict] = None
    memory_hits: Optional[dict] = None
    fusion_mode: Optional[str] = None
    conflict_detected: Optional[bool] = None
    conflict_summary: Optional[str] = None
    kg_vector_conflict_detected: Optional[bool] = None
    kg_vector_conflict_summary: Optional[str] = None
    context_compression_enabled: Optional[bool] = None
    evidence_compression_applied: Optional[bool] = None
    evidence_original_chars: Optional[int] = None
    evidence_compressed_chars: Optional[int] = None
    history_compression_enabled: Optional[bool] = None
    history_total_messages: Optional[int] = None
    history_kept_messages: Optional[int] = None
    safety_guard_enabled: Optional[bool] = None
    high_risk_medical: Optional[bool] = None
    high_risk_terms: Optional[List[str]] = None
    dosage_guard_triggered: Optional[bool] = None
    dosage_terms: Optional[List[str]] = None
    sensitive_confirmation_required: Optional[bool] = None
    safety_notice: Optional[str] = None
    privacy_redaction_applied: Optional[bool] = None
    privacy_redaction_count: Optional[int] = None
    care_navigation_required: Optional[bool] = None
    care_navigation_recommended: Optional[bool] = None
    care_navigation_reason: Optional[str] = None
    recommended_department: Optional[str] = None
    location_authorized: Optional[bool] = None
    care_navigation_result_count: Optional[int] = None
    care_navigation_status: Optional[str] = None
    ragas_context_relevance: Optional[float] = None
    ragas_faithfulness: Optional[float] = None
    ragas_answer_relevance: Optional[float] = None
    ragas_quality_score: Optional[float] = None
    ragas_evaluation_mode: Optional[str] = None
    ragas_evaluation_note: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    rag_trace: Optional[RagTrace] = None


class MessageInfo(BaseModel):
    type: str
    content: str
    timestamp: str
    rag_trace: Optional[RagTrace] = None


class SessionMessagesResponse(BaseModel):
    messages: List[MessageInfo]


class SessionInfo(BaseModel):
    session_id: str
    title: Optional[str] = None
    updated_at: str
    message_count: int


class SessionListResponse(BaseModel):
    sessions: List[SessionInfo]


class SessionDeleteResponse(BaseModel):
    session_id: str
    message: str
