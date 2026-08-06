<script setup lang="ts">
import type { PipelineRunItem } from '@/types/material'
import { formatPipelineStage } from '@/utils/status'
import MaterialIdentity from './MaterialIdentity.vue'
import StageStatusBadge from './StageStatusBadge.vue'

defineProps<{ items: PipelineRunItem[]; showRecovery?: boolean }>()
defineEmits<{ recoverPopo: [item: PipelineRunItem]; retryMetadata: [item: PipelineRunItem] }>()

function manifestLabel(value: { bucket?: string; object?: string } | undefined) {
  if (!value?.bucket || !value?.object) return '尚未冻结'
  return `${value.bucket}/${value.object}`
}
</script>

<template>
  <el-table :data="items" size="small" stripe empty-text="该批次没有逐本记录">
    <el-table-column label="教材" min-width="270">
      <template #default="{ row }"><MaterialIdentity :filename="row.filename" :material-id="row.material_id" :material-pk="row.material_pk" /></template>
    </el-table-column>
    <el-table-column label="状态" width="118"><template #default="{ row }"><StageStatusBadge :status="row.status" /></template></el-table-column>
    <el-table-column label="当前阶段" width="140"><template #default="{ row }">{{ formatPipelineStage(row.current_stage) }}</template></el-table-column>
    <el-table-column label="阶段证据" min-width="330">
      <template #default="{ row }">
        <div v-for="attempt in row.attempts" :key="attempt.id" class="attempt-line">
          {{ formatPipelineStage(attempt.stage) }} #{{ attempt.attempt }} · <StageStatusBadge :status="attempt.status" />
          <span v-if="attempt.external_run_id"> · {{ attempt.external_run_id }}</span>
        </div>
        <span v-if="!row.attempts?.length" class="mono-note">尚无尝试记录</span>
        <span class="mono-note" :title="manifestLabel(row.mineru_manifest)">MinerU：{{ manifestLabel(row.mineru_manifest) }}</span>
        <span class="mono-note" :title="manifestLabel(row.popo_manifest)">Popo：{{ manifestLabel(row.popo_manifest) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="问题" min-width="180"><template #default="{ row }"><span class="error-note">{{ row.error_message }}</span></template></el-table-column>
    <el-table-column label="AI 编目" width="130">
      <template #default="{ row }">
        <StageStatusBadge :status="row.metadata_jobs?.[0]?.status || (row.status === 'succeeded' ? 'queued' : 'idle')" />
      </template>
    </el-table-column>
    <el-table-column label="操作" width="265">
      <template #default="{ row }">
        <router-link :to="{ path: '/assets', query: { material_pk: row.material_pk, search: row.material_id } }"><el-button link>材料谱系</el-button></router-link>
        <el-button v-if="['failed', 'skipped_manual_override'].includes(row.metadata_jobs?.[0]?.status)" link @click="$emit('retryMetadata', row)">重试编目</el-button>
        <el-button v-if="showRecovery && row.status === 'failed' && row.mineru_manifest?.object && !row.popo_manifest?.object" link @click="$emit('recoverPopo', row)">恢复 Popo</el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.attempt-line { color: var(--text-secondary); font-size: 12px; line-height: 1.7; }
</style>
