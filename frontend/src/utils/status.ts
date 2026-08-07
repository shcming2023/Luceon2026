// 文件状态映射
export const FileStatusMap = {
  pending: { text: '等待解析', type: 'info' as const },
  parsing: { text: '解析中', type: 'warning' as const },
  parsed: { text: '已完成', type: 'success' as const },
  parse_failed: { text: '解析失败', type: 'danger' as const }
} as const

// 上传状态映射
export const UploadStatusMap = {
  waiting: { text: '等待上传', type: 'info' as const },
  uploading: { text: '上传中', type: 'warning' as const },
  success: { text: '上传成功', type: 'success' as const },
  error: { text: '上传失败', type: 'danger' as const }
} as const

/**
 * 获取文件状态信息
 */
export function getFileStatusInfo(status: string) {
  return FileStatusMap[status as keyof typeof FileStatusMap] || { text: '未知状态', type: 'info' as const }
}

/**
 * 获取文件状态显示文本
 */
export function getFileStatusText(status: string): string {
  return getFileStatusInfo(status).text
}

/**
 * 获取文件状态类型
 */
export function getFileStatusType(status: string): string {
  return getFileStatusInfo(status).type
}

/**
 * 获取上传状态信息
 */
export function getUploadStatusInfo(status: string) {
  return UploadStatusMap[status as keyof typeof UploadStatusMap] || { text: '未知状态', type: 'info' as const }
}

/**
 * 获取上传状态显示文本
 */
export function getUploadStatusText(status: string): string {
  return getUploadStatusInfo(status).text
}

/**
 * 获取上传状态类型
 */
export function getUploadStatusType(status: string): string {
  return getUploadStatusInfo(status).type
}

// 后端配置映射
export const BackendConfig = {
  pipeline: { icon: 'Pipeline', color: '#409EFF' },
  'vlm-engine': { icon: 'VLM Engine', color: '#67C23A' },
  'vlm-http-client': { icon: 'VLM HTTP', color: '#529B2E' },
  'hybrid-engine': { icon: 'Hybrid Engine', color: '#E6A23C' },
  'hybrid-http-client': { icon: 'Hybrid HTTP', color: '#B88230' },
  'vlm-auto-engine': { icon: 'VLM Auto', color: '#67C23A' },
  'hybrid-auto-engine': { icon: 'Hybrid Auto', color: '#E6A23C' },
  vlm: { icon: 'VLM', color: '#67C23A' },
  hybrid: { icon: 'Hybrid', color: '#E6A23C' }
} as const

/**
 * 获取后端信息
 */
export function getBackendInfo(backend?: string) {
  const exactConfig = BackendConfig[backend as keyof typeof BackendConfig]
  if (exactConfig) return exactConfig
  if (backend?.startsWith('vlm-')) return { icon: 'VLM', color: '#67C23A' }
  if (backend?.startsWith('hybrid-')) return { icon: 'Hybrid', color: '#E6A23C' }
  return { icon: '', color: '#909399' }
}

/**
 * 获取后端图标
 */
export function getBackendIcon(backend?: string): string {
  return getBackendInfo(backend).icon
}

/**
 * 获取后端颜色
 */
export function getBackendColor(backend?: string): string {
  return getBackendInfo(backend).color
}

/**
 * 格式化日期时间
 */
export function formatDateTime(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

const PipelineStageText: Record<string, string> = {
  queued: '等待 Worker',
  input_frozen: 'PDF 已哈希并冻结',
  gpu_starting: '正在核验并按需启动 GPU',
  gpu_ready: 'GPU、SSH 与服务已就绪',
  gpu_transport_qualified: '受管 Wrapper 通道已复核，允许提交',
  gpu_transport_failed_before_submit: 'GPU 已就绪，但受管 Wrapper 通道在提交前失败（未创建远端任务）',
  gpu_lifecycle_failed: 'GPU 生命周期门禁失败',
  gpu_stopped: 'GPU 已由云控制面确认停止',
  gpu_retained: 'GPU 保持运行（无停机所有权或仍有活动）',
  pipeline_command: 'GPU 串行处理中（MinerU → Popo）',
  mineru: 'MinerU 解析',
  mineru_frozen: 'MinerU 已冻结',
  mineru_freezing: '正在冻结 MinerU 到本地 MinIO',
  mineru_failed: 'MinerU 失败',
  popo: 'Popo 解析',
  popo_frozen: 'Popo 已冻结',
  pulled_local: '结果已拉回本地并通过 SHA 核验',
  popo_freezing: '正在冻结 Popo 到本地 MinIO',
  popo_model_loading: '正在切换并加载 Popo 模型',
  popo_model_ready: 'Popo 模型已就绪',
  popo_failed: 'Popo 失败',
  metadata: 'AI 元数据',
  finished: '全部阶段完成',
  partial: '部分材料失败',
  failed: '批次失败',
  interrupted: '任务中断',
  recovered_after_worker_loss: 'Worker 丢失后已恢复',
  recovery_done: '异常恢复完成',
  worker_failed: 'Worker 执行失败'
}

export function formatPipelineStage(stage?: string | null): string {
  const value = String(stage || '').trim()
  const remote = /^(mineru|popo)_remote_(.+)$/.exec(value)
  if (remote) return `${remote[1] === 'mineru' ? 'MinerU' : 'Popo'} 远端${remote[2] === 'succeeded' ? '已完成，待本地冻结' : `处理中（${remote[2]}）`}`
  return PipelineStageText[value] || value || '—'
}
