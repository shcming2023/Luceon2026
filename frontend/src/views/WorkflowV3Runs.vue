<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import {
  workflowV3Api,
  type WorkflowV3EligibleSource,
  type WorkflowV3Job,
  type WorkflowV3Release,
  type WorkflowV3Stage,
  type WorkflowV3Status
} from '@/api/workflowV3'
import { useCurrentUser } from '@/utils/user'
import { formatDateTime } from '@/utils/status'
import MaterialIdentity from '@/components/MaterialIdentity.vue'
import StageStatusBadge from '@/components/StageStatusBadge.vue'
import './workspace.css'

const route = useRoute()
const router = useRouter()
const currentUser = useCurrentUser()
const isPipelineAdmin = computed(() => Boolean(currentUser.value?.capabilities?.pipeline_admin))
const loading = ref(false)
const rows = ref<WorkflowV3Job[]>([])
const total = ref(0)
const page = ref(Number(route.query.page) || 1)
const pageSize = ref(20)
const machineStatus = ref(String(route.query.status || ''))
const health = ref<WorkflowV3Status | null>(null)
const releases = ref<WorkflowV3Release[]>([])

const createOpen = ref(false)
const materialLoading = ref(false)
const eligibleMaterials = ref<WorkflowV3EligibleSource[]>([])
const selectedMaterials = ref<WorkflowV3EligibleSource[]>([])
const materialSearch = ref('')
const selectedRelease = ref('')
const creating = ref(false)

const detailOpen = ref(false)
const detailLoading = ref(false)
const selectedJob = ref<WorkflowV3Job | null>(null)
let refreshTimer: number | undefined

const currentRelease = computed(() => releases.value.find(row => row.release_version === selectedRelease.value))
const finalProjection = computed(() => (
  selectedJob.value?.projection_outbox || []
).find(row => row.event_kind === 'final_ready'))
const acceptanceProjection = computed(() => (
  selectedJob.value?.projection_outbox || []
).find(row => row.event_kind === 'human_acceptance'))
const acceptanceAvailable = computed(() => (
  selectedJob.value?.delivery_status === 'projected'
  && finalProjection.value?.status === 'applied'
  && selectedJob.value?.review_entry?.available === true
  && selectedJob.value.review_entry.final_output_id === finalProjection.value?.projected_output_id
  && Boolean(finalProjection.value?.projected_manifest?.sha256)
))
const activeStatuses = new Set(['queued', 'running'])
const deliveryStatusLabels: Record<WorkflowV3Job['delivery_status'], string> = {
  projecting: '交付投影中',
  projected: '正式交付已投影',
  projection_failed: '交付投影失败'
}

function deliveryStatusLabel(job: WorkflowV3Job) {
  if (!job.spec_ready_for_projection && !(job.projection_outbox || []).length) {
    return '尚无正式交付'
  }
  return deliveryStatusLabels[job.delivery_status] || '交付投影中'
}

function specStatusLabel(job: WorkflowV3Job) {
  if (['failed', 'cancelled'].includes(job.machine_status) && job.spec_status === 'in_progress') {
    return '规范未完成'
  }
  return undefined
}

function deliveryAlertTitle(job: WorkflowV3Job) {
  if (job.delivery_status === 'projected') {
    return '独立评估、正式投影和交付复编已经通过。人工接受只记录业务决定，不会改变规范评估结果。'
  }
  if (job.delivery_status === 'projection_failed') {
    return '机器与规范门禁保持通过，但正式交付投影失败；错误证据保留，当前不能人工接受。'
  }
  if (job.machine_status === 'needs_review') {
    return '当前停在证据闭环的人工接手；尚无正式交付。完成恢复与重新验证前不能人工接受。'
  }
  if (['failed', 'cancelled'].includes(job.machine_status)) {
    return '机器执行未完成；尚无正式交付。请按失败证据从最小失败阶段恢复。'
  }
  if (job.spec_ready_for_projection) {
    return '机器与规范门禁已通过，但正式交付尚未投影为可审阅输出；当前不能人工接受。'
  }
  return '任务尚未通过全部机器与规范门禁；当前没有正式交付。'
}

