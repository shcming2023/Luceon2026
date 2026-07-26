<template>
  <div class="compare-page">
    <header class="compare-header">
      <div>
        <span class="page-kicker">审查工作台</span>
        <h1>PDF 比对审查</h1>
        <p>原 PDF · ElegantBook 编译 PDF</p>
      </div>
      <div class="compare-actions">
        <el-button v-if="compare?.material_pk" @click="openMaterialLineage">查看材料谱系</el-button>
        <el-tooltip content="刷新当前材料" placement="bottom">
          <el-button :icon="Refresh" :loading="loading" aria-label="刷新当前材料" @click="refreshCurrent" />
        </el-tooltip>
      </div>
    </header>

    <section class="compare-toolbar">
      <el-input v-model="search" clearable placeholder="搜索 material_id / 文件名" @keyup.enter="loadAssets">
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-select
        v-model="selectedAssetId"
        filterable
        placeholder="选择已有 ElegantBook 输出的材料"
        class="asset-select"
        @change="handleAssetChange"
      >
        <el-option
          v-for="row in assets"
          :key="row.id"
          :label="displayFilename(row)"
          :value="row.id"
        />
      </el-select>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        small
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
      />
    </section>

    <section v-if="selectedAsset" class="selected-strip">
      <div class="selected-summary">
        <div class="selected-copy">
          <strong>{{ displayFilename(selectedAsset) }}</strong>
          <span>{{ selectedAsset.material_id || selectedAsset.run_id || '' }}</span>
        </div>
        <div class="selected-tags">
          <el-tag size="small" :type="outputOriginTagType">{{ outputOriginLabel }}</el-tag>
          <el-tag v-if="compileStatus" size="small" :type="compileTagType">{{ compileStatus }}</el-tag>
        </div>
      </div>
      <div class="selected-controls">
        <el-select
          v-if="availableOutputs.length > 1"
          v-model="selectedOutputId"
          size="small"
          class="output-select"
          placeholder="输出版本"
          @change="loadSelectedCompare"
        >
          <el-option
            v-for="row in availableOutputs"
            :key="outputOptionValue(row)"
            :label="outputOptionLabel(row)"
            :value="outputOptionValue(row)"
          />
        </el-select>
        <el-select
          v-if="availableVolumes.length > 1"
          v-model="selectedVolumeId"
          size="small"
          class="output-select"
          placeholder="交付分卷"
        >
          <el-option
            v-for="volume in availableVolumes"
            :key="volume.volume_id"
            :label="volume.label || volume.volume_id"
            :value="volume.volume_id"
          />
        </el-select>
        <div class="download-actions">
          <el-button
            v-if="canContinueCodex"
            type="warning"
            :loading="continueCodexStarting"
            @click="continueCodexRefine"
          >
            继续精修
          </el-button>
          <el-button v-if="downloadUrls.package_zip" type="primary" :icon="Download" @click="openUrl(downloadUrls.package_zip)">
            {{ zipButtonText }}
          </el-button>
          <el-button v-if="downloadUrls.compiled_pdf" :icon="Document" @click="openUrl(downloadUrls.compiled_pdf)">下载 PDF</el-button>
          <el-button v-if="downloadUrls.compile_report" link type="primary" @click="openUrl(downloadUrls.compile_report)">编译报告</el-button>
        </div>
      </div>
    </section>

    <section class="compare-grid">
      <article class="compare-panel source-panel">
        <header>
          <strong>原 PDF</strong>
          <el-tag v-if="sourcePdfUrl" size="small" type="primary">服务端审阅副本</el-tag>
        </header>
        <PdfSourceViewer
          v-if="sourcePdfUrl"
          :url="sourcePdfUrl"
          :source-map="{ pages: [] }"
          :active-page="sourceActivePage"
          @page-change="sourceActivePage = $event"
        />
        <el-empty v-else :description="emptyText" />
      </article>

      <article class="compare-panel latex-panel">
        <header>
          <strong>ElegantBook 编译 PDF</strong>
          <el-tag v-if="latexPdfUrl" size="small" type="success">Compiled</el-tag>
        </header>
        <PdfSourceViewer
          v-if="latexPdfUrl"
          :url="latexPdfUrl"
          :source-map="{ pages: [] }"
          :active-page="latexActivePage"
          @page-change="latexActivePage = $event"
        />
        <el-empty v-else :description="compareError || '暂无编译 PDF'" />
      </article>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Download, Refresh, Search } from '@element-plus/icons-vue'
