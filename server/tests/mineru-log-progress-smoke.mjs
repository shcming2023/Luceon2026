import { parseTqdmLine, parseLatestMineruProgress } from '../lib/ops-mineru-log-parser.mjs';
import { ParseTaskWorker } from '../services/queue/task-worker.mjs';
import fs from 'fs';
import path from 'path';

async function run() {
  console.log('=== MinerU Log Progress Smoke Test ===');
  let failed = false;

  // 1. Test regex parsing
  const testCases = [
    {
      name: 'Predict phase',
      input: 'Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]',
      expected: { phase: 'Predict', percent: 52, current: 14, total: 27 }
    },
    {
      name: 'Invalid line',
      input: '2026-04-25 10:00:00 INFO: Starting MinerU...',
      expected: null
    }
  ];

  for (const tc of testCases) {
    const result = parseTqdmLine(tc.input);
    if (tc.expected === null) {
      if (result !== null) {
        console.error(`❌ ${tc.name} Failed: Expected null, got`, result);
        failed = true;
      } else {
        console.log(`✅ ${tc.name} Passed`);
      }
    } else {
      if (!result || result.phase !== tc.expected.phase) {
        console.error(`❌ ${tc.name} Failed: Expected`, tc.expected, 'got', result);
        failed = true;
      } else {
        console.log(`✅ ${tc.name} Passed`);
      }
    }
  }

  // 2. Test stale log rejection
  const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
  if (!fs.existsSync(scratchPath)) fs.mkdirSync(scratchPath, { recursive: true });
  const mockLog = path.join(scratchPath, 'mineru-api.log');
  fs.writeFileSync(mockLog, 'Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]\n');
  const stats = fs.statSync(mockLog);
  
  // Test with minObservedAt in the future -> should reject
  const futureTime = new Date(stats.mtimeMs + 10000).toISOString();
  const staleResult = await parseLatestMineruProgress(futureTime);
  if (staleResult !== null) {
    console.error(`❌ Stale log rejection Failed: Expected null, got`, staleResult);
    failed = true;
  } else {
    console.log(`✅ Stale log rejection Passed (Old log ignored)`);
  }

  // Test with minObservedAt in the past -> should accept
  const pastTime = new Date(stats.mtimeMs - 10000).toISOString();
  const validResult = await parseLatestMineruProgress(pastTime);
  if (!validResult || validResult.phase !== 'Predict') {
    console.error(`❌ Valid log acceptance Failed: Expected Predict, got`, validResult);
    failed = true;
  } else {
    console.log(`✅ Valid log acceptance Passed (New log accepted)`);
  }

  // 3. Test Task Worker Attribution Logic (Single vs Multiple)
  const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
  let updateCalled = 0;
  worker.updateTaskWithRetry = async () => { updateCalled++; };

  // Multiple tasks -> no attribution
  await worker.observeMineruProgress([
    { id: '1', state: 'running', metadata: { mineruStatus: 'processing' } },
    { id: '2', state: 'running', metadata: { mineruStatus: 'processing' } }
  ]);
  if (updateCalled !== 0) {
    console.error(`❌ Multiple processing tasks Attribution Failed: expected 0 updates, got ${updateCalled}`);
    failed = true;
  } else {
    console.log(`✅ Multiple processing tasks Attribution Passed (Skipped)`);
  }

  // Single task with fresh log -> attribution
  await worker.observeMineruProgress([
    { id: '1', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: pastTime } }
  ]);
  if (updateCalled !== 1) {
    console.error(`❌ Single processing tasks Attribution Failed: expected 1 update, got ${updateCalled}`);
    failed = true;
  } else {
    console.log(`✅ Single processing tasks Attribution Passed (Updated)`);
  }

  if (failed) {
    process.exit(1);
  } else {
    console.log('✅ MinerU Log Progress Smoke Test Passed');
    process.exit(0);
  }
}

run();
