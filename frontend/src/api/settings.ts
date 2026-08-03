import api from './index'

export type MineruBackend =
  | 'pipeline'
  | 'vlm-engine'
  | 'vlm-http-client'
  | 'hybrid-engine'
  | 'hybrid-http-client'
  | (string & {})

// 设置相关类型定义
export interface SettingsData {
  force_ocr: boolean
  ocr_lang: string
  formula_recognition: boolean
  table_recognition: boolean
  backend: MineruBackend
  version?: string
}

export interface SettingsResponse extends SettingsData {
  user_id: string
}

export interface MineruHealthResponse {
  available: boolean
  base_url: string
  status?: string
  version?: string
  error?: string
  [key: string]: unknown
}

export interface RuntimeMinioConfig {
  endpoint: string
  public_endpoint: string
  secure: boolean
  access_key: string
  secret_key: string
  region: string
  contract_buckets: string[]
  access_key_configured?: boolean
  secret_key_configured?: boolean
}

export interface RuntimeGpuConfig {
  mode: 'on_demand'
  wrapper_url: string
  api_key: string
  api_key_configured?: boolean
}

export interface RuntimeProviderConfig {
  base_url: string
  api_key: string
  api_key_configured?: boolean
}

export interface RuntimeModelsConfig {
  llm: {
    enabled: boolean
    provider: string
    default_model: string
    reasoning_model: string
    deepseek: RuntimeProviderConfig
    outline_decision_max_tokens: number
    outline_global_max_candidates: number
    outline_max_risk_candidates: number
  }
  vision: {
    enabled: boolean
    provider: string
    model: string
    dashscope: RuntimeProviderConfig
    outline_visual_max_candidates: number
  }
}

export interface BackupTarget {
  id: string
  label: string
  kind: 'filesystem'
  path: string
  enabled: boolean
  external: boolean
  exists?: boolean
  writable?: boolean
  status?: string
}

export interface RuntimeBackupConfig {
  enabled: boolean
  mode: 'manifest' | 'copy'
  schedule_enabled: boolean
  interval_hours: number
  include_legacy: boolean
  max_objects: number
  targets: BackupTarget[]
}

export interface RuntimeConfig {
  schema_version: number
  environment: {
    name: string
    public_app_url: string
  }
  minio: RuntimeMinioConfig
  gpu: RuntimeGpuConfig
  models: RuntimeModelsConfig
  backup: RuntimeBackupConfig
}

export interface MinioCheckResult {
  connected: boolean
  endpoint: string
  contract_ok: boolean
  missing: string[]
  buckets: Array<{ bucket: string; exists: boolean; created: boolean; role: string; error?: string }>
  error?: string
}

export interface GpuCheckResult {
  state: 'ready' | 'degraded' | 'offline' | 'expected_off' | string
  wrapper_url: string
  wrapper_ok: boolean
  staged_api_ok: boolean
  health: Record<string, unknown>
  staged_api: Record<string, number | null>
  errors: string[]
  tunnel?: { auto_recovery?: boolean; status?: string; connection_verified?: boolean; last_attempt_at?: string | null; last_recovery_at?: string | null; local_port?: number; remote_port?: number; host?: string }
}

export interface BackupCheckResult {
  targets: BackupTarget[]
  ready_count: number
  external_ready_count: number
}

export interface BackupJob {
  id: string
  parent_job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | string
  mode: 'manifest' | 'copy'
  targets: BackupTarget[]
  buckets: string[]
  include_legacy: boolean
  max_objects: number
  attempt_count: number
  worker_id: string
  object_count: number
  copied_count: number
  bytes_copied: number
  truncated: boolean
  result: { targets?: Array<{ id: string; path: string; manifest: string; copied_count: number; bytes_copied: number }> }
  warnings: string[]
  error_message: string
  alert: { level: string; message: string; acknowledged: boolean }
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ModelProbeResult {
  provider: string
  model: string
  ok: boolean
  skipped: boolean
  status_code: number | null
  latency_ms: number
  error: string
}

export interface ModelCheckResult {
  ok: boolean
  checks: {
    llm: ModelProbeResult
    vision: ModelProbeResult
  }
}

export interface RuntimeStatus {
  status: 'ready' | 'warning' | 'blocked' | string
  blockers: string[]
  warnings: string[]
  minio: MinioCheckResult
  gpu: GpuCheckResult
  models: {
    llm: {
      enabled: boolean
      provider: string
      model: string
      reasoning_model: string
      api_key_configured: boolean
      base_url: string
    }
    vision: {
      enabled: boolean
      provider: string
      model: string
      api_key_configured: boolean
      base_url: string
    }
  }
  backup: BackupCheckResult
  dependencies: {
    ready: boolean
    checks: Record<string, { ready: boolean; detail?: string; reason?: string; worker_id?: string; age_seconds?: number }>
  }
  config: {
    ok: boolean
    errors: string[]
    warnings: string[]
    schema_version: number
  }
  active_gpu_tasks: number
}

// 设置 API
export const settingsApi = {
  /**
   * 获取用户设置
   */
  getSettings() {
    return api.get<SettingsResponse>('/settings')
      .then(res => res.data)
  },

  /**
   * 更新用户设置
   */
  updateSettings(settings: SettingsData) {
    return api.put('/settings', settings)
      .then(res => res.data)
  },

  getMineruHealth() {
    return api.get<MineruHealthResponse>('/system/mineru-health')
      .then(res => res.data)
  },

  getRuntimeSettings() {
    return api.get<RuntimeConfig>('/runtime/settings')
      .then(res => res.data)
  },

  updateRuntimeSettings(settings: RuntimeConfig) {
    return api.put<RuntimeConfig>('/runtime/settings', settings)
      .then(res => res.data)
  },

  getRuntimeStatus() {
    return api.get<RuntimeStatus>('/runtime/status')
      .then(res => res.data)
  },

  checkMinio() {
    return api.post<MinioCheckResult>('/runtime/minio/check')
      .then(res => res.data)
  },

  ensureMinioBuckets() {
    return api.post<MinioCheckResult>('/runtime/minio/ensure-buckets')
      .then(res => res.data)
  },

  checkGpu() {
    return api.post<GpuCheckResult>('/runtime/gpu/check')
      .then(res => res.data)
  },

  checkModels() {
    return api.post<ModelCheckResult>('/runtime/models/check')
      .then(res => res.data)
  },

  checkBackupTargets() {
    return api.post<BackupCheckResult>('/runtime/backup/check')
      .then(res => res.data)
  },

  createBackupJob() {
    return api.post<BackupJob>('/runtime/backup/jobs')
      .then(res => res.data)
  },

  listBackupJobs() {
    return api.get<{ items: BackupJob[] }>('/runtime/backup/jobs')
      .then(res => res.data.items)
  },

  retryBackupJob(jobId: string) {
    return api.post<BackupJob>(`/runtime/backup/jobs/${jobId}/retry`)
      .then(res => res.data)
  },

  acknowledgeBackupAlert(jobId: string) {
    return api.post<BackupJob>(`/runtime/backup/jobs/${jobId}/alerts/acknowledge`)
      .then(res => res.data)
  }
}
