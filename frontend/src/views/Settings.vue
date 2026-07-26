<template>
  <div class="settings-root">
    <div class="settings-shell">
      <header class="page-header">
        <div>
          <span class="page-kicker"><el-icon><Setting /></el-icon> 系统管理</span>
          <h1>运行设置</h1>
          <p>{{ runtime.environment.name || 'development' }} · schema v{{ runtime.schema_version }}</p>
        </div>
        <div class="header-actions">
          <el-button :loading="loading.status" @click="refreshStatus"><el-icon><RefreshRight /></el-icon>刷新状态</el-button>
          <el-button type="primary" :loading="loading.saving" @click="saveRuntime"><el-icon><Check /></el-icon>保存配置</el-button>
        </div>
      </header>

      <el-tabs v-model="activeTab" class="settings-tabs">
        <el-tab-pane label="系统状态" name="status">
          <section class="panel">
            <div class="panel-title">
              <span>真实运行态</span>
              <el-tag :type="runtimeTagType">{{ runtimeStatusText }}</el-tag>
            </div>
            <div class="status-grid">
              <StatusCell label="MinIO 合约" :value="status?.minio.contract_ok ? '正常' : '异常'" :detail="status?.minio.endpoint || runtime.minio.endpoint" />
              <StatusCell label="GPU" :value="gpuStateText" :detail="`${status?.active_gpu_tasks ?? 0} 个活动任务`" />
              <StatusCell label="核心依赖" :value="status?.dependencies.ready ? '全部就绪' : '存在异常'" :detail="dependencySummary" />
              <StatusCell label="外部备份" :value="status?.backup.external_ready_count ? '可写' : '未就绪'" :detail="latestBackupText" />
            </div>
            <div v-if="status?.blockers.length || status?.warnings.length" class="issues">
              <el-tag v-for="item in status.blockers" :key="`b-${item}`" type="danger">{{ item }}</el-tag>
              <el-tag v-for="item in status.warnings" :key="`w-${item}`" type="warning">{{ item }}</el-tag>
            </div>
            <div class="dependency-table">
              <div class="table-row table-head"><span>依赖</span><span>状态</span><span>证据</span></div>
              <div v-for="row in dependencyRows" :key="row.name" class="table-row">
                <span>{{ dependencyLabel(row.name) }}</span>
                <el-tag :type="row.ready ? 'success' : 'danger'">{{ row.ready ? 'ready' : 'failed' }}</el-tag>
                <small>{{ dependencyDetail(row) }}</small>
              </div>
            </div>
          </section>
        </el-tab-pane>

        <el-tab-pane label="目标环境" name="environment">
          <section class="panel">
            <div class="panel-title"><span>目标主机身份</span><el-tag>schema v{{ runtime.schema_version }}</el-tag></div>
            <el-form label-position="top" class="form-grid">
              <el-form-item label="环境名称"><el-input v-model="runtime.environment.name" placeholder="production-mac-mini" /></el-form-item>
              <el-form-item label="公网应用 URL"><el-input v-model="runtime.environment.public_app_url" placeholder="http://152.136.183.144:15000" /></el-form-item>
            </el-form>
            <p class="hint">此处描述部署目标，不代表当前开发机就是正式生产主机。</p>
          </section>
        </el-tab-pane>

        <el-tab-pane label="MinIO 资产" name="minio">
          <section class="panel">
            <div class="panel-title"><span>MinIO</span><el-tag :type="minioReady ? 'success' : 'warning'">{{ minioReady ? '合约正常' : '待检查' }}</el-tag></div>
            <el-form :model="runtime.minio" label-position="top" class="form-grid">
              <el-form-item label="内部 Endpoint"><el-input v-model="runtime.minio.endpoint" placeholder="host.docker.internal:9000" /></el-form-item>
              <el-form-item label="公网 Endpoint"><el-input v-model="runtime.minio.public_endpoint" placeholder="http://152.136.183.144:19000" /></el-form-item>
              <el-form-item label="Access Key"><el-input v-model="runtime.minio.access_key" placeholder="留空沿用已保存值" /></el-form-item>
              <el-form-item label="Secret Key"><el-input v-model="runtime.minio.secret_key" type="password" show-password placeholder="留空沿用已保存值" /></el-form-item>
              <el-form-item label="Region"><el-input v-model="runtime.minio.region" /></el-form-item>
              <el-form-item label="HTTPS"><el-switch v-model="runtime.minio.secure" /></el-form-item>
            </el-form>
            <div class="bucket-table">
              <div class="table-row table-head"><span>Bucket</span><span>角色</span><span>状态</span></div>
              <div v-for="bucket in bucketRows" :key="bucket.bucket" class="table-row">
                <span>{{ bucket.bucket }}</span><span>{{ bucket.role }}</span>
                <el-tag :type="bucket.exists ? 'success' : 'danger'">{{ bucket.exists ? '存在' : '缺失' }}</el-tag>
              </div>
            </div>
            <div class="actions">
              <el-button :loading="loading.minio" @click="checkMinioNow"><el-icon><Connection /></el-icon>检查已保存配置</el-button>
              <el-button type="primary" :loading="loading.ensure" @click="ensureBuckets"><el-icon><FolderChecked /></el-icon>补齐合约 Bucket</el-button>
            </div>
          </section>
        </el-tab-pane>

        <el-tab-pane label="GPU 算力" name="gpu">
          <section class="panel">
            <div class="panel-title"><span>GPU Wrapper</span><el-tag :type="gpuTagType">{{ gpuStateText }}</el-tag></div>
            <el-form :model="runtime.gpu" label-position="top" class="form-grid">
              <el-form-item label="运行语义"><el-input :model-value="'按需启动（无活动任务时离线属于正常）'" disabled /></el-form-item>
              <el-form-item label="Wrapper URL"><el-input v-model="runtime.gpu.wrapper_url" placeholder="http://gpu-host:18080" /></el-form-item>
              <el-form-item label="API Key"><el-input v-model="runtime.gpu.api_key" type="password" show-password placeholder="留空沿用已保存值" /></el-form-item>
            </el-form>
            <div v-if="gpuCheck" class="probe-grid">
              <div><span>Wrapper Health</span><el-tag :type="gpuCheck.wrapper_ok ? 'success' : 'danger'">{{ gpuCheck.wrapper_ok ? 'OK' : 'FAIL' }}</el-tag></div>
              <div><span>MinerU + Popo Staged API</span><el-tag :type="gpuCheck.staged_api_ok ? 'success' : 'danger'">{{ gpuCheck.staged_api_ok ? 'OK' : 'FAIL' }}</el-tag></div>
            </div>
            <div class="actions"><el-button type="primary" :loading="loading.gpu" @click="checkGpuNow"><el-icon><Monitor /></el-icon>检查已保存配置</el-button></div>
          </section>
        </el-tab-pane>

        <el-tab-pane label="模型配置" name="models">
          <section class="panel">
            <div class="panel-title"><span>实际使用的模型</span><el-tag :type="modelCheck?.ok ? 'success' : 'info'">{{ modelCheck?.ok ? '连通正常' : '未检查' }}</el-tag></div>
            <div class="model-section">
              <div class="section-title"><strong>目录重建 LLM</strong><el-switch v-model="runtime.models.llm.enabled" /></div>
              <el-form label-position="top" class="form-grid">
                <el-form-item label="Provider"><el-input v-model="runtime.models.llm.provider" disabled /></el-form-item>
                <el-form-item label="默认模型"><el-input v-model="runtime.models.llm.default_model" /></el-form-item>
                <el-form-item label="推理模型"><el-input v-model="runtime.models.llm.reasoning_model" /></el-form-item>
                <el-form-item label="Base URL"><el-input v-model="runtime.models.llm.deepseek.base_url" /></el-form-item>
                <el-form-item label="API Key"><el-input v-model="runtime.models.llm.deepseek.api_key" type="password" show-password placeholder="留空沿用已保存值" /></el-form-item>
                <el-form-item label="输出 Token 上限"><el-input-number v-model="runtime.models.llm.outline_decision_max_tokens" :min="1000" :max="64000" :step="1000" /></el-form-item>
                <el-form-item label="全局候选上限"><el-input-number v-model="runtime.models.llm.outline_global_max_candidates" :min="1" :max="5000" /></el-form-item>
                <el-form-item label="风险候选上限"><el-input-number v-model="runtime.models.llm.outline_max_risk_candidates" :min="1" :max="1000" /></el-form-item>
              </el-form>
            </div>
            <div class="model-section">
              <div class="section-title"><strong>目录视觉核实</strong><el-switch v-model="runtime.models.vision.enabled" /></div>
              <el-form label-position="top" class="form-grid">
                <el-form-item label="Provider"><el-input v-model="runtime.models.vision.provider" disabled /></el-form-item>
                <el-form-item label="模型"><el-input v-model="runtime.models.vision.model" /></el-form-item>
                <el-form-item label="Base URL"><el-input v-model="runtime.models.vision.dashscope.base_url" /></el-form-item>
                <el-form-item label="API Key"><el-input v-model="runtime.models.vision.dashscope.api_key" type="password" show-password placeholder="留空沿用已保存值" /></el-form-item>
                <el-form-item label="视觉候选上限"><el-input-number v-model="runtime.models.vision.outline_visual_max_candidates" :min="1" :max="1000" /></el-form-item>
              </el-form>
            </div>
            <div v-if="modelCheck" class="probe-grid">
              <div v-for="(probe, name) in modelCheck.checks" :key="name"><span>{{ name }}</span><el-tag :type="probe.ok ? 'success' : probe.skipped ? 'info' : 'danger'">{{ probe.ok ? 'OK' : probe.skipped ? 'SKIP' : 'FAIL' }} · {{ probe.latency_ms }}ms</el-tag></div>
            </div>
            <div class="actions"><el-button type="primary" :loading="loading.models" @click="checkModelsNow"><el-icon><Connection /></el-icon>测试已保存配置</el-button></div>
          </section>
        </el-tab-pane>

        <el-tab-pane label="备份与告警" name="backup">
          <section class="panel">
            <div class="panel-title"><span>持久化备份作业</span><el-tag :type="backupReady ? 'success' : 'warning'">{{ backupReady ? '目标可写' : '目标未就绪' }}</el-tag></div>
            <el-form :model="runtime.backup" label-position="top" class="form-grid">
              <el-form-item label="启用定时备份"><el-switch v-model="runtime.backup.enabled" /></el-form-item>
              <el-form-item label="模式"><el-segmented v-model="runtime.backup.mode" :options="backupModes" /></el-form-item>
              <el-form-item label="开启调度"><el-switch v-model="runtime.backup.schedule_enabled" /></el-form-item>
              <el-form-item label="间隔小时"><el-input-number v-model="runtime.backup.interval_hours" :min="1" :max="720" /></el-form-item>
              <el-form-item label="包含历史 eduassets-latex"><el-switch v-model="runtime.backup.include_legacy" /></el-form-item>
              <el-form-item label="对象上限"><el-input-number v-model="runtime.backup.max_objects" :min="100" :max="5000000" :step="1000" /></el-form-item>
            </el-form>
            <p class="hint">“仅清单”不会复制资产；“完整复制”遇到对象上限或任一目标失败时会失败并产生告警，不会假成功。</p>
            <div class="target-list">
              <div v-for="target in runtime.backup.targets" :key="target.id" class="target-row">
                <el-switch v-model="target.enabled" />
                <strong>{{ target.label }}</strong>
                <el-input :model-value="target.path" disabled />
                <el-tag :type="target.status === 'ready' ? 'success' : target.enabled ? 'warning' : 'info'">{{ target.status || (target.enabled ? '未检查' : 'disabled') }}</el-tag>
              </div>
            </div>
            <div class="actions">
              <el-button :loading="loading.backup" @click="checkBackupNow"><el-icon><FolderChecked /></el-icon>检查固定目标</el-button>
              <el-button type="primary" :loading="loading.backupRun" @click="createBackupJob"><el-icon><Upload /></el-icon>创建备份作业</el-button>
            </div>

            <div class="job-list">
              <div class="table-row table-head job-row"><span>任务</span><span>模式/范围</span><span>结果</span><span>告警/操作</span></div>
              <div v-for="job in backupJobs" :key="job.id" class="table-row job-row">
                <span>#{{ job.id }} · {{ job.status }}</span>
                <small>{{ job.mode === 'manifest' ? '仅清单' : '完整复制' }} · {{ job.buckets.length }} buckets</small>
                <small>{{ job.object_count }} 对象 / {{ job.copied_count }} 次复制<span v-if="job.error_message"> · {{ job.error_message }}</span></small>
                <div class="job-actions">
                  <el-tag v-if="job.alert.level" :type="job.alert.acknowledged ? 'info' : 'danger'">{{ job.alert.acknowledged ? '已确认' : job.alert.level }}</el-tag>
                  <el-button v-if="job.alert.level && !job.alert.acknowledged" link type="primary" @click="acknowledgeAlert(job.id)">确认</el-button>
                  <el-button v-if="job.status === 'failed' || job.status === 'succeeded'" link type="primary" @click="retryBackup(job.id)">重试</el-button>
                </div>
              </div>
              <el-empty v-if="!backupJobs.length" description="尚无备份作业" :image-size="64" />
            </div>
          </section>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Connection, FolderChecked, Monitor, RefreshRight, Setting, Upload } from '@element-plus/icons-vue'