import { materialsApi } from '@/api/materials'
import { reviewApi, type LatexCompareResponse } from '@/api/review'
import PdfSourceViewer from '@/components/PdfSourceViewer.vue'
import type { FileItem } from '@/types/file'

const route = useRoute()
const router = useRouter()

const assets = ref<FileItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const loading = ref(false)
const selectedAssetId = ref('')
const selectedOutputId = ref('')
const selectedVolumeId = ref('')
const compare = ref<LatexCompareResponse | null>(null)
const compareError = ref('')
const sourceActivePage = ref(1)
const latexActivePage = ref(1)
const continueCodexStarting = ref(false)

const selectedAsset = computed(() => assets.value.find(row => row.id === selectedAssetId.value) || null)
const sourcePdfUrl = computed(() => compare.value?.source_pdf_url || (selectedAssetId.value ? reviewApi.getContentUrl(selectedAssetId.value) : ''))
const availableVolumes = computed(() => compare.value?.volumes || [])
const selectedVolume = computed(() => {
  const rows = availableVolumes.value
  return rows.find(row => row.volume_id === selectedVolumeId.value) || rows[0] || null
})
const latexPdfUrl = computed(() => selectedVolume.value?.latex_pdf_url || compare.value?.latex_pdf_url || '')
const downloadUrls = computed(() => selectedVolume.value?.download_urls || compare.value?.download_urls || {})
const availableOutputs = computed(() => compare.value?.available_outputs || [])
const outputOrigin = computed(() => String(compare.value?.output_origin || 'legacy_selfloop'))
type AvailableOutput = NonNullable<LatexCompareResponse['available_outputs']>[number]
const selectedOutputRow = computed<AvailableOutput | null>(() => {
  const target = String(compare.value?.output_id || selectedOutputId.value || '')
  return availableOutputs.value.find(row => outputOptionValue(row) === target) || null
})
const selectedQualityStatus = computed(() => {
  const manifest = compare.value?.manifest_json || {}
  const qa = manifest.qa && typeof manifest.qa === 'object' ? manifest.qa as Record<string, unknown> : {}
  return String(selectedOutputRow.value?.quality_status || qa.status || '')
})
const outputOriginLabel = computed(() => {
  return outputOriginText(outputOrigin.value, selectedQualityStatus.value)
})
const zipButtonText = computed(() => {
  if (outputOrigin.value === 'legacy_selfloop') return '导出 LaTeX ZIP'
  if (outputOrigin.value === 'worker_v3') return '下载 Worker V3 LaTeX ZIP'
  if (outputOrigin.value === 'worker_v2') return '下载 LaTeX ZIP'
  return selectedQualityStatus.value === 'passed' ? '导出精修 LaTeX ZIP' : '导出候选 LaTeX ZIP'
})
const outputOriginTagType = computed(() => {
  if (outputOrigin.value === 'worker_v3') {
    return selectedQualityStatus.value === 'passed' ? 'success' : 'warning'
  }
  if (outputOrigin.value === 'worker_v2') return 'success'
  if (outputOrigin.value === 'codex_refined' && selectedQualityStatus.value === 'passed') return 'success'
  if (outputOrigin.value === 'codex_refined') return 'warning'
  if (outputOrigin.value === 'codex_elegantbook') return 'primary'
  return 'info'
})
const canContinueCodex = computed(() => outputOrigin.value === 'codex_refined' && selectedQualityStatus.value === 'needs_fix')
const compileStatus = computed(() => {
  const report = compare.value?.compile_report || {}
  const manifest = compare.value?.manifest_json || {}
  const compile = manifest.compile && typeof manifest.compile === 'object' ? manifest.compile as Record<string, unknown> : {}
  return String(report.status || compile.status || '')
})
const compileTagType = computed(() => {
  if (compileStatus.value === 'succeeded') return 'success'
  if (compileStatus.value === 'fallback_pdf' || compileStatus.value === 'skipped') return 'warning'
  if (compileStatus.value === 'failed') return 'danger'
  return 'info'
})
const emptyText = computed(() => loading.value ? '正在加载' : '请选择材料')

function displayFilename(row: FileItem) {
  return row.title || row.input_filename || row.filename || row.material_id || `#${row.id}`
}

function outputOptionValue(row: NonNullable<LatexCompareResponse['available_outputs']>[number]) {
  return String(row.id || row.manifest?.object || row.output_run_id || '')
}

