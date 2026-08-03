<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { materialsApi, type WorkflowV2JobSummary } from '@/api/materials'
import type { MaterialItem } from '@/types/material'
import { formatDateTime } from '@/utils/status'
import MaterialIdentity from '@/components/MaterialIdentity.vue'
import StageStatusBadge from '@/components/StageStatusBadge.vue'
import './workspace.css'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const rows = ref<WorkflowV2JobSummary[]>([])
const total = ref(0)
const statusCounts = ref({ passed: 0, blocked: 0, running: 0 })
const page = ref(Number(route.query.page) || 1)
const pageSize = ref(20)
const status = ref(String(route.query.status || ''))

const createOpen = ref(false)
const materialLoading = ref(false)
const eligibleMaterials = ref<MaterialItem[]>([])
const selectedMaterials = ref<MaterialItem[]>([])
const materialSearch = ref('')
const creating = ref(false)

const detailOpen = ref(false)
const detailLoading = ref(false)
const selectedJob = ref<Record<string, any> | null>(null)
const candidate = ref<Record<string, any> | null>(null)
const candidateError = ref('')
const terminalStatuses = new Set(['succeeded', 'needs_review', 'blocked', 'handoff_ready', 'failed', 'cancelled'])
let refreshTimer: number | undefined

function updateQuery() {
  router.replace({ query: { ...(status.value ? { status: status.value } : {}), ...(page.value > 1 ? { page: String(page.value) } : {}), ...(selectedJob.value ? { job_id: selectedJob.value.id } : {}) } })
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const result = await materialsApi.getWorkflowV2JobSummaryPage({ page: page.value, page_size: pageSize.value, status: status.value })
    rows.value = result.jobs
    total.value = result.total
    statusCounts.value = result.status_counts || { passed: 0, blocked: 0, running: 0 }
    const requested = String(route.query.job_id || '')
    if (requested && selectedJob.value?.id !== requested) await openDetail(requested)
    updateQuery()
  } catch (error: any) {
    if (showLoading) ElMessage.error(error?.response?.data?.detail || '精修任务加载失败')
  } finally {
    if (showLoading) loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  load()
}

async function openCreate() {
  createOpen.value = true
  selectedMaterials.value = []
  await loadEligibleMaterials()
}

async function loadEligibleMaterials() {
  materialLoading.value = true
  try {
    const result = await materialsApi.getMaterials({ page: 1, page_size: 100, stage: 'popo', search: materialSearch.value })
    eligibleMaterials.value = result.materials
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '解析就绪材料加载失败')
  } finally {
    materialLoading.value = false
  }
}

function onMaterialSelection(value: MaterialItem[]) {
  selectedMaterials.value = value
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

async function createJobs() {
  if (!selectedMaterials.value.length) return
  creating.value = true
  try {
    const result = await materialsApi.createWorkflowV2JobsBatch(selectedMaterials.value.map(row => Number(row.id)))
    const queuedJobs = (result.results || []).filter((row: Record<string, any>) => row.job?.status === 'queued')
    const queueResults = await Promise.allSettled(queuedJobs.map((row: Record<string, any>) => materialsApi.runWorkflowV2Job(row.job.id)))
    const enqueued = queueResults.filter(row => row.status === 'fulfilled').length
    const enqueueFailed = queueResults.length - enqueued
    const summary = `已创建 ${result.created || 0} 个任务，复用 ${result.existing || 0} 个现有任务；提交 Worker ${enqueued} 个，失败 ${(result.failed || 0) + enqueueFailed} 个`
    if ((result.failed || 0) + enqueueFailed) ElMessage.warning(summary)
    else ElMessage.success(summary)
    createOpen.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '精修任务创建失败')
  } finally {
    creating.value = false
  }
}

async function runJob(job: WorkflowV2JobSummary) {
  await ElMessageBox.confirm(`开始执行 ${job.id}？Worker 会异步运行，页面可随时退出。`, '运行精修任务', { type: 'info' })
  try {
    await materialsApi.runWorkflowV2Job(job.id)
    ElMessage.success('任务已进入 Worker 队列')
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '任务入队失败')
  }
}

