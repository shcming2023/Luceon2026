<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { materialsApi } from '@/api/materials'
import type { PipelineRun, PipelineRunItem } from '@/types/material'
import { ensureCurrentUser, useCurrentUser } from '@/utils/user'
import { formatDateTime, formatPipelineStage } from '@/utils/status'
import StageStatusBadge from '@/components/StageStatusBadge.vue'
import PipelineRunItems from '@/components/PipelineRunItems.vue'
import './workspace.css'

const route = useRoute()
const router = useRouter()
const currentUser = useCurrentUser()
const isAdmin = computed(() => Boolean(currentUser.value?.capabilities?.pipeline_admin))
const loading = ref(false)
const rows = ref<PipelineRun[]>([])
const total = ref(0)
const page = ref(Number(route.query.page) || 1)
const pageSize = ref(20)
const status = ref(String(route.query.status || ''))
const selectedRun = ref<PipelineRun | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const recovering = ref(false)
const terminalStatuses = new Set(['succeeded', 'partial', 'failed', 'cancelled'])
let refreshTimer: number | undefined

function modeLabel(mode: string) {
  if (mode === 'resume_popo') return '管理员异常恢复'
  if (mode === 'reprocess') return '新版本重解析'
  return '完整解析'
}

function workerLeaseLabel(run: PipelineRun) {
  if (terminalStatuses.has(run.status)) return '已结束 · 租约已释放'
  return run.worker_id || '等待 Worker 领取'
}

function manifestLabel(value: { bucket?: string; object?: string } | undefined) {
  if (!value?.bucket || !value?.object) return '尚未冻结'
  return `${value.bucket}/${value.object}`
}

function gpuLifecycle(run: PipelineRun) {
  return (run.summary?.gpu_lifecycle || {}) as Record<string, any>
}

function gpuShutdown(run: PipelineRun) {
  return (run.summary?.gpu_shutdown || {}) as Record<string, any>
}

function gpuLease(run: PipelineRun) {
  return (gpuLifecycle(run).lease || {}) as Record<string, any>
}

function readinessLabel(run: PipelineRun) {
  const readiness = (gpuLease(run).readiness || {}) as Record<string, any>
  if (!Object.keys(readiness).length) return '尚未形成就绪证据'
  return readiness.ready ? 'SSH / GPU / 磁盘 / wrapper 已核验' : `未就绪 · ${readiness.reason || readiness.error_domain || '原因待核验'}`
}

function readinessDisk(run: PipelineRun) {
  const readiness = (gpuLease(run).readiness || {}) as Record<string, any>
  const ssh = (readiness.ssh || {}) as Record<string, any>
  const actual = Number(ssh.disk_available_bytes || 0)
  const required = Number(ssh.disk_required_bytes || 0)
  if (!actual && !required) return '尚未测量'
  const gib = (value: number) => `${(value / 1073741824).toFixed(2)} GiB`
  return `actual ${gib(actual)} / required ${gib(required)}`
}

function eventDetail(payload: Record<string, unknown>) {
  const value = payload as Record<string, any>
  return value.error_domain || value.status || (Array.isArray(value.blockers) ? value.blockers.join('、') : '')
}

function updateQuery() {
  router.replace({ query: { ...(status.value ? { status: status.value } : {}), ...(page.value > 1 ? { page: String(page.value) } : {}), ...(selectedRun.value ? { run_id: selectedRun.value.id } : {}) } })
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const result = await materialsApi.getPipelineRuns({ page: page.value, page_size: pageSize.value, status: status.value })
    rows.value = result.runs
    total.value = result.total
    const requested = String(route.query.run_id || '')
    if (requested) {
      const listed = rows.value.find(run => run.id === requested)
      if (listed) await openDetail(listed)
      else {
        const detail = await materialsApi.getPipelineRun(requested)
        selectedRun.value = detail
        detailOpen.value = true
      }
    }
    updateQuery()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '解析任务加载失败')
  } finally {
    if (showLoading) loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  load()
}

async function openDetail(run: PipelineRun) {
  detailOpen.value = true
  detailLoading.value = true
  try {
    selectedRun.value = await materialsApi.getPipelineRun(run.id)
    updateQuery()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '任务详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selectedRun.value = null
  const query = { ...route.query }
  delete query.run_id
  router.replace({ query })
}

async function recoverPopo(item: PipelineRunItem) {
  if (!isAdmin.value) return
  await ElMessageBox.confirm(`仅重跑《${item.filename}》的 Popo，复用已冻结 MinerU 资产。该入口只用于异常恢复。`, '管理员异常恢复', { type: 'warning' })
  recovering.value = true
  try {
    const check = await materialsApi.preflightPopoResume(item.material_pk)
    if (!check.ready) throw new Error(`恢复预检未通过：${check.status || check.plan_status}`)
    const run = await materialsApi.startPopoResume(item.material_pk)
    ElMessage.success(`异常恢复任务 #${run.id} 已排队`)
    await load()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '恢复 Popo 失败')
  } finally {
    recovering.value = false
  }
}