function outputOriginText(origin: string, qualityStatus = '') {
  if (origin === 'worker_v3') {
    if (qualityStatus === 'passed') return 'Worker V3 已接受交付'
    if (qualityStatus === 'ready_for_user_acceptance') return 'Worker V3 待人工接受'
    return 'Worker V3 候选'
  }
  if (origin === 'worker_v2') return qualityStatus === 'passed' ? 'Worker V2.3 核心产物' : 'Worker V2.3 候选'
  if (origin === 'codex_refined') {
    if (qualityStatus === 'passed') return 'Codex 精修'
    if (qualityStatus === 'needs_fix') return 'Codex 候选（待修复）'
    return 'Codex 候选'
  }
  if (origin === 'codex_elegantbook') return 'Codex ElegantBook'
  return 'Legacy baseline'
}

function outputOptionLabel(row: NonNullable<LatexCompareResponse['available_outputs']>[number]) {
  const origin = outputOriginText(row.origin, row.quality_status || '')
  const quality = row.quality_status && row.quality_status !== 'passed' ? ` · ${row.quality_status}` : ''
  const suffix = row.is_current ? ' · 当前' : ''
  return `${origin}${quality} · ${row.version_label || row.output_run_id || row.created_at || row.manifest?.object || '未命名'}${suffix}`
}