async function retryJob(job: WorkflowV2JobSummary) {
  try {
    await materialsApi.retryWorkflowV2Job(job.id)
    ElMessage.success('已从失败阶段重试')
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重试失败')
  }
}

async function openDetail(jobId: string) {
  detailOpen.value = true
  detailLoading.value = true
  candidate.value = null
  candidateError.value = ''
  try {
    selectedJob.value = await materialsApi.getWorkflowV2Job(jobId)
    if (['needs_review', 'blocked', 'handoff_ready'].includes(selectedJob.value.status) && selectedJob.value.deliverable_available) {
      try {
        candidate.value = await materialsApi.getWorkflowV2ReviewCandidate(jobId)
      } catch (error: any) {
        candidateError.value = error?.response?.data?.detail || '当前阶段没有可下载的排版候选件'
      }
    }
    updateQuery()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '任务详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function refreshActiveState() {
  await load(false)
  if (!selectedJob.value || terminalStatuses.has(selectedJob.value.status)) return
  try {
    const job = await materialsApi.getWorkflowV2Job(selectedJob.value.id)
    selectedJob.value = job
    if (['needs_review', 'blocked', 'handoff_ready'].includes(job.status) && job.deliverable_available) {
      try {
        candidate.value = await materialsApi.getWorkflowV2ReviewCandidate(job.id)
        candidateError.value = ''
      } catch (error: any) {
        candidate.value = null
        candidateError.value = error?.response?.data?.detail || '当前阶段没有可下载的排版候选件'
      }
    }
  } catch { /* keep the last visible snapshot until the next refresh */ }
}

function closeDetail() {
  selectedJob.value = null
  candidate.value = null
  candidateError.value = ''
  const query = { ...route.query }
  delete query.job_id
  router.replace({ query })
}

function currentStageStatus(job: Record<string, any>) {
  if (job.current_stage_status) return job.current_stage_status
  return [...(job.stages || [])].reverse().find((row: Record<string, any>) => row.stage_key === job.current_stage_key)?.status || '—'
}

function jobErrorMessage(job: Record<string, any>) {
  if (job.error?.message || job.error_message) return job.error?.message || job.error_message
  return [...(job.stages || [])].reverse().find((row: Record<string, any>) => row.error_message)?.error_message || '—'
}

const errorLabels: Record<string, string> = {
  preserved_source_blocks_missing_from_clean: '源码块没有稳定映射到清洗输出',
  clean_content_lines_unassigned: '清洗正文仍有未归属的源码行',
  clean_content_lines_assigned_more_than_once: '清洗正文行被重复归属',
  question_stem_used_as_outline_node: '题干被误识别为目录节点',
  outline_placeholder_title_forbidden: '目录仍包含占位标题',
  outline_depth_out_of_range: '目录层级不符合 2–3 级要求',
  outline_level_jump: '目录层级发生跳级',
  outline_parent_missing: '目录节点缺少有效父节点',
  outline_child_precedes_parent_source_page: '子目录在来源页序上早于父目录',
  outline_arbitration_required: '目录候选存在歧义，需要有边界的结构裁决',
  body_anchors_owned_by_multiple_outline_nodes: '多个目录节点占用了同一正文锚点',
  outline_nodes_missing_from_semantic_sections: '语义章节缺少已接受目录节点',
  semantic_sections_not_in_accepted_outline: '语义章节未绑定到已接受目录',
  semantic_sections_without_outline_node_id: '语义章节缺少稳定目录节点 ID',
  semantic_section_order_differs_from_accepted_outline: '语义章节顺序与已接受目录不一致',
  semantic_section_parent_binding_mismatch: '语义章节与目录父节点绑定不一致',
  semantic_section_level_mismatch: '语义章节层级与已接受目录不一致',
  semantic_section_source_span_invalid: '语义章节的来源范围无效',
  semantic_section_source_order_invalid: '语义章节来源顺序无效',
  semantic_component_lines_assigned_more_than_once: '正文行被重复分配到多个语义章节',
  manual_review_handoff_missing: '人工接手所需证据或交付物不完整',
  latex_missing_glyphs: 'LaTeX 存在缺字，不能交付',
  latex_obvious_overflow: 'LaTeX 存在明显溢出，不能交付',
  latex_unresolved_image_resources: 'LaTeX 仍有未解析图片资源',
  latex_missing_resources: 'LaTeX 引用的资源缺失',
}

function compactError(job: Record<string, any>) {
  const code = String(job.error?.code || '').trim()
  const raw = String(job.error?.message || job.error_message || '').trim()
  const candidates = raw
    .split(/[;\n]/)
    .map(value => value.trim())
    .filter(Boolean)
  const firstCode = candidates.find(value => errorLabels[value])
    || candidates.find(value => /^[a-z][a-z0-9_]{5,}$/.test(value))
    || code
  return {
    label: errorLabels[firstCode] || errorLabels[code] || (firstCode ? '质量门禁未通过' : '—'),
    code: firstCode || code,
  }
}

function stageLabel(value: string) {
  return ({
    canonical_clean_material: '源码守恒清洗',
    outline_reconstruction: '目录重建',
    semantic_annotation: '语义标注',
    deterministic_elegantbook: '确定性排版',
    bounded_deepseek_polish_qa: '有界精修质检',
    independent_final_review: '独立终审',
  } as Record<string, string>)[value] || value || '—'
}

function jobOutputLabel(job: WorkflowV2JobSummary) {
  if (job.job_output_id) return `本任务 Output ${job.job_output_id}`
  if (job.status === 'needs_review' && job.artifact_count) return `本任务候选 ${job.artifact_count} 项 · 尚未接受`
  return '本任务尚未接受输出'
}

async function handoff() {
  if (!selectedJob.value) return
  try {
    await materialsApi.handoffWorkflowV2ReviewCandidate(selectedJob.value.id)
    ElMessage.success('人工接手已登记；请下载候选 PDF、问题证据和待修复 ZIP')
    await openDetail(selectedJob.value.id)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '人工交接登记失败')
  }
}

