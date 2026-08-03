import api from './index'
import type { FileListResponse, DownloadUrlResponse } from './files'
import type { MarkdownVariant, PopoStatus, SourceMap } from '@/types/file'

export type ReviewMarkdownVariant = MarkdownVariant | 'popo_page'

export interface ManifestImportPayload {
  manifest_bucket: string
  manifest_object: string
  title?: string
}

export interface ReviewMetadataPayload {
  review_status: string
  review_tags: string[]
  review_note: string
}

export interface FinalReviewSessionPayload {
  asset_id: number
  reuse_open?: boolean
}

export interface FinalReviewAnnotationPayload {
  issue_type: string
  severity: string
  status?: string
  human_note?: string
  anchors?: Record<string, unknown>
  evidence?: Record<string, unknown>
}

export interface FinalReviewDecisionPayload {
  decision: string
  reviewer_note?: string
}

export interface LatexCompareResponse {
  asset_id: string
  material_pk: string
  material_id: string
  stage: string
  output_id?: string
  output_origin?: string
  output_run_id?: string
  available_outputs?: Array<{
    id?: string
    material_pk?: string
    manifest: {
      bucket: string
      object: string
    }
    material_id: string
    popo_run_id: string
    output_run_id: string
    origin: string
    status?: string
    quality_status?: string
    is_current?: boolean
    version_label?: string
    created_at: string
  }>
  manifest: {
    bucket: string
    object: string
  }
  manifest_json: Record<string, unknown>
  compile_report: Record<string, unknown>
  source_pdf_url: string
  source_pdf_original_url?: string
  latex_pdf_url: string
  download_urls: Record<string, string>
  volumes?: Array<{
    volume_id: string
    label: string
    compiled_pdf: string
    package_zip: string
    compile_report: string
    latex_pdf_url: string
    download_urls: Record<string, string>
  }>
}

export const reviewApi = {
  importManifest(payload: ManifestImportPayload) {
    return api.post('/review/assets/from_manifest', payload).then(res => res.data)
  },

  importManifestBatch(payload: { manifest_bucket: string; manifest_objects: string[] }) {
    return api.post('/review/assets/batch_from_manifest', payload).then(res => res.data)
  },

  syncMinio(payload: {
    manifest_bucket: string
    prefix: string
    input_bucket?: string
    input_prefix?: string
    include_input_pdfs?: boolean
    limit?: number
  }) {
    return api.post('/review/assets/sync_minio', payload).then(res => res.data)
  },

  getAssets(params: { page: number; page_size: number; search?: string; view?: string }) {
    return api.get<FileListResponse>('/review/assets', { params }).then(res => res.data)
  },

  getFinalReviewAssets(params: { page: number; page_size: number; search?: string }) {
    return api.get<FileListResponse & {
      issue_types: string[]
      severities: string[]
      statuses: string[]
    }>('/review/final/assets', { params }).then(res => res.data)
  },

  getAsset(assetId: string) {
    return api.get(`/review/assets/${assetId}`).then(res => res.data)
  },

  getLatexCompare(assetId: string, outputId = '') {
    const params = outputId ? { output_id: outputId } : undefined
    return api.get<LatexCompareResponse>(`/review/assets/${assetId}/latex_compare`, { params }).then(res => res.data)
  },

  updateMetadata(assetId: string, payload: ReviewMetadataPayload) {
    return api.patch(`/review/assets/${assetId}/metadata`, payload).then(res => res.data)
  },

  createFinalReviewSession(payload: FinalReviewSessionPayload) {
    return api.post('/review/final/sessions', payload).then(res => res.data)
  },

  getFinalReviewSession(sessionId: string) {
    return api.get(`/review/final/sessions/${sessionId}`).then(res => res.data)
  },

  createFinalReviewAnnotation(sessionId: string, payload: FinalReviewAnnotationPayload) {
    return api.post(`/review/final/sessions/${sessionId}/annotations`, payload).then(res => res.data)
  },

  updateFinalReviewAnnotation(annotationId: string, payload: Partial<FinalReviewAnnotationPayload>) {
    return api.patch(`/review/final/annotations/${annotationId}`, payload).then(res => res.data)
  },

  deleteFinalReviewAnnotation(annotationId: string) {
    return api.delete(`/review/final/annotations/${annotationId}`).then(res => res.data)
  },

  submitFinalReviewSession(sessionId: string) {
    return api.post(`/review/final/sessions/${sessionId}/submit`).then(res => res.data)
  },

  verifyFinalReviewAnnotation(annotationId: string) {
    return api.post(`/review/final/annotations/${annotationId}/verify`).then(res => res.data)
  },

  verifyFinalReviewSession(sessionId: string) {
    return api.post(`/review/final/sessions/${sessionId}/verify`).then(res => res.data)
  },

  decideFinalReviewAnnotation(annotationId: string, payload: FinalReviewDecisionPayload) {
    return api.patch(`/review/final/annotations/${annotationId}/decision`, payload).then(res => res.data)
  },

  exportFinalReviewSession(sessionId: string) {
    return api.get(`/review/final/sessions/${sessionId}/export`).then(res => res.data)
  },

  generateReport(assetId: string) {
    return api.post(`/review/assets/${assetId}/report`).then(res => res.data)
  },

  getReportUrl(assetId: string) {
    return `/api/review/assets/${assetId}/report`
  },

  getParsedContent(assetId: string, variant: ReviewMarkdownVariant = 'markdown_page') {
    return api.get<string>(`/review/assets/${assetId}/parsed_content`, {
      params: { variant }
    }).then(res => res.data)
  },

  getPopoStatus(assetId: string) {
    return api.get<PopoStatus>(`/review/assets/${assetId}/popo/status`)
      .then(res => res.data)
  },

  getSourceMap(assetId: string) {
    return api.get<SourceMap>(`/review/assets/${assetId}/source_map`)
      .then(res => res.data)
  },

  getOutlineReview(assetId: string) {
    return api.get(`/review/assets/${assetId}/outline_review`)
      .then(res => res.data)
  },

  getDownloadUrl(assetId: string) {
    return api.get<DownloadUrlResponse>(`/review/assets/${assetId}/download_url`)
      .then(res => res.data)
  },

  getPageImageUrl(assetId: string, page: number, width = 1200) {
    const params = new URLSearchParams({
      page: String(page),
      width: String(width)
    })
    return `/api/review/assets/${assetId}/page_image?${params.toString()}`
  },

  getContentUrl(assetId: string) {
    return `/api/review/assets/${assetId}/content`
  },

  getArtifactUrl(assetId: string, stage: string, path: string) {
    return `/api/review/assets/${assetId}/artifact?stage=${encodeURIComponent(stage)}&path=${encodeURIComponent(path)}`
  },

  getElegantBookPackageUrl(assetId: string) {
    return `/api/review/assets/${assetId}/artifact?stage=elegantbook&path=latex-project.zip`
  }
}