function needsPolling(job: WorkflowV3Job) {
  const decisionProjection = (job.projection_outbox || []).find(
    row => row.event_kind === 'human_acceptance'
  )
  return (
    activeStatuses.has(job.machine_status)
    || (
      job.spec_ready_for_projection === true
      && job.delivery_status === 'projecting'
    )
    || (
      job.human_acceptance_decision_recorded
      && !job.human_acceptance_effective
      && !['failed', 'suppressed'].includes(decisionProjection?.status || '')
    )
  )
}

function shortHash(value?: string) {
  if (!value) return '—'
  return `${value.slice(0, 12)}…`
}

function jsonEvidence(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function openUrl(url?: string) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer')
}

function candidateStatus(candidate: Record<string, any>, stage: WorkflowV3Stage) {
  const promoted = String(stage.promotion?.candidate_id || '') === String(candidate.id || '')
  if (promoted) return `已晋级 · ${candidate.status}`
  return candidate.status || 'candidate'
}

function openExactReview() {
  const entry = selectedJob.value?.review_entry
  if (!entry?.available || !entry.review_asset_id || !entry.final_output_id) {
    ElMessage.warning('当前任务没有通过同对象交叉验证的审阅入口')
    return
  }
  router.push({
    path: '/review/compare',
    query: {
      asset_id: entry.review_asset_id,
      output_id: entry.final_output_id
    }
  })
}

async function retryProjection(outbox: Record<string, any>) {
  if (!selectedJob.value) return
  await ElMessageBox.confirm(
    `只重新排队投影 outbox #${outbox.id}；不会重跑 12 阶段或改写已晋级候选。继续？`,
    '重试交付投影',
    { type: 'warning', confirmButtonText: '重新排队', cancelButtonText: '返回' }
  )
  try {
    await workflowV3Api.retryProjection(selectedJob.value.id, String(outbox.id))
    selectedJob.value = await workflowV3Api.job(selectedJob.value.id)
    ElMessage.success('指定投影 outbox 已重新排队')
    await load(false)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '投影重试失败')
  }
}

function updateQuery() {
  router.replace({
    query: {
      ...(machineStatus.value ? { status: machineStatus.value } : {}),
      ...(page.value > 1 ? { page: String(page.value) } : {}),
      ...(selectedJob.value ? { job_id: selectedJob.value.id } : {})
    }
  })
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const [statusResult, releaseRows, jobs] = await Promise.all([
      workflowV3Api.health(),
      workflowV3Api.releases(),
      workflowV3Api.jobs({
        page: page.value,
        page_size: pageSize.value,
        machine_status: machineStatus.value || undefined
      })
    ])
    health.value = statusResult
    releases.value = releaseRows
    rows.value = jobs.items
    total.value = jobs.total
    if (!selectedRelease.value) {
      selectedRelease.value = releaseRows.find(row => row.status === 'registered')?.release_version || ''
    }
    const requested = String(route.query.job_id || '')
    if (requested && selectedJob.value?.id !== requested) await openDetail(requested)
    updateQuery()
  } catch (error: any) {
    if (showLoading) ElMessage.error(error?.response?.data?.detail || 'Worker V3 控制平面加载失败')
  } finally {
    if (showLoading) loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  void load()
}

async function openCreate() {
  createOpen.value = true
  selectedMaterials.value = []
  await loadEligibleMaterials()
}

async function loadEligibleMaterials() {
  materialLoading.value = true
  try {
    eligibleMaterials.value = await workflowV3Api.eligibleSources(materialSearch.value)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '完整冻结解析资产加载失败')
  } finally {
    materialLoading.value = false
  }
}

function onMaterialSelection(value: WorkflowV3EligibleSource[]) {
  selectedMaterials.value = value
}

function eligibleForSelection(row: WorkflowV3EligibleSource) {
  return row.eligible
}