async function revalidate() {
  if (!selectedJob.value) return
  try {
    await materialsApi.revalidateWorkflowV2ReviewCandidate(selectedJob.value.id)
    ElMessage.success('已从最小失败阶段重新验证')
    detailOpen.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重新验证失败')
  }
}

function hasOpenManualHandoff(job: Record<string, any>) {
  return (job.repair_attempts || []).some(
    (row: Record<string, any>) => row.repair_kind === 'manual_handoff' && row.status === 'running',
  )
}

async function restartBlockedStage() {
  if (!selectedJob.value) return
  const stageKey = selectedJob.value.minimal_resume_stage_key || selectedJob.value.current_stage_key
  await ElMessageBox.confirm(
    `从“${stageLabel(stageKey)}”重新验证？已通过且可靠冻结的上游产物不会重跑。`,
    '重新验证阻断阶段',
    { type: 'warning', confirmButtonText: '重新验证', cancelButtonText: '取消' },
  )
  try {
    await materialsApi.restartWorkflowV2Job(selectedJob.value.id, stageKey)
    ElMessage.success(`已从“${stageLabel(stageKey)}”创建新尝试并提交 Worker`)
    detailOpen.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '阻断阶段重新验证失败')
  }
}

function openReview(job: WorkflowV2JobSummary) {
  if (job.review_asset_id) router.push({ path: '/review/compare', query: { asset_id: job.review_asset_id } })
}

function openMaterial(job: WorkflowV2JobSummary) {
  router.push({ path: '/assets', query: { material_pk: job.material_pk, search: job.material_id } })
}

