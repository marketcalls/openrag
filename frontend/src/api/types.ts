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
export type ModelPublic = components['schemas']['ModelPublic'];
export type EmbeddingProfileCreate = components['schemas']['EmbeddingProfileCreate'];
export type EmbeddingProfileOut = components['schemas']['EmbeddingProfileOut'];
export type EmbeddingProfilePatch = components['schemas']['EmbeddingProfilePatch'];

export type DocumentStatus = DocumentOut['status'];

export interface SourceRef {
  marker: number;
  document_id: string;
  filename: string;
  page: number;
  chunk_index: number;
  score: number;
  snippet: string;
}

export interface CitationRef {
  marker: number;
  document_id: string;
  chunk_ref: string;
  page: number;
  score: number;
}

export interface DoneInfo {
  message_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  no_answer: boolean;
}
