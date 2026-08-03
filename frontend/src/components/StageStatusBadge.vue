<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string; label?: string }>()

const type = computed(() => {
  if (['succeeded', 'success', 'completed', 'done', 'frozen', 'available', 'passed', 'published'].includes(props.status)) return 'success'
  if (['failed', 'error', 'cancelled', 'rejected', 'blocked'].includes(props.status)) return 'danger'
  if (['queued', 'running', 'validating', 'needs_review', 'handoff_ready', 'partial', 'retrying'].includes(props.status)) return 'warning'
  return 'info'
})

const text = computed(() => props.label || ({
  queued: '排队中', running: '运行中', in_progress: '进行中', validating: '验证中', retrying: '重试中',
  succeeded: '已完成', success: '已完成', completed: '已完成', done: '已完成', passed: '已通过', published: '已发布',
  partial: '部分失败', failed: '失败', error: '错误', cancelled: '已取消', rejected: '已拒绝',
  needs_review: '质量门禁阻断', blocked: '质量门禁阻断', handoff_ready: '可人工接手', frozen: '已冻结', available: '可下载', unavailable: '不可用', missing: '缺失',
  input: '仅 PDF', mineru_done: 'MinerU 完成', popo_done: '解析完成', idle: '空闲'
} as Record<string, string>)[props.status] || props.status || '未知')
</script>

<template><el-tag :type="type" effect="plain" size="small">{{ text }}</el-tag></template>