watch(page, () => load())
onMounted(async () => {
  const linkedMaterialPk = String(route.query.material_pk || '')
  const linkedMaterialId = String(route.query.material_id || '')
  await load()
  if (linkedMaterialPk) {
    materialSearch.value = linkedMaterialId
    await openCreate()
    const requested = eligibleMaterials.value.find(row => row.id === linkedMaterialPk)
    if (requested) selectedMaterials.value = [requested]
  }
  refreshTimer = window.setInterval(() => {
    if (rows.value.some(job => !terminalStatuses.has(job.status)) || (selectedJob.value && !terminalStatuses.has(selectedJob.value.status))) {
      void refreshActiveState()
    }
  }, 10_000)
})
onUnmounted(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
})
</script>

<template>
  <div class="workspace-page">
    <header class="workspace-header">
      <div>
        <span class="workspace-kicker">Stage 3 · Worker V2.3</span>
        <h1>精修任务</h1>
        <p>从已冻结 Popo 资产创建异步 Worker 任务；硬质量门禁与可人工交接产物分开显示。</p>
      </div>
      <div class="workspace-actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">创建精修任务</el-button>
      </div>
    </header>

    <div class="status-summary">
      <strong>通过 {{ statusCounts.passed }}</strong>
      <strong class="blocked-count">阻断 {{ statusCounts.blocked }}</strong>
      <strong>运行 {{ statusCounts.running }}</strong>
    </div>

    <section class="workspace-panel">
      <div class="workspace-toolbar">
        <div class="workspace-filters">
          <el-select v-model="status" clearable placeholder="任务状态" style="width: 180px" @change="applyFilter">
            <el-option label="排队中" value="queued" />
            <el-option label="运行中" value="running" />
            <el-option label="质量门禁阻断" value="blocked" />
            <el-option label="可人工接手" value="handoff_ready" />
            <el-option label="失败" value="failed" />
            <el-option label="已完成" value="succeeded" />
          </el-select>
        </div>
        <span class="mono-note">任务记录与浏览器会话解耦，刷新或退出不会丢失进度</span>
      </div>
      <el-table v-loading="loading" :data="rows" height="calc(100vh - 286px)" empty-text="暂无精修任务">
        <el-table-column label="教材" min-width="280"><template #default="{ row }"><MaterialIdentity :filename="row.filename || row.material_id" :material-id="row.material_id" :material-pk="row.material_pk" /></template></el-table-column>
        <el-table-column label="任务" min-width="180"><template #default="{ row }"><el-button link @click="openDetail(row.id)">{{ row.id }}</el-button><span class="identity-meta">{{ row.workflow_version }}</span></template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="{ row }"><StageStatusBadge :status="row.business_status === 'blocked' ? 'blocked' : row.status" /></template></el-table-column>
        <el-table-column label="当前阶段 / 尝试" min-width="180"><template #default="{ row }">{{ row.current_stage_key || '—' }}<span class="identity-meta">尝试 {{ row.current_attempt || 0 }} · {{ row.current_stage_status }}</span></template></el-table-column>
        <el-table-column label="输入 / 输出归属" min-width="230"><template #default="{ row }"><span class="mono-note" :title="row.source_popo_manifest?.object">Popo {{ row.source_popo_manifest?.object?.split('/').at(-2) || '—' }}</span><span class="identity-meta">{{ jobOutputLabel(row) }}</span><span v-if="row.current_material_output_id && row.current_material_output_id !== row.job_output_id" class="identity-meta">材料当前基线 Output {{ row.current_material_output_id }}</span></template></el-table-column>
        <el-table-column label="恢复与交付" min-width="220"><template #default="{ row }">最后成功：{{ stageLabel(row.last_successful_stage_key) }}<span class="identity-meta">最小恢复：{{ stageLabel(row.minimal_resume_stage_key) }}</span><span class="identity-meta">{{ row.deliverable_available ? '已有可下载交接产物' : '尚无完整交接产物' }}</span></template></el-table-column>
        <el-table-column label="问题" min-width="210"><template #default="{ row }"><span class="error-note">{{ compactError(row).label }}</span><span v-if="compactError(row).code" class="identity-meta">{{ compactError(row).code }}</span></template></el-table-column>
        <el-table-column label="更新时间" width="170"><template #default="{ row }">{{ formatDateTime(row.updated_at || row.created_at || '') }}</template></el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link @click="openDetail(row.id)">详情</el-button>
            <el-button link @click="openMaterial(row)">材料谱系</el-button>
            <el-button v-if="row.status === 'queued'" link @click="runJob(row)">运行</el-button>
            <el-button v-if="row.status === 'failed'" link @click="retryJob(row)">重试失败阶段</el-button>
            <el-button v-if="row.status === 'succeeded' && row.review_asset_id" link @click="openReview(row)">比对审阅</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="workspace-pagination"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" /></div>
    </section>

    <el-dialog v-model="createOpen" title="从解析就绪资产创建 Worker V2.3 任务" width="1040px">
      <div class="workspace-filters" style="margin-bottom: 12px">
        <el-input v-model="materialSearch" placeholder="搜索教材" style="width: 300px" @keyup.enter="loadEligibleMaterials"><template #prefix><el-icon><Search /></el-icon></template></el-input>
        <el-button @click="loadEligibleMaterials">查询</el-button>
      </div>
      <el-table v-loading="materialLoading" :data="eligibleMaterials" row-key="id" max-height="460" @selection-change="onMaterialSelection">
        <el-table-column type="selection" width="46" />
        <el-table-column label="教材" min-width="290"><template #default="{ row }"><MaterialIdentity :filename="row.filename" :material-id="row.material_id" :material-pk="row.id" /></template></el-table-column>
        <el-table-column label="Popo Run" min-width="180"><template #default="{ row }"><span class="mono-note">{{ row.popo_run_id }}</span></template></el-table-column>
        <el-table-column label="可用精修产物" width="155"><template #default="{ row }"><StageStatusBadge :status="row.refinement_output_status" :label="refinementOutputLabel(row)" /></template></el-table-column>
        <el-table-column label="最新精修任务" width="145"><template #default="{ row }"><StageStatusBadge :status="row.latest_refinement_status" :label="latestRefinementLabel(row)" /></template></el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedMaterials.length" :loading="creating" @click="createJobs">创建并提交 {{ selectedMaterials.length }} 个任务</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" size="82%" :title="selectedJob ? `Worker V2.3 · ${selectedJob.id}` : '精修任务详情'" @closed="closeDetail">
      <div v-loading="detailLoading">
        <el-descriptions v-if="selectedJob" :column="3" border class="detail-section">
          <el-descriptions-item label="状态"><StageStatusBadge :status="selectedJob.business_status === 'blocked' ? 'blocked' : selectedJob.status" /></el-descriptions-item>
          <el-descriptions-item label="教材">#{{ selectedJob.material_pk }} · {{ selectedJob.material_id }}</el-descriptions-item>
          <el-descriptions-item label="当前阶段">{{ selectedJob.current_stage_key }} / {{ currentStageStatus(selectedJob) }}</el-descriptions-item>
          <el-descriptions-item label="输入 Popo" :span="3"><span class="mono-note">{{ selectedJob.source_popo_manifest?.bucket }}/{{ selectedJob.source_popo_manifest?.object }}</span></el-descriptions-item>
          <el-descriptions-item label="最后成功">{{ stageLabel(selectedJob.last_successful_stage_key) }}</el-descriptions-item>
          <el-descriptions-item label="最小恢复">{{ stageLabel(selectedJob.minimal_resume_stage_key) }}</el-descriptions-item>
          <el-descriptions-item label="交接产物">{{ selectedJob.deliverable_available ? '可下载' : '尚未生成' }}</el-descriptions-item>
          <el-descriptions-item label="错误" :span="3"><span class="error-note">{{ compactError(selectedJob).label }}（{{ compactError(selectedJob).code }}）</span><details><summary>查看完整技术日志</summary><pre>{{ jobErrorMessage(selectedJob) }}</pre></details></el-descriptions-item>
        </el-descriptions>

        <section v-if="selectedJob && ['needs_review', 'blocked', 'handoff_ready'].includes(selectedJob.status)" class="detail-section">
          <h3>{{ selectedJob.status === 'handoff_ready' ? '人工交接闭环' : '质量门禁阻断' }}</h3>
          <template v-if="candidate">
            <el-alert :type="selectedJob.status === 'handoff_ready' ? 'warning' : 'error'" :closable="false" :title="selectedJob.status === 'handoff_ready' ? '已生成完整可编译产物，可由人工继续完善。' : '候选产物仍触发硬质量门禁，不能视为完成。'" />
            <div class="inline-actions" style="margin-top: 12px">
              <el-button tag="a" target="_blank" :href="candidate.files?.pdf">查看候选 PDF</el-button>
              <el-button tag="a" :href="candidate.files?.latex_zip">下载待修复 ZIP</el-button>
              <el-button tag="a" :href="candidate.files?.validation">下载问题证据</el-button>
              <el-button v-if="selectedJob.status === 'handoff_ready'" type="warning" @click="handoff">登记人工接手</el-button>
              <el-button v-if="hasOpenManualHandoff(selectedJob)" type="primary" @click="revalidate">复验人工修订候选件</el-button>
              <el-button v-if="selectedJob.minimal_resume_stage_key" type="warning" @click="restartBlockedStage">从最小阻断阶段恢复</el-button>
            </div>
            <pre class="candidate-blockers">{{ JSON.stringify(candidate.blockers, null, 2) }}</pre>
          </template>
          <template v-else>
            <el-alert type="error" :closable="false" :title="`${selectedJob.current_stage_key} 阶段已阻断，尚未生成排版 PDF/ZIP。${candidateError ? ` ${candidateError}` : ''}`" />
            <div class="inline-actions" style="margin-top: 12px">
              <el-button type="warning" @click="restartBlockedStage">从最小阻断阶段恢复</el-button>
            </div>
          </template>
        </section>

        <section v-if="selectedJob" class="detail-section">
          <h3>阶段尝试</h3>
          <el-table :data="selectedJob.stages || []" size="small">
            <el-table-column prop="stage_key" label="阶段" min-width="190" />
            <el-table-column prop="attempt" label="尝试" width="80" />
            <el-table-column label="状态" width="130"><template #default="{ row }"><StageStatusBadge :status="row.status" /></template></el-table-column>
            <el-table-column prop="error_message" label="错误" min-width="220" />
          </el-table>
        </section>

        <section v-if="selectedJob" class="detail-section">
          <h3>产物与质量发现</h3>
          <el-tabs>
            <el-tab-pane :label="`本任务正式输出 ${selectedJob.outputs?.length || 0}`"><pre>{{ JSON.stringify(selectedJob.outputs || [], null, 2) }}</pre></el-tab-pane>
            <el-tab-pane :label="`产物 ${selectedJob.artifacts?.length || 0}`"><pre>{{ JSON.stringify(selectedJob.artifacts || [], null, 2) }}</pre></el-tab-pane>
            <el-tab-pane :label="`质量发现 ${selectedJob.qa_findings?.length || 0}`"><pre>{{ JSON.stringify(selectedJob.qa_findings || [], null, 2) }}</pre></el-tab-pane>
            <el-tab-pane :label="`修复记录 ${selectedJob.repair_attempts?.length || 0}`"><pre>{{ JSON.stringify(selectedJob.repair_attempts || [], null, 2) }}</pre></el-tab-pane>
          </el-tabs>
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.candidate-blockers, pre { max-height: 320px; overflow: auto; padding: 12px; border-radius: 6px; background: #111827; color: #e5e7eb; font-size: 11px; }
.status-summary { display: flex; gap: 18px; margin: 0 0 12px; padding: 10px 14px; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; }
.blocked-count { color: #dc2626; }
</style>
