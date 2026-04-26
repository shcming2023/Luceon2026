import { exec } from 'child_process';
import assert from 'assert';
import path from 'path';

// This smoke test verifies the basic ingestion loop requirements for the sidecar architecture.

async function runTest() {
  console.log('=== MinerU Sidecar Ingestion Smoke Test ===\n');

  // 1. Verify observer script can be parsed and executed up to the loop
  console.log('Test 1: ops/mineru-log-observer.mjs can start without missing dependencies');
  await new Promise((resolve, reject) => {
    const observerPath = path.resolve(process.cwd(), 'ops/mineru-log-observer.mjs');
    const child = exec(`node -e "setTimeout(() => process.exit(0), 500); require('${observerPath.replace(/\\/g, '/')}');"`);
    
    let output = '';
    child.stdout.on('data', data => output += data);
    child.stderr.on('data', data => output += data);
    
    child.on('close', code => {
      if (code === 0 && output.includes('[host-observer] Starting host log observer')) {
        console.log('Test 1 Pass ✅\n');
        resolve();
      } else {
        console.error('Test 1 Failed. Output:', output);
        reject(new Error('Observer failed to start or threw an error'));
      }
    });
  });

  console.log('All Sidecar tests passed ✅');
  process.exit(0);
}

runTest().catch(err => {
  console.error('Smoke test failed:', err);
  process.exit(1);
});
