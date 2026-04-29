/**
 * task-retry-metadata-smoke.mjs
 * 验证人工 retry/reparse 不会继承旧 MinerU 提交、parsed 产物和 submit retry 计数。
 */

import assert from 'assert';
import { buildMetadataForNewParseRun } from '../lib/task-actions-routes.mjs';

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`[PASS] ${name}`);
  } catch (err) {
    failed += 1;
    console.error(`[FAIL] ${name}: ${err.message}`);
  }
}

const dirty = {
  mineruExecutionProfile: { backendEffective: 'pipeline' },
  mineruTaskId: 'old-mineru-id',
  mineruStatus: 'processing',
  mineruSubmittedAt: '2026-04-28T01:00:00.000Z',
  mineruStartedAt: '2026-04-28T01:01:00.000Z',
  mineruObservedProgress: { phase: 'Table-ocr det', current: 1, total: 33 },
  submitRetries: 5,
  lastSubmitError: '本地 MinerU 不可达',
  _synthetic_warn: 'mineru-status-query-timeout',
  localTimeoutOccurred: true,
  recoveredFromMisjudgedFailed: true,
  previousErrorMessage: 'old timeout',
  markdownObjectName: 'parsed/material/full.md',
  parsedPrefix: 'parsed/material/',
  parsedFilesCount: 2423,
  artifactManifestObjectName: 'parsed/material/artifact-manifest.json',
  zipObjectName: 'parsed/material/mineru-result.zip',
  aiJobId: 'ai-job-old',
};

const retryMeta = buildMetadataForNewParseRun(dirty, {
  retryOf: 'task-old',
  aiJobId: null,
  submitRetries: 0,
});

check('preserves execution profile', () => {
  assert.strictEqual(retryMeta.mineruExecutionProfile.backendEffective, 'pipeline');
});

check('clears old mineruTaskId', () => {
  assert.strictEqual(retryMeta.mineruTaskId, undefined);
});

check('clears old parsed artifacts pointers', () => {
  assert.strictEqual(retryMeta.parsedFilesCount, undefined);
  assert.strictEqual(retryMeta.parsedPrefix, undefined);
  assert.strictEqual(retryMeta.markdownObjectName, undefined);
  assert.strictEqual(retryMeta.zipObjectName, undefined);
});

check('resets submit retry counter for manual retry', () => {
  assert.strictEqual(retryMeta.submitRetries, 0);
  assert.strictEqual(retryMeta.lastSubmitError, undefined);
});

check('clears stale timeout/recovery evidence from new run', () => {
  assert.strictEqual(retryMeta.localTimeoutOccurred, undefined);
  assert.strictEqual(retryMeta.recoveredFromMisjudgedFailed, undefined);
  assert.strictEqual(retryMeta.previousErrorMessage, undefined);
});

check('keeps retry lineage', () => {
  assert.strictEqual(retryMeta.retryOf, 'task-old');
  assert.strictEqual(retryMeta.aiJobId, null);
});

console.log(`\nResults: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
