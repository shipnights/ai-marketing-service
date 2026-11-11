/**
 * Projects API module — CRUD, status transitions, and website analysis.
 */

import { apiCall, type ApiResponse } from "./api-client";

export type { ApiResponse };

// ============================================================================
// Types
// ============================================================================

export type ProjectStatus = "draft" | "active" | "archived";

export interface Project {
  project_id: string;
  team_id: string;
  name: string;
  description?: string;
  platforms: string[];
  website_url?: string;
  brand_voice?: string;
  status: ProjectStatus;
  created_by?: string;
  created_at: string;
  updated_at: string;
}

export interface CreateProjectRequest {
  name: string;
  description?: string;
  platforms?: string[];
  website_url?: string;
  brand_voice?: string;
}

export interface UpdateProjectRequest {
  name?: string;
  description?: string;
  platforms?: string[];
  website_url?: string;
  brand_voice?: string;
}

// ============================================================================
// API functions
// ============================================================================

export async function listProjects(status?: ProjectStatus) {
  const qs = status ? `?status=${status}` : "";
  return apiCall<Project[]>(`/api/v1/projects${qs}`);
}

export async function getProject(projectId: string) {
  return apiCall<Project>(`/api/v1/projects/${projectId}`);
}

export async function createProject(data: CreateProjectRequest) {
  return apiCall<Project>("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateProject(projectId: string, data: UpdateProjectRequest) {
  return apiCall<Project>(`/api/v1/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function activateProject(projectId: string) {
  return apiCall<Project>(`/api/v1/projects/${projectId}/activate`, {
    method: "POST",
  });
}

export async function archiveProject(projectId: string) {
  return apiCall<Project>(`/api/v1/projects/${projectId}/archive`, {
    method: "POST",
  });
}
