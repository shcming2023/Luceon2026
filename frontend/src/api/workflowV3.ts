import api from './index'

export interface WorkflowV3Status {
  configured: boolean
  enabled: boolean
  ready: boolean
  execution_enabled: boolean
  detail: string
  workflow_version: string
  operations?: {
    blockers: string[]
    registered_release_count: number
    artifact_backend: {
      mode: string
      ready: boolean
      directory_mode_admitted: boolean
    }
    queues: {
      producer: number
      evaluation: number
      promotion: number
      projection: number
    }
    active_executions: number
    stale_executions: number
    workers: Record<string, {
      ready: boolean
      fresh_count: number
      workers: Array<Record<string, any>>
    }>
  } | null
}

export interface WorkflowV3Release {
  id: string
  release_version: string
  manifest_sha256: string
  package: {
    bucket: string
    object: string
    sha256: string
  }
  workflow_version: string
  template_sha256: string
  runtime_identity_sha256: string
  status: string
  created_at: string | null
}

export interface WorkflowV3Stage {
  id: string
  stage_key: string
  stage_version: string
  attempt: number
  generation: number
  owner: string
  machine_status: string
  spec_status: string
  input: {
    kind: string
    promotion_id: string
    sha256: string
  }
  promotion: {
    id: string
    candidate_id: string
    sha256: string
  }
  execution?: Record<string, any> | null
  candidates?: Array<{
    id: string
    artifact_kind: string
    sha256: string
    size_bytes: number
    status: string
    generation: number
    metadata?: Record<string, any>
  }>
  evaluations?: Array<{
    id: string
    candidate_id: string
    evaluator_identity: string
    evaluator_version: string
    decision: string
    spec_passed: boolean
    gate_results: Record<string, any>
    findings: Array<Record<string, any>>
    generation: number
  }>
  error: { code: string; message: string }
  started_at: string | null
  finished_at: string | null
}

export interface WorkflowV3Job {
  id: string
  user_id?: string
  material_pk: string
  material_id: string
  filename?: string
  review_asset_id?: string
  source_pdf_sha256?: string
  source_identity?: {
    verified: boolean
    errors: string[]
    filename: string
    material_pk: string
    material_id: string
    source_pdf_sha256: string
    source_pdf: {
      bucket: string
      object: string
      sha256: string
      size_bytes?: number
    }
    popo_manifest: {
      bucket: string
      object: string
      sha256: string
    }
    review_asset_id: string
  }
  source_popo_manifest: {
    bucket: string
    object: string
    sha256: string
  }
  workflow_version: string
  skill_release: {
    version: string
    sha256: string
  }
  template_sha256: string
  machine_status: string
  spec_status: string
  readiness_status: string
  human_acceptance_status: string
  spec_passed: boolean
  spec_ready_for_projection?: boolean
  ready_for_user_acceptance: boolean
  delivery_status: 'projecting' | 'projected' | 'projection_failed'
  delivery_error?: string
  projection_errors?: Array<{
    outbox_id: string
    event_kind: string
    status: string
    message: string
  }>
  human_acceptance_decision_recorded: boolean
  human_acceptance_effective: boolean
  human_accepted: boolean
  current_stage_key: string
  current_generation: number
  priority: number
  payload?: {
    source_evidence?: {
      input_set_sha256?: string
      source_pdf?: { bucket: string; object: string; sha256: string; size_bytes: number }
      mineru_manifest?: { bucket: string; object: string; sha256: string; size_bytes: number }
      popo_manifest?: { bucket: string; object: string; sha256: string; size_bytes: number }
      stage_run_ids?: { mineru: string; popo: string }
    }
  }
  error: { code: string; message: string }
  stages?: WorkflowV3Stage[]
  events?: Array<Record<string, any>>
  model_calls?: Array<Record<string, any>>
  candidates?: Array<Record<string, any>>
  evaluations?: Array<Record<string, any>>
  promotions?: Array<Record<string, any>>
  review_resolutions?: Array<Record<string, any>>
  final_output_id?: string
  review_entry?: {
    available: boolean
    review_asset_id: string
    final_output_id: string
    compare_url: string
    compare_api_url: string
  }
  delivery_assets?: {
    candidate: WorkflowV3DeliveryAssetGroup
    projected_candidate: WorkflowV3DeliveryAssetGroup
    formal: WorkflowV3DeliveryAssetGroup
  }
  projection_outbox?: Array<{
    id: string
    event_kind: string
    status: string
    projected_output_id?: string
    projected_manifest?: {
      bucket: string
      object: string
      sha256: string
    }
    last_error?: string
    attempt_count?: number
    lease_expires_at?: string | null
  }>
  created_at: string | null
  updated_at: string | null
}