async function retryMetadata(item: PipelineRunItem) {
  const job = item.metadata_jobs?.[0]
  if (!job) return
  try {
    await materialsApi.retryMetadataJob(item.material_pk, job.id)
    ElMessage.success('AI 元数据任务已重新排队；不会覆盖人工确认内容')
    if (selectedRun.value) await openDetail(selectedRun.value)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '编目任务重试失败')
  }
}

watch(page, () => load())
onMounted(async () => {
  try { await ensureCurrentUser() } catch { /* API will enforce authorization */ }
  await load()
  refreshTimer = window.setInterval(() => {
    if (rows.value.some(run => !terminalStatuses.has(run.status)) || (selectedRun.value && !terminalStatuses.has(selectedRun.value.status))) {
      void load(false)
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
        <span class="workspace-kicker">Stage 2 · asynchronous pipeline</span>
        <h1>解析任务</h1>
        <p>每个批次固定教材快照；MinerU 与 Popo 按模型批量串行，结果按教材独立冻结与失败隔离。</p>
      </div>
      <div class="workspace-actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="router.push('/assets')">从 PDF 资产创建批次</el-button>
      </div>
    </header>

    <el-alert type="info" :closable="false" show-icon>
      <template #title>标准执行链：MinerU 批量 → 每本拉回并冻结 → 卸载 MinerU → Popo 批量 → 每本拉回并冻结 → AI 元数据任务。失败只影响对应教材。</template>
    </el-alert>

    <section class="workspace-panel">
      <div class="workspace-toolbar">
        <div class="workspace-filters">
          <el-select v-model="status" clearable placeholder="任务状态" style="width: 170px" @change="applyFilter">
            <el-option label="排队中" value="queued" />
            <el-option label="运行中" value="running" />
            <el-option label="已完成" value="succeeded" />
            <el-option label="部分失败" value="partial" />
            <el-option label="失败" value="failed" />
          </el-select>
        </div>
        <span class="mono-note">GPU 重任务全局串行 · 批次上限 5</span>
      </div>
      <div class="mobile-record-list" aria-label="解析任务列表">
        <article v-for="row in rows" :key="`mobile-${row.id}`" class="mobile-record-card">
          <div class="identity-title">解析批次 #{{ row.id }}</div>
          <dl class="mobile-record-facts">
            <div><dt>模式</dt><dd>{{ modeLabel(row.mode) }}</dd></div>
            <div><dt>状态</dt><dd>{{ row.status }}</dd></div>
            <div><dt>当前阶段</dt><dd>{{ formatPipelineStage(row.current_stage) }}</dd></div>
            <div><dt>结果</dt><dd>共 {{ row.total }} · 成功 {{ row.success }} · 失败 {{ row.failed }}</dd></div>
            <div><dt>Worker / 租约</dt><dd>{{ workerLeaseLabel(row) }}</dd></div>
          </dl>
          <el-button link @click="openDetail(row)">查看详情</el-button>
        </article>
      </div>
      <el-table v-loading="loading" :data="rows" max-height="calc(100vh - 330px)" class="workspace-data-table" empty-text="暂无解析任务">
        <el-table-column label="批次" width="105"><template #default="{ row }"><el-button link @click="openDetail(row)">#{{ row.id }}</el-button></template></el-table-column>
        <el-table-column label="模式" width="145"><template #default="{ row }">{{ modeLabel(row.mode) }}</template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{ row }"><StageStatusBadge :status="row.status" /></template></el-table-column>
        <el-table-column label="当前阶段" min-width="145"><template #default="{ row }">{{ formatPipelineStage(row.current_stage) }}</template></el-table-column>
        <el-table-column label="逐本结果" width="190"><template #default="{ row }">共 {{ row.total }} · 成功 {{ row.success }} · 失败 {{ row.failed }}</template></el-table-column>
        <el-table-column label="Worker / 租约" min-width="195"><template #default="{ row }"><span class="mono-note">{{ workerLeaseLabel(row) }}</span><span class="identity-meta">尝试 {{ row.attempt_count }}</span></template></el-table-column>
        <el-table-column label="创建时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at || '') }}</template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link @click="openDetail(row)">查看</el-button></template></el-table-column>
      </el-table>
      <div class="workspace-pagination"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" /></div>
    </section>

    <section v-if="selectedRun && detailOpen" class="mobile-run-detail" aria-label="解析任务详情">
      <div class="identity-title">解析批次 #{{ selectedRun.id }} 详情</div>
      <dl class="mobile-record-facts">
        <div><dt>状态</dt><dd>{{ selectedRun.status }}</dd></div>
        <div><dt>启动前状态</dt><dd>{{ gpuLease(selectedRun).prior_state || '未核验' }}</dd></div>
        <div><dt>启动所有权</dt><dd>{{ gpuLease(selectedRun).started_by_pipeline ? '本任务从 Stopped 启动' : '原本运行或无所有权' }}</dd></div>
        <div><dt>SSH / 服务</dt><dd>{{ readinessLabel(selectedRun) }}</dd></div>
        <div><dt>GPU 磁盘门禁</dt><dd>{{ readinessDisk(selectedRun) }}</dd></div>
        <div><dt>生命周期</dt><dd>{{ gpuLease(selectedRun).phase || '未登记' }}</dd></div>
        <div><dt>停机结果</dt><dd>{{ gpuShutdown(selectedRun).status || '尚未执行安全停机判断' }}</dd></div>
        <div><dt>停机阻断</dt><dd>{{ (gpuShutdown(selectedRun).blockers || []).join('、') || '无' }}</dd></div>
      </dl>
      <div v-for="item in selectedRun.items || []" :key="`mobile-item-${item.id}`" class="mobile-run-item">
        <strong>{{ item.filename || item.material_id }}</strong>
        <span class="identity-meta">{{ item.material_id }}</span>
        <span class="identity-meta">MinerU：{{ manifestLabel(item.mineru_manifest) }}</span>
        <span class="identity-meta">Popo：{{ manifestLabel(item.popo_manifest) }}</span>
      </div>
      <ol class="mobile-event-list">
        <li v-for="event in selectedRun.events || []" :key="`mobile-event-${event.id}`">
          <strong>{{ formatPipelineStage(event.stage) }}</strong> · {{ event.message }}
        </li>
      </ol>
    </section>

    <el-drawer v-model="detailOpen" size="86%" :title="selectedRun ? `解析批次 #${selectedRun.id}` : '解析任务详情'" @closed="closeDetail">
      <div v-loading="detailLoading || recovering">
        <el-descriptions v-if="selectedRun" :column="3" border class="detail-section">
          <el-descriptions-item label="状态"><StageStatusBadge :status="selectedRun.status" /></el-descriptions-item>
          <el-descriptions-item label="模式">{{ modeLabel(selectedRun.mode) }}</el-descriptions-item>
          <el-descriptions-item label="当前阶段">{{ formatPipelineStage(selectedRun.current_stage) }}</el-descriptions-item>
          <el-descriptions-item label="幂等键"><span class="mono-note">{{ selectedRun.idempotency_key || '历史任务未登记' }}</span></el-descriptions-item>
          <el-descriptions-item label="心跳">{{ formatDateTime(selectedRun.heartbeat_at || '') || '—' }}</el-descriptions-item>
          <el-descriptions-item label="错误"><span class="error-note">{{ selectedRun.error_message }}</span></el-descriptions-item>
          <el-descriptions-item label="GPU 生命周期">{{ gpuLifecycle(selectedRun).status || '未由本任务管理' }}</el-descriptions-item>
          <el-descriptions-item label="启动前状态">{{ gpuLease(selectedRun).prior_state || '未核验' }}</el-descriptions-item>
          <el-descriptions-item label="启动所有权">{{ gpuLease(selectedRun).started_by_pipeline ? '本任务从 Stopped 启动' : '原本运行或无所有权' }}</el-descriptions-item>
          <el-descriptions-item label="SSH / 服务就绪">{{ readinessLabel(selectedRun) }}</el-descriptions-item>
          <el-descriptions-item label="GPU 磁盘门禁">{{ readinessDisk(selectedRun) }}</el-descriptions-item>
          <el-descriptions-item label="生命周期阶段">{{ gpuLease(selectedRun).phase || '未登记' }}</el-descriptions-item>
          <el-descriptions-item label="停机结果">{{ gpuShutdown(selectedRun).status || '尚未执行安全停机判断' }}</el-descriptions-item>
          <el-descriptions-item label="停机阻断">{{ (gpuShutdown(selectedRun).blockers || []).join('、') || '无' }}</el-descriptions-item>
        </el-descriptions>
        <section v-if="selectedRun?.events?.length" class="detail-section">
          <h3>云实例、远端阶段与冻结时间线</h3>
          <el-timeline>
            <el-timeline-item v-for="event in selectedRun.events" :key="event.id" :timestamp="formatDateTime(event.created_at || '')" :type="event.level === 'error' ? 'danger' : event.level === 'warning' ? 'warning' : 'primary'">
              <strong>{{ formatPipelineStage(event.stage) }}</strong> · {{ event.message }}
              <div v-if="eventDetail(event.payload)" class="mono-note">{{ eventDetail(event.payload) }}</div>
            </el-timeline-item>
          </el-timeline>
        </section>
        <section v-if="selectedRun" class="detail-section">
          <h3>逐本状态与阶段证据</h3>
          <PipelineRunItems :items="selectedRun.items || []" :show-recovery="isAdmin" @recover-popo="recoverPopo" @retry-metadata="retryMetadata" />
        </section>
        <el-alert v-if="!isAdmin" type="info" :closable="false" title="“恢复 Popo”仅向具备 pipeline_admin 权限的管理员显示，普通流程不会要求用户手动提交 Popo。" />
      </div>
    </el-drawer>
  </div>
</template>
