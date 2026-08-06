export type MaterialStage =
  | 'input'
  | 'mineru_done'
  | 'popo_done'
  | 'latex_done'
  | 'raw_done'
  | 'clean_stale'
  | 'clean_done'
  | 'standard_done'
  | 'failed'

export type PipelineRunStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'idle' | string

export interface ObjectRef {
  bucket: string
  object: string
}

export interface CodexSkillJob {
  id: string
  material_pk?: string
  material_id?: string
  review_asset_id?: string
  mode: string
  status: string
  requested_skill: string
  skill_version: string
  attempt_count: number
  staging_dir: string
  error_message: string
  payload?: Record<string, unknown>
  result?: Record<string, unknown>
  source_popo_manifest?: ObjectRef
  baseline_manifest?: ObjectRef
  output_manifest: ObjectRef
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface MaterialItem {
  id: string
  material_id: string
  source_hash: string
  title: string
  filename: string
  book_metadata?: MaterialBookMetadata | null
  source_type: string
  input_bucket: string
  input_object: string
  input_sha256: string
  size: number
  page_count: number
  content_type: string
  stage_status: MaterialStage
  pipeline_status: string
  mineru_manifest: ObjectRef
  popo_manifest: ObjectRef
  latex_manifest: ObjectRef
  raw_manifest: ObjectRef
  clean_manifest: ObjectRef
  standard_manifest: ObjectRef
  mineru_available: boolean
  popo_available: boolean
  latex_available: boolean
  raw_available: boolean
  clean_available: boolean
  standard_available: boolean
  raw_dry_run_available?: boolean
  codex_job?: CodexSkillJob | null
  refinement_status: string
  refinement_output_status: string
  current_refinement_output?: {
    id: string
    output_run_id: string
    popo_run_id: string
    status: string
    quality_status: string
    is_current: boolean
  } | null
  latest_refinement_status: string
  latest_refinement_job?: {
    id: string
    status: string
    current_stage_key?: string
    created_at?: string | null
    updated_at?: string | null
  } | null
  latest_refinement_source: string
  mineru_run_id: string
  popo_run_id: string
  latex_run_id: string
  raw_run_id: string
  raw_dry_run_id?: string
  clean_run_id: string
  standard_run_id: string
  standard_quality_score: number | null
  review_asset_id: string
  ignored: boolean
  last_synced_at: string | null
  created_at: string | null
}

export interface MaterialListResponse {
  total: number
  page: number
  page_size: number
  materials: MaterialItem[]
}

export interface MaterialBookMetadata {
  id: string
  material_pk: string
  original_title: string
  publication_date: string
  publication_year: number | null
  edition: string
  subject: string
  publication_country: string
  series_name: string
  publisher: string
  isbn: string
  language: string
  grade_level: string
  status: string
  source: string
  confidence: number | null
  manual_override: boolean
  evidence: Array<Record<string, unknown>>
  sample: Record<string, unknown>
  extraction_model: string
  extraction_error: string
  extracted_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface MaterialMetadataOptions {
  subjects: string[]
  countries: string[]
  series: string[]
  publishers: string[]
  languages: string[]
  editions: string[]
}

export interface MaterialSyncSummary {
  total: number
  scanned: Record<string, number>
  stages: Record<MaterialStage | string, number>
  availability: Record<MaterialStage | string, number>
}

export interface PipelineRun {
  id: string
  status: PipelineRunStatus
  mode: string
  idempotency_key: string
  queue_slot: string
  current_stage: string
  total: number
  processed: number
  success: number
  failed: number
  progress_percent?: number
  progress_basis?: string
  summary: Record<string, unknown>
  request: Record<string, unknown>
  error_message: string
  worker_id: string
  attempt_count: number
  heartbeat_at: string | null
  lease_expires_at: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  items?: PipelineRunItem[]
  events?: PipelineEvent[]
}

export interface PipelineStageAttempt {
  id: string
  run_item_id: string
  stage: string
  attempt: number
  status: string
  external_batch_id: string
  external_run_id: string
  input_manifest: ObjectRef
  output_manifest: ObjectRef
  error_code: string
  error_message: string
  evidence: Record<string, unknown>
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface PipelineRunItem {
  id: string
  run_id: string
  material_pk: string
  material_id: string
  filename: string
  input: ObjectRef & { sha256: string }
  status: string
  current_stage: string
  mineru_run_id: string
  mineru_manifest: ObjectRef
  popo_run_id: string
  popo_manifest: ObjectRef
  error_code: string
  error_message: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  attempts: PipelineStageAttempt[]
  metadata_jobs: MetadataJob[]
}

export interface PipelineRunListResponse {
  total: number
  page: number
  page_size: number
  runs: PipelineRun[]
}

export interface MetadataJob {
  id: string
  material_pk: string
  material_id: string
  status: string
  idempotency_key: string
  source_manifest: ObjectRef
  source_manifest_sha256: string
  model: string
  prompt_version: string
  force: boolean
  attempt_count: number
  worker_id: string
  error_message: string
  result: Record<string, unknown>
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface MaterialArtifact {
  artifact_id: string
  kind: string
  stage: string
  label: string
  filename: string
  status: string
  verification_status: string
  run_id: string
  output_id: string
  current: boolean
  historical: boolean
  candidate: boolean
  frozen: boolean
  download_available: boolean
  size_bytes: number
  sha256: string
  etag: string
  content_type: string
  created_at: string
}

export interface MaterialArtifactCatalog {
  material: {
    material_pk: string
    material_id: string
    filename: string
    input_sha256: string
  }
  artifacts: MaterialArtifact[]
}

export interface MaterialLineage {
  material: MaterialItem
  pipeline_items: Array<PipelineRunItem & { run: PipelineRun | null }>
  metadata_jobs: MetadataJob[]
  workflow_jobs: Array<Record<string, any>>
  workflow_status: { available: boolean; error: string }
  outputs: Array<Record<string, any>>
  review: { asset_id: string; available: boolean }
}

export interface PipelineEvent {
  id: string
  run_id: string
  level: string
  stage: string
  message: string
  payload: Record<string, unknown>
  created_at: string | null
}

export interface MaterialSummary {
  total: number
  stages: Record<MaterialStage | string, number>
  availability: Record<MaterialStage | string, number>
  latest_run: PipelineRun | null
}

export interface PipelineStatusResponse {
  run: PipelineRun | null
  events: PipelineEvent[]
  audit?: Record<string, unknown> | null
}

export interface PipelinePreflightResponse {
  ready: boolean
  status: string
  checked_at: string
  limit: number
  gpu_ok: boolean
  staged_api_ok: boolean
  plan_status: string
  selected_count: number
  active_marker_count: number
  scheduler_state_counts: Record<string, number>
  health: Record<string, unknown>
  staged_api_probe: Record<string, unknown>
  plan: Record<string, unknown>
  returncode: number
  command_text: string
  resource_gate?: {
    applies: boolean
    ok: boolean
    status: string
    reason?: string
    large_pdf_count: number
    selected_input_bytes: number
    max_page_count: number
    artifact_limit_bytes?: number
    artifact_used_bytes?: number
    available_headroom_bytes?: number
    required_headroom_bytes?: number
    expansion_factor?: number
  }
  snapshot?: Array<{
    material_pk: number
    material_id: string
    filename: string
    input_bucket: string
    input_object: string
    input_sha256: string
    size_bytes: number
    page_count: number
  }>
}

export interface MaterialUploadResponse {
  upload_policy_sha256?: string
  total: number
  success: number
  failed: number
  duplicates?: number
  files: Array<{
    filename: string
    status: string
    eligibility_status?: 'upload_allowed' | 'uploaded_but_gpu_resource_review' | 'gpu_eligible' | 'rejected_by_config' | string
    error_message?: string
    material?: MaterialItem
  }>
}

export interface PdfUploadCapabilities {
  schema_version: string
  policy_sha256: string
  max_file_bytes: number
  max_file_pages: number
  max_request_bytes: number
  max_request_files: number
  max_gpu_batch_input_bytes: number
  max_gpu_batch_files: number
  large_pdf_threshold_bytes: number
  large_pdf_page_threshold: number
  expansion_factor: number
  min_local_temp_free_bytes: number
  min_gpu_headroom_bytes: number
  default_stage_timeout_seconds: number
  large_stage_timeout_seconds: number
  upload_chunk_bytes: number
  multipart_overhead_bytes: number
  temp_dir: string
  max_file_label: string
  actual_2gib_transfer_qualified: boolean
  internal_2gib_2000_profile_qualified: boolean
  internal_profile_gap: string[]
}
