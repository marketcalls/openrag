// This is the only module that reaches into generated schema component names.
// Consumers depend on these stable aliases when the backend schema evolves.
import type { components } from './schema';

export type UserOut = components['schemas']['UserOut'];
export type InvitationCreate = components['schemas']['InvitationCreate'];
export type PermissionCatalogOut = components['schemas']['PermissionCatalogOut'];
export type PermissionCode = PermissionCatalogOut['code'];
export type RoleBindingReplace = components['schemas']['RoleBindingReplace'];
export type RoleCreate = components['schemas']['RoleCreate'];
export type RoleOut = components['schemas']['RoleOut'];
export type RolePatch = components['schemas']['RolePatch'];
export type WorkspaceOut = components['schemas']['WorkspaceOut'];
export type WorkspaceMemberOut = components['schemas']['WorkspaceMemberOut'];
export type DocumentOut = components['schemas']['DocumentOut'];
export type ChatOut = components['schemas']['ChatOut'];
export type ChatDetailOut = components['schemas']['ChatTreeOut'];
export type MessageOut = components['schemas']['MessageNode'];
export type ModelCreate = components['schemas']['ModelCreate'];
export type ModelOut = components['schemas']['ModelOut'];
export type ModelPatch = components['schemas']['ModelPatch'];
export type ModelProbeOut = components['schemas']['ModelProbeOut'];
export type ModelPublic = components['schemas']['ModelPublic'];
export type ReasoningEffort = ModelPublic['default_reasoning_effort'];
export type EmbeddingProfileCreate = components['schemas']['EmbeddingProfileCreate'];
export type EmbeddingProfileOut = components['schemas']['EmbeddingProfileOut'];
export type EmbeddingProfilePatch = components['schemas']['EmbeddingProfilePatch'];
export type EmbeddingDeploymentCreate = components['schemas']['EmbeddingDeploymentCreate'];
export type EmbeddingDeploymentOut = components['schemas']['EmbeddingDeploymentOut'];
export type RagOperationsOverview = components['schemas']['RagOperationsOverview'];
export type RagOperationsSeriesPoint = components['schemas']['RagOperationsSeriesPoint'];
export type RagOperationsRunOut = components['schemas']['RagOperationsRunOut'];
export type RagOperationsRunPage = components['schemas']['RagOperationsRunPage'];
export type RagOperationsErrorPage = components['schemas']['RagOperationsErrorPage'];
export type RagOperationsErrorDetail = components['schemas']['RagOperationsErrorDetail'];
export type ErrorIssueOut = components['schemas']['ErrorIssueOut'];
export type EvaluationCaseCreate = components['schemas']['EvaluationCaseCreate'];
export type EvaluationCaseOut = components['schemas']['EvaluationCaseOut'];
export type EvaluationCaseResultOut = components['schemas']['EvaluationCaseResultOut'];
export type EvaluationDatasetCreate = components['schemas']['EvaluationDatasetCreate'];
export type EvaluationDatasetOut = components['schemas']['EvaluationDatasetOut'];
export type EvaluationDatasetVersionCreate = components['schemas']['EvaluationDatasetVersionCreate'];
export type EvaluationDatasetVersionDetail = components['schemas']['EvaluationDatasetVersionDetail'];
export type EvaluationDatasetVersionOut = components['schemas']['EvaluationDatasetVersionOut'];
export type EvaluationPolicyOut = components['schemas']['EvaluationPolicyOut'];
export type EvaluationPolicyUpsert = components['schemas']['EvaluationPolicyUpsert'];
export type EvaluationRunCreate = components['schemas']['EvaluationRunCreate'];
export type EvaluationRunDetail = components['schemas']['EvaluationRunDetail'];
export type EvaluationRunOut = components['schemas']['EvaluationRunOut'];
export type MemoryCreate = components['schemas']['MemoryCreate'];
export type MemoryOut = components['schemas']['MemoryOut'];
export type MemoryPageOut = components['schemas']['MemoryPageOut'];
export type MemoryPatch = components['schemas']['MemoryPatch'];
export type MemoryPreferenceOut = components['schemas']['MemoryPreferenceOut'];
export type MemoryPreferencePatch = components['schemas']['MemoryPreferencePatch'];

export type DocumentStatus = DocumentOut['status'];
export type ChatRoute = 'direct' | 'conversation' | 'rag' | 'analytics' | 'clarify';

export interface RagOperationsFilters {
  from: string;
  to: string;
  route?: 'direct' | 'conversation' | 'rag' | 'analytics' | 'clarify' | 'unknown';
  outcome?: 'grounded' | 'conversational' | 'no_answer' | 'failed' | 'cancelled';
  org_id?: string;
  workspace_id?: string;
  model_id?: string;
  environment?: string;
  release?: string;
}

export interface SourceRef {
  marker: number;
  document_id: string;
  filename: string;
  page: number;
  chunk_index: number;
  score: number;
  snippet: string;
  document_version_id?: string | null;
  evidence_span_id?: string | null;
  version_label?: string | null;
  section_label?: string | null;
  section_path?: string[] | null;
  locator_kind?: string | null;
  locator_label?: string | null;
  content_hash?: string | null;
  dense_score?: number | null;
  sparse_score?: number | null;
  fused_score?: number | null;
  rerank_score?: number | null;
}

export interface CitationRef {
  marker: number;
  document_id: string;
  chunk_ref: string;
  page: number;
  score: number;
  document_version_id?: string | null;
  evidence_span_id?: string | null;
  document_name?: string | null;
  version_label?: string | null;
  section_label?: string | null;
  section_path?: string[] | null;
  locator_kind?: string | null;
  locator_label?: string | null;
  content_hash?: string | null;
  dense_score?: number | null;
  sparse_score?: number | null;
  fused_score?: number | null;
  rerank_score?: number | null;
}

export interface DoneInfo {
  message_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  no_answer: boolean;
}
