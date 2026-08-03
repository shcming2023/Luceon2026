import type { AxiosProgressEvent } from 'axios'
import api from './index'
import type {
  CodexSkillJob,
  MaterialBookMetadata,
  MaterialListResponse,
  MaterialArtifactCatalog,
  MaterialLineage,
  MaterialMetadataOptions,
  MetadataJob,
  MaterialSummary,
  MaterialSyncSummary,
  MaterialUploadResponse,
  PipelinePreflightResponse,
  PipelineRun,
  PipelineRunListResponse,
  PipelineStatusResponse
} from '@/types/material'

const UPLOAD_TIMEOUT_MS = 30 * 60 * 1000

export interface MaterialListParams {
  page: number
  page_size: number
  search?: string
  stage?: string
  metadata_status?: string
  subject?: string
  country?: string
  series?: string
  year_from?: number | null
  year_to?: number | null
}

export interface PipelineTarget {
  material_id?: string
  input_object?: string
  material_ids?: string[]
  input_objects?: string[]
  material_pks?: number[]
  reprocess_completed?: boolean
}

export interface CodexJobCreatePayload {
  mode?: string
  requested_skill?: string
  skill_version?: string
  run_reason?: string
  force?: boolean
  payload?: Record<string, unknown>
}

export interface WorkerV1Policy {
  mode: string
  batch_enabled: boolean
  auto_retry_enabled: boolean
  single_job_audit_enabled: boolean
}

export interface WorkflowV2JobSummary {
  id: string
  material_pk: string
  material_id: string
  filename: string
  review_asset_id: string
  source_popo_manifest: { bucket: string; object: string }
  current_output_id: string
  job_output_id: string
  current_material_output_id: string
  current_material_output: Record<string, any> | null
  outputs: Array<Record<string, any>>
  workflow_version: string
  is_current_workflow: boolean
  status: string
  business_status: string
  current_stage_key: string
  current_stage_status: string
  current_attempt: number
  last_successful_stage_key: string
  minimal_resume_stage_key: string
  deliverable_available: boolean
  stages: Array<{ key: string; attempt: number; status: string }>
  event_count: number
  artifact_count: number
  model_call_count: number
  open_finding_count: number
  error: { code: string; message: string }
  created_at: string | null
  updated_at: string | null
}

export interface WorkflowV2JobCreateResponse {
  created: boolean
  job: Record<string, any>
}