function apiUrl(url: string) {
  if (!url) return ''
  if (/^https?:\/\//i.test(url)) return url
  return url.startsWith('/api/') ? url : `/api${url.startsWith('/') ? url : `/${url}`}`
}

function openUrl(url: string) {
  const target = apiUrl(url)
  if (target) window.open(target, '_blank', 'noopener,noreferrer')
}

function openMaterialLineage() {
  if (!compare.value?.material_pk) return
  router.push({
    path: '/assets',
    query: { material_pk: compare.value.material_pk, search: compare.value.material_id || '' }
  })
}

function codexSkillVersion() {
  return `manual-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}`
}

async function continueCodexRefine() {
  const materialPk = selectedOutputRow.value?.material_pk || ''
  if (!materialPk) {
    ElMessage.warning('当前输出缺少 material_pk，不能创建继续精修任务')
    return
  }
  try {
    await ElMessageBox.confirm(
      '将为当前 Codex 候选创建新的异步精修任务。旧候选和 legacy baseline 都会保留，新任务不会在浏览器请求中直接长时间执行。',
      '继续 Codex 精修',
      { type: 'warning', confirmButtonText: '创建任务', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  continueCodexStarting.value = true
  try {
    const job = await materialsApi.createCodexJob(materialPk, {
      mode: 'refresh_legacy',
      skill_version: codexSkillVersion(),
      run_reason: 'continue_refine_candidate',
      force: true,
      payload: {
        source: 'compare_review_continue',
        source_output_id: selectedOutputId.value || compare.value?.output_id || '',
        source_quality_status: selectedQualityStatus.value
      }
    })
    ElMessage.success(`继续精修任务已入队：#${job.id}`)
  } finally {
    continueCodexStarting.value = false
  }
}

async function refreshCurrent() {
  if (!selectedAssetId.value) {
    await loadAssets()
    return
  }
  await loadSelectedCompare()
}

async function handleAssetChange() {
  selectedOutputId.value = ''
  selectedVolumeId.value = ''
  await loadSelectedCompare()
}

async function ensureSelectedAssetVisible(assetId: string) {
  if (!assetId || assets.value.some(row => row.id === assetId)) return
  try {
    const asset = await reviewApi.getAsset(assetId) as FileItem
    assets.value = [asset, ...assets.value]
  } catch {
    // Keep the URL-selected id; the compare endpoint will surface the real error.
  }
}

async function loadAssets() {
  loading.value = true
  compareError.value = ''
  try {
    const response = await reviewApi.getAssets({
      page: page.value,
      page_size: pageSize.value,
      search: search.value,
      view: 'compare'
    })
    assets.value = response.files
    total.value = response.total
    const routeAssetId = typeof route.query.asset_id === 'string' ? route.query.asset_id : ''
    const routeOutputId = typeof route.query.output_id === 'string' ? route.query.output_id : ''
    if (routeAssetId) {
      selectedAssetId.value = routeAssetId
      selectedOutputId.value = routeOutputId
      await ensureSelectedAssetVisible(routeAssetId)
    } else if (!selectedAssetId.value || !assets.value.some(row => row.id === selectedAssetId.value)) {
      selectedAssetId.value = assets.value[0]?.id || ''
      selectedOutputId.value = ''
    }
    await loadSelectedCompare()
  } finally {
    loading.value = false
  }
}

async function loadSelectedCompare() {
  compare.value = null
  compareError.value = ''
  sourceActivePage.value = 1
  latexActivePage.value = 1
  selectedVolumeId.value = ''
  if (!selectedAssetId.value) return
  const query: Record<string, string> = { asset_id: selectedAssetId.value }
  if (selectedOutputId.value) query.output_id = selectedOutputId.value
  void router.replace({ path: '/review/compare', query })
  try {
    compare.value = await reviewApi.getLatexCompare(selectedAssetId.value, selectedOutputId.value)
    selectedOutputId.value = compare.value.output_id || selectedOutputId.value
    selectedVolumeId.value = compare.value.volumes?.[0]?.volume_id || ''
  } catch (error: any) {
    compareError.value = error?.response?.data?.detail || error?.message || 'LaTeX 比对信息加载失败'
    ElMessage.error(compareError.value)
  }
}

watch([page, pageSize], () => {
  void loadAssets()
})

onMounted(loadAssets)
</script>

<style scoped>
.compare-page {
  display: flex;
  height: 100%;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.compare-header {
  display: flex;
  min-height: 48px;
  flex-shrink: 0;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.page-kicker {
  display: block;
  margin-bottom: 3px;
  color: var(--primary-dark);
  font-size: 10px;
  font-weight: 650;
}

.compare-header h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: 23px;
  font-weight: 720;
  line-height: 1.2;
  letter-spacing: 0;
}

.compare-header p {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 11px;
}

.compare-actions {
  flex-shrink: 0;
}

.compare-actions :deep(.el-button) {
  width: 34px;
  padding: 6px 0 !important;
}

.compare-toolbar,
.selected-strip,
.compare-panel {
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
}

.compare-toolbar {
  display: grid;
  flex-shrink: 0;
  grid-template-columns: minmax(220px, 1fr) minmax(280px, 390px) auto;
  align-items: center;
  gap: 9px;
  padding: 9px;
}

.asset-select {
  width: 100%;
}

.compare-toolbar :deep(.el-pagination) {
  justify-content: flex-end;
  white-space: nowrap;
}

.selected-strip {
  display: flex;
  flex-shrink: 0;
  align-items: stretch;
  flex-direction: column;
  gap: 7px;
  padding: 8px 10px;
}

.selected-summary {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.selected-copy {
  display: grid;
  min-width: 160px;
  flex: 1;
  gap: 1px;
}

.selected-copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-copy span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-tags,
.selected-controls,
.download-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.selected-tags,
.selected-controls {
  flex-shrink: 0;
}

.selected-controls {
  min-width: 0;
  width: 100%;
  justify-content: space-between;
  padding-top: 7px;
  border-top: 1px solid var(--border-light);
}

.output-select {
  width: min(390px, 35vw);
  flex-shrink: 0;
}

.download-actions {
  flex-wrap: nowrap;
}

.download-actions :deep(.el-button) {
  min-height: 32px;
  margin-left: 0;
  padding: 5px 9px !important;
  font-size: 12px;
}

.compare-grid {
  display: grid;
  min-height: 0;
  flex: 1;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
}

.compare-panel {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.compare-panel > header {
  display: flex;
  min-height: 40px;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border-light);
  background: #fafbfc;
  color: var(--text-primary);
}

.compare-panel > header strong {
  font-size: 12px;
  font-weight: 680;
}

.compare-panel :deep(.pdf-source-viewer) {
  height: 100%;
  min-height: 0;
  flex: 1;
  border: 0;
  border-radius: 0;
}

.compare-panel :deep(.el-empty) {
  min-height: 360px;
  flex: 1;
}

@media (max-width: 1220px) {
  .compare-toolbar {
    grid-template-columns: minmax(220px, 1fr) minmax(260px, 360px);
  }

  .compare-toolbar :deep(.el-pagination) {
    grid-column: 1 / -1;
    justify-content: flex-end;
  }

  .output-select {
    width: min(380px, 36vw);
  }
}

@media (max-width: 900px) {
  .compare-page {
    overflow: auto;
  }

  .compare-header,
  .compare-toolbar {
    align-items: stretch;
    grid-template-columns: 1fr;
  }

  .compare-header {
    align-items: flex-start;
  }

  .compare-toolbar :deep(.el-pagination) {
    grid-column: auto;
    justify-content: flex-start;
    overflow-x: auto;
  }

  .selected-summary,
  .selected-controls {
    align-items: flex-start;
    flex-direction: column;
  }

  .selected-tags,
  .download-actions {
    flex-wrap: wrap;
  }

  .output-select {
    width: 100%;
  }

  .compare-grid {
    min-height: 1100px;
    flex: none;
    grid-template-columns: 1fr;
  }

  .compare-panel {
    min-height: 540px;
  }
}
</style>