export interface WorkflowV3DeliveryAssetGroup {
  available: boolean
  output_id?: string
  registry_status?: string
  quality_status?: string
  formalized?: boolean
  manifest?: {
    bucket: string
    object: string
    sha256: string
  }
  volumes: Array<{
    volume_id: string
    label: string
    zip_url: string
    pdf_url: string
  }>
  error?: string
}

export interface WorkflowV3JobPage {
  total: number
  page: number
  page_size: number
  items: WorkflowV3Job[]
}

export interface WorkflowV3EligibleSource {
  material_pk: string
  material_id: string
  filename: string
  size_bytes: number
  page_count: number
  mineru_run_id: string
  mineru_manifest_sha256: string
  mineru_frozen_marker_sha256: string
  popo_run_id: string
  popo_manifest_sha256: string
  popo_frozen_marker_sha256: string
  source_pdf_sha256: string
  input_set_sha256: string
  eligible: boolean
  error: string
}

export const workflowV3Api = {
  health() {
    return api.get<WorkflowV3Status>('/workflow-v3/health').then(res => res.data)
  },

  contracts() {
    return api.get<{ workflow_version: string; stages: Array<Record<string, any>> }>('/workflow-v3/contracts').then(res => res.data)
  },

  releases() {
    return api.get<{ items: WorkflowV3Release[] }>('/workflow-v3/releases').then(res => res.data.items)
  },

  jobs(params: { page: number; page_size: number; machine_status?: string }) {
    return api.get<WorkflowV3JobPage>('/workflow-v3/jobs', { params }).then(res => res.data)
  },

  job(jobId: string) {
    return api.get<WorkflowV3Job>(`/workflow-v3/jobs/${jobId}`).then(res => res.data)
  },

  eligibleSources(search = '') {
    return api.get<{ items: WorkflowV3EligibleSource[] }>('/workflow-v3/sources/eligible', {
      params: { search, limit: 100 }
    }).then(res => res.data.items)
  },

  createBatch(
    sources: WorkflowV3EligibleSource[],
    release: WorkflowV3Release,
    source = 'workflow_v3_ui'
  ) {
    return api.post<Record<string, any>>('/workflow-v3/jobs/batch', {
      sources: sources.map(row => ({
        material_pk: Number(row.material_pk),
        popo_manifest_sha256: row.popo_manifest_sha256
      })),
      skill_release_version: release.release_version,
      skill_release_sha256: release.manifest_sha256,
      priority: 100,
      payload: { source }
    }).then(res => res.data)
  },

  retry(jobId: string) {
    return api.post<{ job: WorkflowV3Job }>(`/workflow-v3/jobs/${jobId}/retry`).then(res => res.data.job)
  },

  retryProjection(jobId: string, outboxId: string) {
    return api.post<{ job: WorkflowV3Job; outbox: Record<string, any> }>(
      `/workflow-v3/admin/jobs/${jobId}/projection-outbox/${outboxId}/retry`
    ).then(res => res.data)
  },

  cancel(jobId: string, reason: string) {
    return api.post<{ job: WorkflowV3Job }>(`/workflow-v3/jobs/${jobId}/cancel`, {
      reason
    }).then(res => res.data.job)
  },

  recordAcceptance(
    jobId: string,
    decision: 'accepted' | 'rejected',
    note: string,
    outputId: string,
    manifestSha256: string
  ) {
    return api.post<{ job: WorkflowV3Job }>(`/workflow-v3/jobs/${jobId}/human-acceptance`, {
      accepted: decision === 'accepted',
      output_id: Number(outputId),
      manifest_sha256: manifestSha256,
      reason: note
    }).then(res => res.data.job)
  }
}