export const materialsApi = {
  getMaterials(params: MaterialListParams) {
    return api.get<MaterialListResponse>('/materials', { params }).then(res => res.data)
  },

  getSummary() {
    return api.get<MaterialSummary>('/materials/summary').then(res => res.data)
  },

  getMetadataOptions() {
    return api.get<MaterialMetadataOptions>('/materials/metadata/options').then(res => res.data)
  },

  getMetadata(materialId: string) {
    return api.get<MaterialBookMetadata>(`/materials/${materialId}/metadata`).then(res => res.data)
  },

  updateMetadata(materialId: string, payload: Partial<MaterialBookMetadata>) {
    return api.patch<MaterialBookMetadata>(`/materials/${materialId}/metadata`, payload).then(res => res.data)
  },

  extractMetadata(materialId: string, force = false) {
    return api
      .post<MaterialBookMetadata>(`/materials/${materialId}/metadata/extract`, { force }, { timeout: 120000 })
      .then(res => res.data)
  },

  createMetadataJob(materialId: string, force = false) {
    return api.post<MetadataJob>(`/materials/${materialId}/metadata/jobs`, { force }).then(res => res.data)
  },

  getMetadataJobs(materialId: string, limit = 50) {
    return api.get<{ jobs: MetadataJob[] }>(`/materials/${materialId}/metadata/jobs`, { params: { limit } }).then(res => res.data.jobs)
  },

  retryMetadataJob(materialId: string, jobId: string) {
    return api.post<MetadataJob>(`/materials/${materialId}/metadata/jobs/${jobId}/retry`).then(res => res.data)
  },

  sync(limit?: number) {
    return api.post<MaterialSyncSummary>('/materials/sync', null, { params: { limit } }).then(res => res.data)
  },

  upload(files: File[], onProgress?: (progress: number) => void) {
    const formData = new FormData()
    files.forEach(file => formData.append('files', file))
    return api.post<MaterialUploadResponse>('/materials/upload', formData, {
      timeout: UPLOAD_TIMEOUT_MS,
      onUploadProgress: (event: AxiosProgressEvent) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded * 100) / event.total))
        }
      }
    }).then(res => res.data)
  },

  getPipelineStatus() {
    return api.get<PipelineStatusResponse>('/materials/pipeline/status').then(res => res.data)
  },

  getPipelineRuns(params: { page: number; page_size: number; status?: string }) {
    return api.get<PipelineRunListResponse>('/materials/pipeline/runs', { params }).then(res => res.data)
  },

  getPipelineRun(runId: string) {
    return api.get<PipelineRun>(`/materials/pipeline/runs/${runId}`).then(res => res.data)
  },

  preflightPipeline(limit = 5, target: PipelineTarget = {}) {
    return api.post<PipelinePreflightResponse>('/materials/pipeline/preflight', { limit, ...target }, { timeout: 120000 }).then(res => res.data)
  },

  startPipeline(apply = false, limit = 5, target: PipelineTarget = {}) {
    return api.post<PipelineRun>('/materials/pipeline/start', { apply, limit, ...target }, { timeout: 120000 }).then(res => res.data)
  },

  preflightPopoResume(materialId: string) {
    return api.post<PipelinePreflightResponse>(`/materials/${materialId}/pipeline/resume-popo/preflight`, null, { timeout: 120000 }).then(res => res.data)
  },

  startPopoResume(materialId: string) {
    return api.post<PipelineRun>(`/materials/${materialId}/pipeline/resume-popo/start`, null, { timeout: 120000 }).then(res => res.data)
  },

  getReviewTarget(materialId: string) {
    return api.get<{ review_asset_id: string; output_id?: string }>(`/materials/${materialId}/review_target`).then(res => res.data)
  },

  getDownloadUrl(materialId: string) {
    return api.get<{ url: string }>(`/materials/${materialId}/download_url`).then(res => res.data)
  },

  getContentUrl(materialId: string) {
    return `/api/materials/${materialId}/content`
  },

  getArtifactCatalog(materialId: string) {
    return api.get<MaterialArtifactCatalog>(`/materials/${materialId}/artifacts`).then(res => res.data)
  },

  getLineage(materialId: string) {
    return api.get<MaterialLineage>(`/materials/${materialId}/lineage`).then(res => res.data)
  },

  getArtifactDownloadUrl(materialId: string, artifactId: string) {
    return `/api/materials/${materialId}/artifacts/${encodeURIComponent(artifactId)}/download`
  },

  createCodexJob(materialId: string, payload: CodexJobCreatePayload) {
    return api.post<CodexSkillJob>(`/materials/${materialId}/codex-jobs`, payload).then(res => res.data)
  },

  getWorkerV1Policy() {
    return api.get<WorkerV1Policy>('/materials/codex-jobs/v1-policy').then(res => res.data)
  },

  getWorkflowV2JobSummaries(limit = 200) {
    return api.get<{ jobs: WorkflowV2JobSummary[] }>('/workflow-v2/job-summaries', { params: { limit } }).then(res => res.data.jobs)
  },

  getWorkflowV2JobSummaryPage(params: { page: number; page_size: number; status?: string }) {
    return api.get<{ total: number; page: number; page_size: number; jobs: WorkflowV2JobSummary[]; status_counts: { passed: number; blocked: number; running: number } }>('/workflow-v2/job-summaries', { params }).then(res => res.data)
  },

  getWorkflowV2Job(jobId: string) {
    return api.get<Record<string, any>>(`/workflow-v2/jobs/${jobId}`).then(res => res.data)
  },

  createWorkflowV2Job(materialId: string, source = 'files_row_action') {
    return api.post<WorkflowV2JobCreateResponse>(`/workflow-v2/materials/${materialId}/jobs`, {
      priority: 100,
      payload: { source }
    }).then(res => res.data)
  },

  createWorkflowV2JobsBatch(materialPks: number[], source = 'refinement_tasks_batch') {
    return api.post<Record<string, any>>('/workflow-v2/jobs/batch', {
      material_pks: materialPks,
      priority: 100,
      payload: { source }
    }).then(res => res.data)
  },

  runWorkflowV2Job(jobId: string) {
    return api.post<{ queued: boolean; message_id: string; job: Record<string, any> }>(`/workflow-v2/jobs/${jobId}/run`).then(res => res.data)
  },

  retryWorkflowV2Job(jobId: string) {
    return api.post<Record<string, any>>(`/workflow-v2/jobs/${jobId}/retry`).then(res => res.data)
  },

  restartWorkflowV2Job(jobId: string, stageKey: string) {
    return api.post<Record<string, any>>(`/workflow-v2/jobs/${jobId}/restart/${stageKey}`).then(res => res.data)
  },

  getWorkflowV2ReviewCandidate(jobId: string) {
    return api.get<Record<string, any>>(`/workflow-v2/jobs/${jobId}/review-candidate`).then(res => res.data)
  },

  handoffWorkflowV2ReviewCandidate(jobId: string) {
    return api.post<Record<string, any>>(`/workflow-v2/jobs/${jobId}/handoff`).then(res => res.data)
  },

  revalidateWorkflowV2ReviewCandidate(jobId: string) {
    return api.post<Record<string, any>>(`/workflow-v2/jobs/${jobId}/revalidate`).then(res => res.data)
  }
}