async function createJobs() {
  if (!selectedMaterials.value.length || !selectedRelease.value) return
  creating.value = true
  try {
    if (!currentRelease.value) throw new Error('选定的技能发行包已不可用')
    const result = await workflowV3Api.createBatch(selectedMaterials.value, currentRelease.value)
    const failed = Number(result.failed || 0)
    const summary = `已登记 ${Number(result.created || 0)} 个 V3 任务，复用 ${Number(result.existing || 0)} 个；失败 ${failed} 个`
    failed ? ElMessage.warning(summary) : ElMessage.success(summary)
    createOpen.value = false
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || 'Worker V3 任务创建失败')
  } finally {
    creating.value = false
  }
}

async function openDetail(jobId: string) {
  detailOpen.value = true
  detailLoading.value = true
  try {
    selectedJob.value = await workflowV3Api.job(jobId)
    updateQuery()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || 'Worker V3 任务详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function retry(job: WorkflowV3Job) {
  await ElMessageBox.confirm(
    `只从 ${job.current_stage_key} 的最小失败点创建新尝试？已经晋级的上游产物不会重跑。`,
    '重试失败阶段',
    { type: 'warning', confirmButtonText: '重试', cancelButtonText: '取消' }
  )
  try {
    await workflowV3Api.retry(job.id)
    ElMessage.success('已创建最小失败阶段的新尝试')
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重试失败')
  }
}

async function cancelJob(job: WorkflowV3Job) {
  const result = await ElMessageBox.prompt(
    `取消会终止当前 ${job.current_stage_key} 尝试；迟到的 Producer/Evaluator 结果将被拒绝。请输入取消原因。`,
    '取消 Worker V3 任务',
    {
      type: 'warning',
      confirmButtonText: '确认取消',
      cancelButtonText: '返回',
      inputValidator: value => value.trim().length >= 3 || '请至少输入 3 个字符'
    }
  )
  try {
    const updated = await workflowV3Api.cancel(job.id, result.value.trim())
    if (selectedJob.value?.id === job.id) selectedJob.value = updated
    ElMessage.success('任务已取消；已晋级证据和冻结资产保持不变')
    await load(false)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '取消任务失败')
  }
}

async function accept(decision: 'accepted' | 'rejected') {
  if (!selectedJob.value) return
  if (!acceptanceAvailable.value || !finalProjection.value) {
    ElMessage.warning('正式交付尚未投影完成，不能记录人工决定')
    return
  }
  const verb = decision === 'accepted' ? '接受' : '拒绝'
  const result = await ElMessageBox.prompt(
    `这是独立于规范门禁的人工决定。请输入${verb}说明。`,
    `${verb}交付`,
    {
      confirmButtonText: verb,
      cancelButtonText: '取消',
      inputValidator: value => value.trim().length >= 3 || '请至少输入 3 个字符'
    }
  )
  try {
    selectedJob.value = await workflowV3Api.recordAcceptance(
      selectedJob.value.id,
      decision,
      result.value,
      finalProjection.value.projected_output_id || '',
      finalProjection.value.projected_manifest?.sha256 || ''
    )
    ElMessage.success(`已记录人工${verb}，历史规范证据未被改写`)
    await load(false)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || `人工${verb}登记失败`)
  }
}

async function refreshActiveState() {
  await load(false)
  if (selectedJob.value && needsPolling(selectedJob.value)) {
    try {
      selectedJob.value = await workflowV3Api.job(selectedJob.value.id)
    } catch { /* keep last verified snapshot */ }
  }
}

function closeDetail() {
  selectedJob.value = null
  const query = { ...route.query }
  delete query.job_id
  router.replace({ query })
}