import { settingsApi } from '@/api/settings'
import type { BackupJob, BackupTarget, GpuCheckResult, ModelCheckResult, MinioCheckResult, RuntimeConfig, RuntimeStatus } from '@/api/settings'

const StatusCell = defineComponent({
  props: { label: String, value: String, detail: String },
  setup: props => () => h('div', { class: 'status-cell' }, [h('span', props.label), h('strong', props.value), h('small', props.detail || '-')])
})

const currentBuckets = ['eduassets-input', 'eduassets-mineru', 'eduassets-minerupopo', 'eduassets-raw', 'eduassets-clean', 'eduassets-standard', 'eduassets-parsed', 'eduassets-elegantbook', 'eduassets-review']
const defaultRuntime = (): RuntimeConfig => ({
  schema_version: 2,
  environment: { name: 'development', public_app_url: '' },
  minio: { endpoint: '', public_endpoint: '', secure: false, access_key: '', secret_key: '', region: 'us-east-1', contract_buckets: currentBuckets },
  gpu: { mode: 'on_demand', wrapper_url: '', api_key: '' },
  models: {
    llm: { enabled: true, provider: 'deepseek', default_model: 'deepseek-v4-flash', reasoning_model: 'deepseek-v4-pro', deepseek: { base_url: 'https://api.deepseek.com', api_key: '' }, outline_decision_max_tokens: 16000, outline_global_max_candidates: 500, outline_max_risk_candidates: 120 },
    vision: { enabled: false, provider: 'dashscope', model: 'qwen3.7-plus-2026-05-26', dashscope: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', api_key: '' }, outline_visual_max_candidates: 40 }
  },
  backup: { enabled: false, mode: 'manifest', schedule_enabled: false, interval_hours: 24, include_legacy: true, max_objects: 2000000, targets: [] }
})

const backupModes = [{ label: '仅清单（不复制）', value: 'manifest' }, { label: '完整复制', value: 'copy' }]
const activeTab = ref('status')
const runtime = ref<RuntimeConfig>(defaultRuntime())
const status = ref<RuntimeStatus | null>(null)
const minioCheck = ref<MinioCheckResult | null>(null)
const gpuCheck = ref<GpuCheckResult | null>(null)
const modelCheck = ref<ModelCheckResult | null>(null)
const backupJobs = ref<BackupJob[]>([])
const loading = reactive({ initial: false, saving: false, status: false, minio: false, ensure: false, gpu: false, models: false, backup: false, backupRun: false })

const runtimeTagType = computed(() => status.value?.status === 'ready' ? 'success' : status.value?.status === 'blocked' ? 'danger' : 'warning')
const runtimeStatusText = computed(() => !status.value ? '未检查' : status.value.status === 'ready' ? '就绪' : status.value.status === 'blocked' ? '阻断' : '警告')
const minioReady = computed(() => Boolean(minioCheck.value?.contract_ok || status.value?.minio.contract_ok))
const gpuStateText = computed(() => {
  const state = gpuCheck.value?.state || status.value?.gpu.state
  if (state === 'expected_off') return '按需关闭（正常）'
  if (state === 'ready') return '在线可用'
  if (state === 'degraded') return '服务降级'
  return state === 'offline' ? '离线' : '未检查'
})
const gpuTagType = computed(() => ['ready', 'expected_off'].includes(gpuCheck.value?.state || status.value?.gpu.state || '') ? 'success' : 'warning')
const backupReady = computed(() => Boolean(status.value?.backup.ready_count || runtime.value.backup.targets.some(target => target.status === 'ready')))
const latestBackupText = computed(() => backupJobs.value[0] ? `#${backupJobs.value[0].id} ${backupJobs.value[0].status}` : '无作业记录')
const dependencyRows = computed(() => Object.entries(status.value?.dependencies.checks || {}).map(([name, value]) => ({ name, ...value })))
const dependencySummary = computed(() => `${dependencyRows.value.filter(row => row.ready).length}/${dependencyRows.value.length || 0} ready`)
const bucketRows = computed(() => minioCheck.value?.buckets?.length ? minioCheck.value.buckets : runtime.value.minio.contract_buckets.map(bucket => ({ bucket, role: bucketRole(bucket), exists: false, created: false })))

const bucketRole = (bucket: string) => ({
  'eduassets-input': 'source_pdf', 'eduassets-mineru': 'mineru_official', 'eduassets-minerupopo': 'popo_official',
  'eduassets-raw': 'raw_master', 'eduassets-clean': 'clean_candidate', 'eduassets-standard': 'standard_master',
  'eduassets-parsed': 'archive_optional', 'eduassets-elegantbook': 'elegantbook_output', 'eduassets-review': 'review_evidence'
}[bucket] || 'unknown')
const dependencyLabel = (name: string) => ({ sqlite: 'SQLite', redis: 'Redis', workflow_database: 'Workflow MySQL', material_worker: '解析 Worker', workflow_worker: '精修 Worker', backup_worker: '备份 Worker', overleaf: 'Overleaf/XeLaTeX' }[name] || name)
const dependencyDetail = (row: { detail?: string; reason?: string; worker_id?: string; age_seconds?: number }) => row.detail || row.reason || (row.worker_id ? `${row.worker_id} · ${row.age_seconds ?? '-'}s` : '-')

const loadRuntime = async () => { loading.initial = true; try { runtime.value = await settingsApi.getRuntimeSettings() } catch { ElMessage.error('加载运行设置失败') } finally { loading.initial = false } }
const loadBackupJobs = async () => { try { backupJobs.value = await settingsApi.listBackupJobs() } catch { ElMessage.error('加载备份作业失败') } }
const refreshStatus = async () => { loading.status = true; try { status.value = await settingsApi.getRuntimeStatus(); minioCheck.value = status.value.minio; gpuCheck.value = status.value.gpu; applyBackupCheck(status.value.backup.targets); await loadBackupJobs() } catch { ElMessage.error('刷新运行状态失败') } finally { loading.status = false } }
const saveRuntime = async () => { loading.saving = true; try { runtime.value = await settingsApi.updateRuntimeSettings(runtime.value); ElMessage.success('运行设置已保存') } catch { ElMessage.error('保存运行设置失败') } finally { loading.saving = false } }
const checkMinioNow = async () => { loading.minio = true; try { minioCheck.value = await settingsApi.checkMinio(); ElMessage[minioCheck.value.contract_ok ? 'success' : 'warning'](minioCheck.value.contract_ok ? 'MinIO 合约正常' : 'MinIO 合约异常') } catch { ElMessage.error('检查 MinIO 失败') } finally { loading.minio = false } }
const ensureBuckets = async () => { loading.ensure = true; try { minioCheck.value = await settingsApi.ensureMinioBuckets(); ElMessage[minioCheck.value.contract_ok ? 'success' : 'warning'](minioCheck.value.contract_ok ? 'Bucket 合约已满足' : '仍有 Bucket 不可用') } catch { ElMessage.error('维护 Bucket 失败') } finally { loading.ensure = false } }
const checkGpuNow = async () => { loading.gpu = true; try { gpuCheck.value = await settingsApi.checkGpu(); ElMessage[gpuCheck.value.wrapper_ok && gpuCheck.value.staged_api_ok ? 'success' : 'warning'](gpuCheck.value.wrapper_ok && gpuCheck.value.staged_api_ok ? 'GPU Wrapper 与 Staged API 可用' : 'GPU 服务未完全就绪') } catch { ElMessage.error('检查 GPU 失败') } finally { loading.gpu = false } }
const checkModelsNow = async () => { loading.models = true; try { modelCheck.value = await settingsApi.checkModels(); ElMessage[modelCheck.value.ok ? 'success' : 'warning'](modelCheck.value.ok ? '模型连通正常' : '模型连通检查异常') } catch { ElMessage.error('测试模型失败') } finally { loading.models = false } }
const checkBackupNow = async () => { loading.backup = true; try { const result = await settingsApi.checkBackupTargets(); applyBackupCheck(result.targets); ElMessage[result.ready_count ? 'success' : 'warning'](result.ready_count ? '备份目标可写' : '没有可写备份目标') } catch { ElMessage.error('检查备份目标失败') } finally { loading.backup = false } }
const createBackupJob = async () => { loading.backupRun = true; try { const job = await settingsApi.createBackupJob(); await loadBackupJobs(); ElMessage.success(`备份作业 #${job.id} 已入队`) } catch { ElMessage.error('创建备份作业失败，请先保存并启用目标') } finally { loading.backupRun = false } }
const retryBackup = async (jobId: string) => { await settingsApi.retryBackupJob(jobId); await loadBackupJobs(); ElMessage.success('重试作业已入队') }
const acknowledgeAlert = async (jobId: string) => { await settingsApi.acknowledgeBackupAlert(jobId); await loadBackupJobs(); ElMessage.success('告警已确认') }
const applyBackupCheck = (targets: BackupTarget[]) => { const byId = new Map(targets.map(target => [target.id, target])); runtime.value.backup.targets = runtime.value.backup.targets.map(target => ({ ...target, ...byId.get(target.id), path: byId.get(target.id)?.path ?? target.path })) }

onMounted(async () => { await loadRuntime(); await refreshStatus() })
</script>

<style scoped>
.settings-root { width: 100%; min-height: 100%; background: var(--bg-secondary); }
.settings-shell { max-width: 1240px; margin: 0 auto; padding: 28px; }
.page-header, .panel-title, .actions, .header-actions, .section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.page-header { margin-bottom: 20px; }
.page-header h1 { margin: 5px 0; font-size: 28px; }
.page-header p, .hint { margin: 0; color: var(--text-muted); }
.page-kicker { display: flex; align-items: center; gap: 6px; color: var(--primary-color); font-size: 13px; font-weight: 650; }
.panel { padding: 22px; border: 1px solid var(--border-light); border-radius: 10px; background: var(--bg-primary); }
.panel-title { margin-bottom: 20px; font-size: 17px; font-weight: 650; }
.status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.status-cell { display: flex; min-height: 112px; flex-direction: column; gap: 8px; padding: 16px; border: 1px solid var(--border-light); border-radius: 8px; }
.status-cell span, .status-cell small, .table-row small { color: var(--text-muted); }
.status-cell strong { font-size: 17px; }
.issues { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
.bucket-table, .dependency-table, .job-list { margin-top: 20px; border: 1px solid var(--border-light); border-radius: 8px; overflow: hidden; }
.table-row { display: grid; grid-template-columns: 1.4fr 1fr 1fr; align-items: center; gap: 14px; min-height: 48px; padding: 8px 14px; border-bottom: 1px solid var(--border-light); }
.table-row:last-child { border-bottom: 0; }
.table-head { background: var(--bg-secondary); color: var(--text-muted); font-size: 12px; font-weight: 650; }
.actions { justify-content: flex-end; margin-top: 20px; }
.probe-grid { display: grid; gap: 8px; margin-top: 16px; }
.probe-grid > div { display: flex; align-items: center; justify-content: space-between; padding: 12px; border: 1px solid var(--border-light); border-radius: 7px; }
.model-section { padding: 18px 0; border-top: 1px solid var(--border-light); }
.model-section:first-of-type { border-top: 0; }
.target-list { display: grid; gap: 10px; margin-top: 18px; }
.target-row { display: grid; grid-template-columns: auto 130px 1fr auto; align-items: center; gap: 12px; }
.job-row { grid-template-columns: 120px 1fr 1.6fr 170px; }
.job-actions { display: flex; align-items: center; gap: 4px; }
.hint { margin-top: 8px; font-size: 13px; }
@media (max-width: 900px) { .status-grid, .form-grid { grid-template-columns: 1fr; } .target-row, .job-row { grid-template-columns: 1fr; } .page-header { align-items: flex-start; flex-direction: column; } }
</style>
