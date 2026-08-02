import assert from 'node:assert/strict'
import fs from 'node:fs'

const view = fs.readFileSync(
  new URL('../src/views/WorkflowV3Runs.vue', import.meta.url),
  'utf8'
)
const api = fs.readFileSync(
  new URL('../src/api/workflowV3.ts', import.meta.url),
  'utf8'
)

assert.match(view, /selectedJob\.value\?\.review_entry/)
assert.match(view, /entry\.review_asset_id/)
assert.match(view, /entry\.final_output_id/)
assert.match(view, /query:\s*\{\s*asset_id:\s*entry\.review_asset_id,\s*output_id:\s*entry\.final_output_id/s)
assert.doesNotMatch(
  view,
  /asset_id:\s*selectedJob\.review_asset_id,\s*output_id:\s*finalProjection/,
  'compare navigation must use the server-verified review entry as one exact binding'
)
assert.match(view, /finding\.responsible_stage/)
assert.match(view, /finding\.recovery_stage/)
assert.match(view, /finding\.evidence_refs \|\| finding\.evidence/)
assert.match(view, /finding\.handoff/)
assert.match(view, /困难样本以完整证据交接人工/)
assert.match(view, /人工接手与恢复 generation/)
assert.doesNotMatch(view, /Codex 深度诊断|Codex 候选|Expert Broker/)
assert.doesNotMatch(api, /expert-policy|expert-runs|Expert/)
assert.match(view, /candidate\.generation/)
assert.match(view, /evaluation\.generation/)
assert.match(view, /delivery_assets\?\.candidate/)
assert.match(view, /delivery_assets\?\.formal/)
assert.match(view, /retryProjection\(row\)/)
assert.match(view, /return '尚无正式交付'/)
assert.match(view, /return '规范未完成'/)
assert.match(view, /当前停在证据闭环的人工接手；尚无正式交付/)
assert.match(view, /机器执行未完成；尚无正式交付/)
assert.match(view, /jsonEvidence\(row\.usage\)/)
assert.doesNotMatch(view, /el-table-column prop="usage"/)

assert.match(api, /source_identity\?:/)
assert.match(api, /review_entry\?:/)
assert.match(api, /final_output_id:\s*string/)
assert.match(
  api,
  /\/workflow-v3\/admin\/jobs\/\$\{jobId\}\/projection-outbox\/\$\{outboxId\}\/retry/
)

console.log('Worker V3 stable review identity contract passed')
