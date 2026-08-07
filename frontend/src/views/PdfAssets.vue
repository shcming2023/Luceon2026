<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Refresh, Search, UploadFilled } from '@element-plus/icons-vue'
import { materialsApi } from '@/api/materials'
import type { MaterialArtifactCatalog, MaterialItem, MaterialLineage, MaterialUploadResponse, PdfUploadCapabilities, PipelinePreflightResponse } from '@/types/material'
import { formatFileSize } from '@/utils/format'
import { formatDateTime } from '@/utils/status'
import { ensureCurrentUser, fetchCurrentUser, useCurrentUser } from '@/utils/user'
import MaterialIdentity from '@/components/MaterialIdentity.vue'
import StageStatusBadge from '@/components/StageStatusBadge.vue'
import ArtifactDownloadPanel from '@/components/ArtifactDownloadPanel.vue'
import LineageTimeline from '@/components/LineageTimeline.vue'
import './workspace.css'

const route = useRoute()
const router = useRouter()
const currentUser = useCurrentUser()
const isAdmin = computed(() => Boolean(currentUser.value?.capabilities?.pipeline_admin))
const loading = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadResults = ref<MaterialUploadResponse['files']>([])
const uploadCapabilities = ref<PdfUploadCapabilities | null>(null)
const rows = ref<MaterialItem[]>([])
const total = ref(0)
const page = ref(Number(route.query.page) || 1)
const pageSize = ref(20)
const search = ref(String(route.query.search || ''))
const stage = ref(String(route.query.stage || ''))
const selected = ref<MaterialItem[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
type UploadResumeContext = { filenames: string[]; progress: number; updatedAt?: string }
const uploadResumeContext = ref<UploadResumeContext | null>(null)
const uploadResumeDescription = computed(() => {
  const context = uploadResumeContext.value
  if (!context) return ''
  const filenames = context.filenames.slice(0, 3).join('、')
  const remainder = context.filenames.length > 3 ? ` 等 ${context.filenames.length} 个文件` : ''
  const progress = context.progress > 0 ? `，中断前浏览器进度 ${context.progress}%` : ''
  return `${filenames}${remainder}${progress}。浏览器不能自动恢复本地文件，请重新选择同一批文件；已写入资产库的文件会按 SHA-256 去重复用，只继续处理未完成文件。`
})

const batchDialog = ref(false)
const preflightLoading = ref(false)
const submitting = ref(false)
const preflight = ref<PipelinePreflightResponse | null>(null)
const reprocessCompleted = ref(false)

const detailOpen = ref(false)
const detailLoading = ref(false)
const detailMaterial = ref<MaterialItem | null>(null)
const catalog = ref<MaterialArtifactCatalog | null>(null)
const lineage = ref<MaterialLineage | null>(null)

const selectionValid = computed(() => selected.value.length > 0 && selected.value.length <= 5)
const uploadLimitText = computed(() => {
  const value = uploadCapabilities.value
  if (!value) return '正在读取服务器上传限制…'
  const aggregateGiB = value.max_request_bytes / (1024 ** 3)
  return `单文件 ${value.max_file_label} / ${value.max_file_pages} 页；单次最多 ${value.max_request_files} 本、合计 ${aggregateGiB.toFixed(aggregateGiB % 1 ? 1 : 0)} GiB。大文件上传后仍需 GPU 资源预检。`
})
const uploadResumeKey = 'luceon-upload-resume'

function readUploadResumeContext() {
  try {
    const raw = sessionStorage.getItem(uploadResumeKey)
    if (!raw) return
    const value = JSON.parse(raw) as Partial<UploadResumeContext>
    if (!Array.isArray(value.filenames) || !value.filenames.every(filename => typeof filename === 'string')) {
      sessionStorage.removeItem(uploadResumeKey)
      return
    }
    uploadResumeContext.value = {
      filenames: value.filenames,
      progress: Number.isFinite(value.progress) ? Math.max(0, Math.min(100, Number(value.progress))) : 0,
      updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : undefined,
    }
  } catch {
    sessionStorage.removeItem(uploadResumeKey)
  }
}

function saveUploadResumeContext(files: File[], progress: number) {
  const context: UploadResumeContext = {
    filenames: files.map(file => file.name),
    progress,
    updatedAt: new Date().toISOString(),
  }
  sessionStorage.setItem(uploadResumeKey, JSON.stringify(context))
  uploadResumeContext.value = context
}

function clearUploadResumeContext() {
  sessionStorage.removeItem(uploadResumeKey)
  uploadResumeContext.value = null
}

function metadataDisplayStatus(material: MaterialItem) {
  const status = material.book_metadata?.status || 'missing'
  return material.book_metadata?.manual_override || status === 'manual' || status === 'ai_extracted' ? 'succeeded' : status
}

function metadataDisplayLabel(material: MaterialItem) {
  const status = material.book_metadata?.status || 'missing'
  if (material.book_metadata?.manual_override || status === 'manual') return '人工已编目'
  if (status === 'ai_extracted') return 'AI 已编目'
  return '待编目'
}

function refinementOutputLabel(material: MaterialItem) {
  if (material.current_refinement_output?.id) return `Output ${material.current_refinement_output.id} 可用`
  return material.refinement_output_status === 'succeeded' ? '已有可用输出' : '暂无可用输出'
}

function latestRefinementLabel(material: MaterialItem) {
  if (material.latest_refinement_status === 'idle') return '未创建任务'
  if (material.latest_refinement_status === 'unavailable') return '任务状态不可用'
  return undefined
}

function latestWorkflowV2JobId(material: MaterialItem) {
  return material.latest_refinement_source === 'workflow_v2' ? material.latest_refinement_job?.id || '' : ''
}

function updateQuery() {
  router.replace({ query: { ...(search.value ? { search: search.value } : {}), ...(stage.value ? { stage: stage.value } : {}), ...(page.value > 1 ? { page: String(page.value) } : {}), ...(detailOpen.value && detailMaterial.value ? { material_pk: detailMaterial.value.id } : {}) } })
}

async function load() {
  loading.value = true
  try {
    const requestedMaterialPk = String(route.query.material_pk || '')
    const result = await materialsApi.getMaterials({ page: page.value, page_size: pageSize.value, search: search.value, stage: stage.value })
    rows.value = result.materials
    total.value = result.total
    updateQuery()
    const requested = rows.value.find(row => row.id === requestedMaterialPk)
    if (requested && (!detailOpen.value || detailMaterial.value?.id !== requested.id)) await openDetail(requested)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || 'PDF 资产加载失败')
  } finally {
    loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  load()
}

async function uploadFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  if (files.some(file => file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf'))) {
    ElMessage.error('只能上传 PDF 文件')
    input.value = ''
    return
  }
  const capability = uploadCapabilities.value
  if (capability) {
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0)
    if (files.length > capability.max_request_files || totalBytes > capability.max_request_bytes || files.some(file => file.size > capability.max_file_bytes)) {
      ElMessage.error(`所选文件超过服务器当前包络：${uploadLimitText.value}`)
      input.value = ''
      return
    }
  }
  try {
    await fetchCurrentUser()
  } catch {
    ElMessage.error('登录已失效。已保留待上传文件名称，请重新登录后继续。')
    saveUploadResumeContext(files, 0)
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  uploadResults.value = []
  saveUploadResumeContext(files, 0)
  try {
    const result = await materialsApi.upload(files, value => {
      uploadProgress.value = value
      saveUploadResumeContext(files, value)
    })
    uploadResults.value = result.files || []
    const duplicateText = result.duplicates ? `，${result.duplicates} 个已存在并去重` : ''
    ElMessage.success(`上传处理完成：${result.success} 个成功${duplicateText}`)
    clearUploadResumeContext()
    await load()
  } catch (error: any) {
    if (!error?.isAuthExpired) ElMessage.error(error?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
    input.value = ''
  }
}

function onSelectionChange(value: MaterialItem[]) {
  if (value.length > 5) {
    ElMessage.warning('一个解析批次最多选择 5 本教材')
    return
  }
  selected.value = value
  preflight.value = null
}

function isSelected(row: MaterialItem) {
  return selected.value.some(item => Number(item.id) === Number(row.id))
}

function toggleMobileSelection(row: MaterialItem, checked: boolean | string | number) {
  const next = selected.value.filter(item => Number(item.id) !== Number(row.id))
  if (Boolean(checked)) {
    if (next.length >= 5) {
      ElMessage.warning('一个解析批次最多选择 5 本教材')
      return
    }
    next.push(row)
  }
  selected.value = next
  preflight.value = null
}

function openBatch() {
  if (!selectionValid.value) return
  preflight.value = null
  reprocessCompleted.value = false
  batchDialog.value = true
}

function preflightResourceText(result: PipelinePreflightResponse) {
  if (result.status === 'GPU_OFFLINE') return '自动管理已关闭；请先在 Compshare 手工开机，系统不会自动开机或关机。'
  const gate = result.resource_gate
  if (!gate?.applies) return ''
  const required = formatFileSize(gate.required_headroom_bytes || 0)
  if (result.status === 'CLOUD_LIFECYCLE_DEFERRED' || gate.status === 'deferred_until_gpu_ready') {
    return `GPU 当前可离线；提交后由 Worker 执行 Describe/按需启动，并在上传 PDF 前核验远端磁盘。本批次动态要求至少 ${required}`
  }
  const available = formatFileSize(gate.available_headroom_bytes || 0)
  return gate.ok
    ? `超大 PDF 资源门禁通过：GPU 临时产物余量 ${available}，本批次要求至少 ${required}`
    : `${gate.reason || 'GPU 临时产物余量不足'}：当前 ${available}，本批次要求至少 ${required}`
}

function preflightFailureText(result: PipelinePreflightResponse) {
  if (result.status === 'GPU_OFFLINE') return 'GPU_OFFLINE：自动管理已关闭，请手工开机后重试'
  if (result.resource_gate?.applies && !result.resource_gate.ok) return preflightResourceText(result)
  return result.status || result.plan_status || '请检查运行状态'
}

async function runPreflight() {
  preflightLoading.value = true
  try {
    preflight.value = await materialsApi.preflightPipeline(selected.value.length, {
      material_pks: selected.value.map(row => Number(row.id)),
      reprocess_completed: reprocessCompleted.value
    })
    if (preflight.value.ready) ElMessage.success('预检通过，可提交解析批次')
    else ElMessage.warning(`预检未通过：${preflightFailureText(preflight.value)}`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '预检失败')
  } finally {
    preflightLoading.value = false
  }
}

async function submitBatch() {
  if (!preflight.value?.ready) return
  await ElMessageBox.confirm('提交后将按快照执行 MinerU 批量 → 逐本冻结 → Popo 批量 → 逐本冻结。是否继续？', '提交解析批次', { type: 'warning' })
  submitting.value = true
  try {
    const run = await materialsApi.startPipeline(true, selected.value.length, {
      material_pks: selected.value.map(row => Number(row.id)),
      reprocess_completed: reprocessCompleted.value
    })
    ElMessage.success(`解析批次 #${run.id} 已进入持久队列`)
    batchDialog.value = false
    router.push(`/pipeline/runs?run_id=${run.id}`)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    const failedPreflight = detail?.preflight as PipelinePreflightResponse | undefined
    ElMessage.error(failedPreflight ? `提交已拦截：${preflightFailureText(failedPreflight)}` : (typeof detail === 'string' ? detail : '提交失败'))
  } finally {
    submitting.value = false
  }
}

async function openDetail(material: MaterialItem) {
  detailMaterial.value = material
  detailOpen.value = true
  updateQuery()
  detailLoading.value = true
  catalog.value = null
  lineage.value = null
  try {
    const [artifactResult, lineageResult] = await Promise.all([
      materialsApi.getArtifactCatalog(material.id),
      materialsApi.getLineage(material.id)
    ])
    catalog.value = artifactResult
    lineage.value = lineageResult
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '资产详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailMaterial.value = null
  catalog.value = null
  lineage.value = null
  updateQuery()
}

async function createMetadataJob(material: MaterialItem) {
  try {
    const job = await materialsApi.createMetadataJob(material.id)
    ElMessage.success(`AI 元数据任务 #${job.id} 已排队`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '元数据任务创建失败')
  }
}

function openReview(material: MaterialItem) {
  if (material.review_asset_id) router.push({ path: '/review/compare', query: { asset_id: material.review_asset_id } })
}

function openRefinement(material: MaterialItem) {
  const jobId = latestWorkflowV2JobId(material)
  if (jobId) {
    router.push({ path: '/workflow/jobs', query: { job_id: jobId } })
    return
  }
  router.push({ path: '/workflow/jobs', query: { material_pk: material.id, material_id: material.material_id } })
}

watch(page, load)
onMounted(async () => {
  readUploadResumeContext()
  try { await ensureCurrentUser() } catch { /* API will enforce authorization */ }
  try { uploadCapabilities.value = await materialsApi.getUploadCapabilities() } catch { /* upload API remains authoritative */ }
  await load()
})
</script>

<template>
  <div class="workspace-page">
    <header class="workspace-header">
      <div>
        <span class="workspace-kicker">Stage 1 · digital assets</span>
        <h1>PDF 资产</h1>
        <p>上传、去重、检索与下载原始或阶段性数字资产；任务执行在独立工作台追踪。</p>
        <p class="mono-note">{{ uploadLimitText }}</p>
      </div>
      <div class="workspace-actions">
        <input ref="fileInput" hidden type="file" accept="application/pdf,.pdf" multiple @change="uploadFiles" />
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="UploadFilled" :loading="uploading" @click="fileInput?.click()">上传 PDF</el-button>
      </div>
    </header>

    <el-progress v-if="uploading" :percentage="uploadProgress" :stroke-width="5" />

    <el-alert
      v-if="uploadCapabilities && !uploadCapabilities.internal_2gib_2000_profile_qualified"
      type="warning"
      :closable="false"
      show-icon
      title="当前部署上传限制低于公司内部 2 GiB / 2000 页资格基线"
      :description="uploadCapabilities.internal_profile_gap.join('；')"
    />

    <section v-if="uploadResumeContext && !uploading" class="workspace-panel">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="检测到上次上传未完整确认"
        :description="uploadResumeDescription"
      />
      <div class="inline-actions" style="margin-top: 8px">
        <el-button link @click="clearUploadResumeContext">清除恢复提示</el-button>
      </div>
    </section>

    <section v-if="uploadResults.length" class="workspace-panel">
      <div class="workspace-toolbar">
        <div>
          <strong>本次上传结果</strong>
          <p class="mono-note">逐文件显示本次提交名与系统规范资产；“已去重”表示复用既有数字资产，没有重复创建材料。</p>
        </div>
      </div>
      <el-table :data="uploadResults" size="small" max-height="240">
        <el-table-column prop="filename" label="本次提交文件名" min-width="220" />
        <el-table-column label="处理结果" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'failed' ? 'danger' : row.status === 'duplicate' ? 'warning' : 'success'">
              {{ row.status === 'duplicate' ? '已去重' : row.status === 'success' ? '已新建' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入冻结" width="125">
          <template #default="{ row }">
            <StageStatusBadge
              :status="row.material?.pipeline_status === 'input_frozen' ? 'succeeded' : 'needs_review'"
              :label="row.material?.pipeline_status === 'input_frozen' ? 'input_frozen' : (row.material?.pipeline_status || '未形成')"
            />
          </template>
        </el-table-column>
        <el-table-column label="GPU 资格" width="180">
          <template #default="{ row }">
            <el-tag :type="row.eligibility_status === 'gpu_eligible' ? 'success' : row.eligibility_status === 'uploaded_but_gpu_resource_review' ? 'warning' : 'danger'">
              {{ row.eligibility_status === 'gpu_eligible' ? '可进入资源预检' : row.eligibility_status === 'uploaded_but_gpu_resource_review' ? '已上传，待 GPU 资源审阅' : '配置拒绝' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="系统规范资产" min-width="330">
          <template #default="{ row }">
            <MaterialIdentity
              v-if="row.material"
              :filename="row.material.filename"
              :material-id="row.material.material_id"
              :material-pk="row.material.id"
              :sha256="row.material.input_sha256"
            />
            <span v-else class="error-note">{{ row.error_message || '未生成资产' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="workspace-panel">
      <div class="workspace-toolbar">
        <div class="workspace-filters">
          <el-input v-model="search" clearable placeholder="文件名、material_id、SHA 或元数据" style="width: 300px" @keyup.enter="applyFilter"><template #prefix><el-icon><Search /></el-icon></template></el-input>
          <el-select v-model="stage" clearable placeholder="资产阶段" style="width: 150px" @change="applyFilter">
            <el-option label="仅 PDF" value="input" />
            <el-option label="MinerU" value="mineru" />
            <el-option label="解析就绪" value="popo" />
            <el-option label="已有输出" value="latex" />
          </el-select>
          <el-button @click="applyFilter">查询</el-button>
        </div>
        <div class="inline-actions">
          <span class="mono-note">已选 {{ selected.length }}/5</span>
          <el-button type="primary" :disabled="!selectionValid" @click="openBatch">创建解析批次</el-button>
        </div>
      </div>

      <div class="workspace-table">
        <div class="mobile-record-list" aria-label="PDF 资产列表">
          <article v-for="row in rows" :key="`mobile-${row.id}`" class="mobile-record-card">
            <el-checkbox
              :model-value="isSelected(row)"
              :aria-label="`选择 ${row.filename}`"
              @change="toggleMobileSelection(row, $event)"
            >选择此 PDF</el-checkbox>
            <MaterialIdentity :filename="row.filename" :material-id="row.material_id" :material-pk="row.id" :sha256="row.input_sha256" />
            <dl class="mobile-record-facts">
              <div><dt>规格</dt><dd>{{ formatFileSize(row.size) }} · {{ row.page_count || '—' }} 页</dd></div>
              <div><dt>解析阶段</dt><dd>{{ row.popo_available ? '解析已完成（已冻结）' : '解析未完成' }}</dd></div>
              <div><dt>输入状态</dt><dd>{{ row.pipeline_status || row.stage_status || '未形成' }}</dd></div>
            </dl>
            <div class="inline-actions">
              <el-button link @click="openDetail(row)">资产与追溯</el-button>
              <el-button link @click="createMetadataJob(row)">AI 编目</el-button>
              <el-button v-if="row.popo_available" link @click="openRefinement(row)">{{ latestWorkflowV2JobId(row) ? '查看精修' : '进入精修' }}</el-button>
            </div>
          </article>
        </div>
        <el-table v-loading="loading" :data="rows" row-key="id" max-height="calc(100vh - 284px)" class="workspace-data-table" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="46" />
          <el-table-column label="PDF / 身份" min-width="310">
            <template #default="{ row }"><MaterialIdentity :filename="row.filename" :material-id="row.material_id" :material-pk="row.id" :sha256="row.input_sha256" /></template>
          </el-table-column>
          <el-table-column label="规格" width="135"><template #default="{ row }"><span>{{ formatFileSize(row.size) }}</span><span class="identity-meta">{{ row.page_count || '—' }} 页</span></template></el-table-column>
          <el-table-column label="解析阶段" width="155"><template #default="{ row }"><StageStatusBadge :status="row.popo_available ? 'succeeded' : row.stage_status" :label="row.popo_available ? '解析已完成（已冻结）' : '解析未完成'" /></template></el-table-column>
          <el-table-column label="编目状态" width="130"><template #default="{ row }"><StageStatusBadge :status="metadataDisplayStatus(row)" :label="metadataDisplayLabel(row)" /></template></el-table-column>
          <el-table-column label="可用精修产物" width="155"><template #default="{ row }"><StageStatusBadge :status="row.refinement_output_status" :label="refinementOutputLabel(row)" /></template></el-table-column>
          <el-table-column label="最新精修任务" width="145"><template #default="{ row }"><StageStatusBadge :status="row.latest_refinement_status" :label="latestRefinementLabel(row)" /></template></el-table-column>
          <el-table-column label="更新时间" width="166"><template #default="{ row }">{{ formatDateTime(row.last_synced_at || row.created_at || '') }}</template></el-table-column>
          <el-table-column label="操作" width="260">
            <template #default="{ row }">
              <el-button link @click="openDetail(row)">资产与追溯</el-button>
              <el-button link @click="createMetadataJob(row)">AI 编目</el-button>
              <el-button v-if="row.popo_available" link @click="openRefinement(row)">{{ latestWorkflowV2JobId(row) ? '查看精修' : '进入精修' }}</el-button>
              <el-button v-if="row.review_asset_id" link @click="openReview(row)">比对审阅</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="workspace-pagination"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" /></div>
    </section>

    <el-dialog v-model="batchDialog" title="创建解析批次" width="720px">
      <el-alert type="info" :closable="false" show-icon title="所选教材会固化为不可变任务快照；执行顺序为 MinerU 批量、逐本冻结、Popo 批量、逐本冻结。" />
      <ul class="snapshot-list">
        <li v-for="row in selected" :key="row.id"><MaterialIdentity :filename="row.filename" :material-id="row.material_id" :material-pk="row.id" :sha256="row.input_sha256" /></li>
      </ul>
      <el-alert
        v-if="selected.some(row => row.popo_available)"
        type="warning"
        :closable="false"
        show-icon
        title="所选资产已有冻结解析结果。普通提交会保持幂等且不重刷；只有管理员明确创建新版本时才会重新运行 MinerU 与 Popo，历史版本不会删除。"
      />
      <el-checkbox v-if="isAdmin" v-model="reprocessCompleted" @change="preflight = null">管理员：为已完成资产创建新的不可变解析版本</el-checkbox>
      <el-alert
        v-if="preflight"
        :type="preflight.ready ? 'success' : 'warning'"
        :closable="false"
        :title="preflight.status === 'CLOUD_LIFECYCLE_DEFERRED' ? '云生命周期已延后到正式提交' : preflight.ready ? '预检通过' : `预检未通过：${preflightFailureText(preflight)}`"
        :description="preflightResourceText(preflight)"
      />
      <template #footer>
        <el-button @click="batchDialog = false">取消</el-button>
        <el-button :loading="preflightLoading" @click="runPreflight">执行预检</el-button>
        <el-button type="primary" :disabled="!preflight?.ready" :loading="submitting" @click="submitBatch">提交解析</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" size="720px" :title="detailMaterial?.filename || '资产详情'" @closed="closeDetail">
      <div v-loading="detailLoading">
        <section class="detail-section"><h3><el-icon><Download /></el-icon> 可下载资产</h3><ArtifactDownloadPanel v-if="detailMaterial && catalog" :material-pk="detailMaterial.id" :artifacts="catalog.artifacts" /></section>
        <section class="detail-section"><h3>跨域追溯</h3><LineageTimeline v-if="lineage" :lineage="lineage" /></section>
      </div>
    </el-drawer>
  </div>
</template>