watch(page, () => void load())
onMounted(async () => {
  await load()
  refreshTimer = window.setInterval(() => {
    if (rows.value.some(needsPolling) || (selectedJob.value && needsPolling(selectedJob.value))) {
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
        <span class="workspace-kicker">Stage 3 · Worker V3 Skill-Native</span>
        <h1>技能原生精修</h1>
        <p>确定性代码与受约束 LLM 生产候选；独立评估通过后才能晋级，困难样本以完整证据交接人工。</p>
      </div>
      <div class="workspace-actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :disabled="!health?.execution_enabled" @click="openCreate">创建 V3 任务</el-button>
      </div>
    </header>

    <el-alert
      v-if="health && !health.execution_enabled"
      type="warning"
      :closable="false"
      :title="`V3 当前不可执行：${health.detail}`"
      description="V2.3 不受影响。V3 只在发行包、数据库、MinIO 适配器以及 Producer、Evaluator、Promotion、Projector 四类 Worker 心跳全部通过门禁后开放。"
      style="margin-bottom: 14px"
    />

    <section class="workspace-panel">
      <div class="workspace-toolbar">
        <div class="workspace-filters">
          <el-select v-model="machineStatus" clearable placeholder="机器执行状态" style="width: 190px" @change="applyFilter">
            <el-option label="排队中" value="queued" />
            <el-option label="运行中" value="running" />
            <el-option label="需人工接手" value="needs_review" />
            <el-option label="失败" value="failed" />
            <el-option label="已取消" value="cancelled" />
            <el-option label="机器阶段完成" value="succeeded" />
          </el-select>
        </div>
        <span class="mono-note">机器完成 ≠ 规范通过 ≠ 可供用户验收 ≠ 用户已接受</span>
      </div>

      <el-table v-loading="loading" :data="rows" height="calc(100vh - 286px)" empty-text="暂无 Worker V3 任务">
        <el-table-column label="教材" min-width="250">
          <template #default="{ row }">
            <MaterialIdentity
              :filename="row.filename || row.material_id"
              :material-id="row.material_id"
              :material-pk="row.material_pk"
              :sha256="row.source_pdf_sha256"
            />
            <span v-if="row.source_identity?.verified !== true" class="error-note">冻结身份交叉验证失败</span>
          </template>
        </el-table-column>
        <el-table-column label="V3 任务 / 发行包" min-width="210">
          <template #default="{ row }">
            <el-button link @click="openDetail(row.id)">{{ row.id }}</el-button>
            <span class="identity-meta">{{ row.skill_release?.version }} · {{ shortHash(row.skill_release?.sha256) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="机器状态" width="125">
          <template #default="{ row }"><StageStatusBadge :status="row.machine_status" /></template>
        </el-table-column>
        <el-table-column label="规范门禁" width="125">
          <template #default="{ row }"><StageStatusBadge :status="row.spec_status" :label="specStatusLabel(row)" /></template>
        </el-table-column>
        <el-table-column label="交付状态" min-width="170">
          <template #default="{ row }">
            <span>{{ deliveryStatusLabel(row) }}</span>
            <span class="identity-meta">
              {{ row.human_acceptance_decision_recorded ? `人工决定：${row.human_acceptance_status}` : '人工决定：未记录' }}
              · {{ row.human_acceptance_effective ? '投影已生效' : '投影未生效' }}
            </span>
            <span
              v-for="projectionError in row.projection_errors || []"
              :key="projectionError.outbox_id"
              class="error-note"
            >
              {{ projectionError.event_kind }}：{{ projectionError.message }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="当前阶段" min-width="210">
          <template #default="{ row }">
            {{ row.current_stage_key || '—' }}
            <span class="identity-meta">12 阶段 · Producer → Evaluator → Promotion → Projector</span>
          </template>
        </el-table-column>
        <el-table-column label="问题" min-width="180">
          <template #default="{ row }"><span class="error-note">{{ row.error?.message }}</span></template>
        </el-table-column>
        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.updated_at || row.created_at || '') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link @click="openDetail(row.id)">证据详情</el-button>
            <el-button v-if="activeStatuses.has(row.machine_status)" link type="danger" @click="cancelJob(row)">取消</el-button>
            <el-button v-if="['failed', 'needs_review'].includes(row.machine_status)" link @click="retry(row)">最小阶段重试</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="workspace-pagination">
        <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" />
      </div>
    </section>

    <el-dialog v-model="createOpen" title="从完整冻结解析资产创建 Worker V3 任务" width="980px">
      <el-alert
        type="info"
        :closable="false"
        title="创建时锁定技能发行包、模板、源 PDF、MinerU/Popo 清单、冻结标记和归档哈希；任一对象漂移都会失败关闭。"
        style="margin-bottom: 14px"
      />
      <div class="workspace-filters" style="margin-bottom: 12px">
        <el-select v-model="selectedRelease" placeholder="选择已登记发行包" style="width: 320px">
          <el-option
            v-for="release in releases.filter(row => row.status === 'registered')"
            :key="release.id"
            :label="`${release.release_version} · ${shortHash(release.manifest_sha256)}`"
            :value="release.release_version"
          />
        </el-select>
        <el-input v-model="materialSearch" placeholder="搜索教材" style="width: 300px" @keyup.enter="loadEligibleMaterials">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button @click="loadEligibleMaterials">查询</el-button>
      </div>
      <div v-if="currentRelease" class="release-binding">
        Manifest {{ currentRelease.manifest_sha256 }} · Template {{ currentRelease.template_sha256 }} · Runtime {{ currentRelease.runtime_identity_sha256 }}
      </div>
      <el-table v-loading="materialLoading" :data="eligibleMaterials" row-key="material_pk" max-height="440" @selection-change="onMaterialSelection">
        <el-table-column type="selection" width="46" :selectable="eligibleForSelection" />
        <el-table-column label="教材" min-width="360">
          <template #default="{ row }"><MaterialIdentity :filename="row.filename" :material-id="row.material_id" :material-pk="row.material_pk" /></template>
        </el-table-column>
        <el-table-column label="冻结解析血缘" min-width="280">
          <template #default="{ row }">
            <span class="mono-note">MinerU {{ row.mineru_run_id || '—' }}</span>
            <span class="identity-meta">Popo {{ row.popo_run_id || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="可追溯资产" width="180">
          <template #default="{ row }">
            <StageStatusBadge :status="row.eligible ? 'succeeded' : 'failed'" />
            <span class="identity-meta">
              {{ row.eligible ? `输入集 ${shortHash(row.input_set_sha256)}` : row.error }}
            </span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedMaterials.length || !selectedRelease" :loading="creating" @click="createJobs">
          锁定发行包并创建 {{ selectedMaterials.length }} 个任务
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="detailOpen"
      size="88%"
      :title="selectedJob ? `Worker V3 · ${selectedJob.id}` : 'Worker V3 证据详情'"
      @closed="closeDetail"
    >
      <div v-loading="detailLoading">
        <el-descriptions v-if="selectedJob" :column="4" border class="detail-section">
          <el-descriptions-item label="机器执行"><StageStatusBadge :status="selectedJob.machine_status" /></el-descriptions-item>
          <el-descriptions-item label="规范门禁"><StageStatusBadge :status="selectedJob.spec_status" :label="specStatusLabel(selectedJob)" /></el-descriptions-item>
          <el-descriptions-item label="规范就绪">{{ selectedJob.spec_ready_for_projection ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="交付投影">{{ deliveryStatusLabel(selectedJob) }}</el-descriptions-item>
          <el-descriptions-item label="验收就绪">{{ selectedJob.ready_for_user_acceptance ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="人工决定">
            {{ selectedJob.human_acceptance_decision_recorded ? selectedJob.human_acceptance_status : '未记录' }}
          </el-descriptions-item>
          <el-descriptions-item label="决定投影">{{ selectedJob.human_acceptance_effective ? '已生效' : '未生效' }}</el-descriptions-item>
          <el-descriptions-item label="教材" :span="2">
            <MaterialIdentity
              :filename="selectedJob.filename || selectedJob.material_id"
              :material-id="selectedJob.material_id"
              :material-pk="selectedJob.material_pk"
              :sha256="selectedJob.source_pdf_sha256"
            />
          </el-descriptions-item>
          <el-descriptions-item label="冻结 ReviewAsset">#{{ selectedJob.review_asset_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="当前 generation">{{ selectedJob.current_generation }}</el-descriptions-item>
          <el-descriptions-item label="工作流">{{ selectedJob.workflow_version }}</el-descriptions-item>
          <el-descriptions-item label="技能发行">{{ selectedJob.skill_release.version }}</el-descriptions-item>
          <el-descriptions-item label="模板 SHA">{{ shortHash(selectedJob.template_sha256) }}</el-descriptions-item>
          <el-descriptions-item label="技能包 SHA" :span="2"><span class="mono-note">{{ selectedJob.skill_release.sha256 }}</span></el-descriptions-item>
          <el-descriptions-item label="输入集 SHA" :span="2"><span class="mono-note">{{ selectedJob.payload?.source_evidence?.input_set_sha256 || '—' }}</span></el-descriptions-item>
          <el-descriptions-item label="MinerU SHA" :span="2"><span class="mono-note">{{ selectedJob.payload?.source_evidence?.mineru_manifest?.sha256 || '—' }}</span></el-descriptions-item>
          <el-descriptions-item label="Popo SHA" :span="2"><span class="mono-note">{{ selectedJob.source_popo_manifest.sha256 }}</span></el-descriptions-item>
          <el-descriptions-item label="源 PDF SHA" :span="2"><span class="mono-note">{{ selectedJob.payload?.source_evidence?.source_pdf?.sha256 || '—' }}</span></el-descriptions-item>
          <el-descriptions-item label="输入 Popo" :span="4">
            <span class="mono-note">{{ selectedJob.source_popo_manifest.bucket }}/{{ selectedJob.source_popo_manifest.object }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="selectedJob?.source_identity?.verified !== true"
          type="error"
          :closable="false"
          title="冻结教材身份未通过交叉验证，审阅入口已关闭"
          :description="(selectedJob?.source_identity?.errors || []).join('；')"
          class="detail-section"
        />
        <div v-if="selectedJob && activeStatuses.has(selectedJob.machine_status)" class="inline-actions detail-section">
          <el-button type="danger" plain @click="cancelJob(selectedJob)">取消当前任务</el-button>
          <span class="mono-note">取消只终止当前执行，不删除候选、评估、晋级或冻结源资产。</span>
        </div>
        <section v-if="selectedJob" class="detail-section">
          <h3>12 阶段执行、评估与晋级</h3>
          <el-table :data="selectedJob.stages || []" size="small">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="stage-evidence">
                  <h4>候选</h4>
                  <div v-if="!row.candidates?.length" class="mono-note">尚无候选</div>
                  <div v-for="candidate in row.candidates || []" :key="candidate.id" class="evidence-card">
                    <strong>#{{ candidate.id }} · {{ candidateStatus(candidate, row) }}</strong>
                    <span class="identity-meta">
                      generation {{ candidate.generation }} · {{ candidate.artifact_kind }} · SHA {{ candidate.sha256 }}
                    </span>
                  </div>
                  <h4>独立评估 findings</h4>
                  <div v-if="!row.evaluations?.length" class="mono-note">尚无独立评估</div>
                  <div v-for="evaluation in row.evaluations || []" :key="evaluation.id" class="evidence-card">
                    <strong>
                      Evaluation #{{ evaluation.id }} · {{ evaluation.decision }}
                      · generation {{ evaluation.generation }}
                    </strong>
                    <span class="identity-meta">
                      Candidate #{{ evaluation.candidate_id }} · {{ evaluation.evaluator_identity }} / {{ evaluation.evaluator_version }}
                    </span>
                    <div v-if="!evaluation.findings?.length" class="mono-note">无 findings</div>
                    <div v-for="(finding, findingIndex) in evaluation.findings || []" :key="findingIndex" class="finding-card">
                      <div><strong>{{ finding.code || finding.kind || `finding ${findingIndex + 1}` }}</strong> · {{ finding.message || finding.summary || '—' }}</div>
                      <div class="identity-meta">
                        responsible_stage {{ finding.responsible_stage || '—' }}
                        · recovery_stage {{ finding.recovery_stage || '—' }}
                      </div>
                      <div class="evidence-grid">
                        <div>
                          <span class="field-label">evidence</span>
                          <pre>{{ jsonEvidence(finding.evidence_refs || finding.evidence) }}</pre>
                        </div>
                        <div>
                          <span class="field-label">handoff</span>
                          <pre>{{ jsonEvidence(finding.handoff) }}</pre>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="stage_key" label="阶段" min-width="210" />
            <el-table-column prop="attempt" label="尝试" width="75" />
            <el-table-column prop="generation" label="Generation" width="105" />
            <el-table-column prop="owner" label="责任方" min-width="150" />
            <el-table-column label="机器" width="125">
              <template #default="{ row }"><StageStatusBadge :status="row.machine_status" /></template>
            </el-table-column>
            <el-table-column label="规范" width="125">
              <template #default="{ row }"><StageStatusBadge :status="row.spec_status" /></template>
            </el-table-column>
            <el-table-column label="候选 / 评估 / 晋级" min-width="240">
              <template #default="{ row }">
                候选 {{ row.candidates?.length || 0 }} · 评估 {{ row.evaluations?.length || 0 }}
                <span class="identity-meta">Promotion {{ row.promotion?.id || '—' }} · {{ shortHash(row.promotion?.sha256) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="心跳 / 错误" min-width="210">
              <template #default="{ row }">
                <span class="mono-note">{{ formatDateTime(row.execution?.heartbeat_at || '') || '—' }}</span>
                <span class="error-note">{{ row.error?.message }}</span>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="selectedJob" class="detail-section">
          <h3>受约束模型调用与成本证据</h3>
          <el-alert
            type="info"
            :closable="false"
            title="模型调用仅服务于发行包声明的受约束阶段；困难样本以 succeeded 或证据闭环的 needs_review 收口。"
            style="margin-bottom: 10px"
          />
          <el-table :data="selectedJob.model_calls || []" size="small" empty-text="本任务尚未调用受约束模型">
            <el-table-column prop="kind" label="通道" width="130" />
            <el-table-column prop="stage_key" label="阶段" min-width="180" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column prop="model" label="模型" min-width="180" />
            <el-table-column prop="input_sha256" label="输入 SHA" min-width="180" />
            <el-table-column label="输出 SHA" min-width="180">
              <template #default="{ row }">
                {{ row.output_sha256 || '—' }}
              </template>
            </el-table-column>
            <el-table-column label="用量" min-width="180">
              <template #default="{ row }"><pre class="mono-note">{{ jsonEvidence(row.usage) }}</pre></template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="selectedJob && (selectedJob.review_resolutions || []).length" class="detail-section">
          <h3>人工接手与恢复 generation</h3>
          <el-table :data="selectedJob.review_resolutions || []" size="small">
            <el-table-column prop="id" label="Resolution" width="110" />
            <el-table-column prop="evaluation_id" label="Evaluation" width="110" />
            <el-table-column prop="recovery_stage" label="Recovery stage" min-width="210" />
            <el-table-column label="Generation" width="180">
              <template #default="{ row }">{{ row.source_generation }} → {{ row.recovery_generation }}</template>
            </el-table-column>
            <el-table-column prop="authorized_by" label="授权人" min-width="180" />
            <el-table-column label="证据" min-width="260">
              <template #default="{ row }">
                <span class="mono-note">{{ row.manifest?.sha256 || '—' }}</span>
                <span class="identity-meta">findings {{ row.finding_fingerprints?.length || 0 }}</span>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="selectedJob" class="detail-section">
          <h3>正式交付投影与人工决定</h3>
          <el-alert
            :type="selectedJob.delivery_status === 'projected' ? 'success' : selectedJob.delivery_status === 'projection_failed' ? 'error' : 'warning'"
            :closable="false"
            :title="deliveryAlertTitle(selectedJob)"
          />
          <el-table
            :data="selectedJob.projection_outbox || []"
            size="small"
            empty-text="尚未生成投影 outbox"
            style="margin-top: 10px"
          >
            <el-table-column prop="event_kind" label="投影事件" width="180" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column prop="attempt_count" label="尝试" width="80" />
            <el-table-column label="输出 / Manifest" min-width="240">
              <template #default="{ row }">
                Output {{ row.projected_output_id || '—' }}
                <span class="identity-meta">Manifest {{ shortHash(row.projected_manifest?.sha256) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="错误" min-width="260">
              <template #default="{ row }"><span class="error-note">{{ row.last_error || '—' }}</span></template>
            </el-table-column>
            <el-table-column v-if="isPipelineAdmin" label="管理员操作" width="130">
              <template #default="{ row }">
                <el-button
                  v-if="row.status === 'failed'"
                  link
                  type="warning"
                  @click="retryProjection(row)"
                >
                  重试此投影
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="delivery-downloads">
            <div>
              <strong>最终 Output ID</strong>
              <span class="identity-meta">{{ selectedJob.review_entry?.final_output_id || selectedJob.final_output_id || '尚未精确投影' }}</span>
            </div>
            <div v-if="selectedJob.delivery_assets?.candidate?.volumes?.length">
              <strong>已晋级候选 ZIP / PDF</strong>
              <div v-for="volume in selectedJob.delivery_assets.candidate.volumes" :key="`candidate-${volume.volume_id}`" class="download-row">
                <span>{{ volume.label }} · {{ volume.volume_id }}</span>
                <el-button link @click="openUrl(volume.zip_url)">候选 ZIP</el-button>
                <el-button link @click="openUrl(volume.pdf_url)">候选 PDF</el-button>
              </div>
            </div>
            <div v-if="selectedJob.delivery_assets?.projected_candidate?.volumes?.length">
              <strong>正式命名空间中的待接受输出</strong>
              <div v-for="volume in selectedJob.delivery_assets.projected_candidate.volumes" :key="`projected-${volume.volume_id}`" class="download-row">
                <span>{{ volume.label }} · Output {{ selectedJob.delivery_assets?.projected_candidate.output_id }}</span>
                <el-button link @click="openUrl(volume.zip_url)">待接受 ZIP</el-button>
                <el-button link @click="openUrl(volume.pdf_url)">待接受 PDF</el-button>
              </div>
            </div>
            <div v-if="selectedJob.delivery_assets?.formal?.volumes?.length">
              <strong>人工接受后的正式输出</strong>
              <div v-for="volume in selectedJob.delivery_assets.formal.volumes" :key="`formal-${volume.volume_id}`" class="download-row">
                <span>{{ volume.label }} · Output {{ selectedJob.delivery_assets?.formal.output_id }}</span>
                <el-button link @click="openUrl(volume.zip_url)">正式 ZIP</el-button>
                <el-button link @click="openUrl(volume.pdf_url)">正式 PDF</el-button>
              </div>
            </div>
          </div>
          <div class="mono-note" style="margin-top: 8px">
            人工决定 {{ selectedJob.human_acceptance_decision_recorded ? selectedJob.human_acceptance_status : '未记录' }}
            · Acceptance projection {{ acceptanceProjection?.status || 'not_recorded' }}
            · {{ selectedJob.human_acceptance_effective ? '决定已投影生效' : '决定尚未投影生效' }}
          </div>
          <div class="inline-actions" style="margin-top: 12px">
            <el-button
              v-if="acceptanceAvailable && selectedJob.review_entry?.available"
              @click="openExactReview"
            >
              打开指定输出比对审阅
            </el-button>
            <el-button type="primary" :disabled="selectedJob.human_accepted || !acceptanceAvailable" @click="accept('accepted')">接受本次交付</el-button>
            <el-button type="danger" plain :disabled="!acceptanceAvailable" @click="accept('rejected')">拒绝并记录原因</el-button>
          </div>
        </section>
      </div>
    </el-drawer>

  </div>
</template>

<style scoped>
.release-binding {
  margin-bottom: 12px;
  padding: 9px 11px;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  background: var(--bg-secondary);
  color: var(--text-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.stage-evidence {
  padding: 8px 18px 16px 46px;
}

.stage-evidence h4 {
  margin: 10px 0 6px;
}

.evidence-card,
.finding-card {
  margin-top: 8px;
  padding: 9px 11px;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  background: var(--bg-secondary);
}

.finding-card {
  background: var(--bg-primary);
}

.evidence-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 8px;
}

.evidence-grid pre {
  max-height: 180px;
  margin: 4px 0 0;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 11px;
}

.field-label {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
}

.delivery-downloads {
  display: grid;
  gap: 10px;
  margin-top: 12px;
  padding: 11px;
  border: 1px solid var(--border-light);
  border-radius: 6px;
}

.download-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}

@media (max-width: 900px) {
  .evidence-grid {
    grid-template-columns: 1fr;
  }
}
</style>
