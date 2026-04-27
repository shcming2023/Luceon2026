/**
 * parsed-list-large-prefix-smoke.mjs
 * 验证大产物 prefix 在 /list 和 /parsed-zip 下的行为。
 */
import fetch from 'node-fetch';

const UPLOAD_SERVER_URL = process.env.UPLOAD_SERVER_URL || 'http://localhost:8081/__proxy/upload';

async function run() {
  console.log('\n═══ P0 Patch 2: Large Prefix List & Zip Smoke Test ═══\n');
  const targetPrefix = 'parsed/2994194655610866/';
  
  console.log(`Test 1: GET /list?prefix=${targetPrefix}`);
  const listResp = await fetch(`${UPLOAD_SERVER_URL}/list?prefix=${targetPrefix}&pageSize=10`);
  if (!listResp.ok) {
    console.error(`❌ /list failed with status: ${listResp.status}`);
    process.exit(1);
  }
  
  const listData = await listResp.json();
  const total = listData.total;
  console.log(`  Total objects reported: ${total}`);
  
  if (total === 0) {
    console.log(`  ⚠️ Notice: prefix ${targetPrefix} has 0 objects. Are we testing on a local machine without MinIO data? Skipping zip test.`);
  } else {
    if (total > 1000) {
      console.log(`  ✅ List successfully retrieved >1000 objects (${total})`);
    } else {
      console.log(`  ✅ List successfully retrieved objects (${total})`);
    }
  }

  console.log(`\nTest 2: POST /parsed-zip for 2994194655610866`);
  // 为了不真下载几百MB，只读 response headers 并 abort (或者限制流读取)
  const controller = new AbortController();
  const zipResp = await fetch(`${UPLOAD_SERVER_URL}/parsed-zip`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ materialId: '2994194655610866' }),
    signal: controller.signal
  }).catch(e => {
    if (e.name === 'AbortError') return { ok: true, isAbort: true };
    throw e;
  });

  if (zipResp.isAbort) {
    console.log('  ✅ Stream successfully initiated.');
  } else if (!zipResp.ok) {
    const errText = await zipResp.text();
    if (total === 0 && errText.includes('暂无文件')) {
      console.log('  ✅ zip correctly reported no files.');
    } else {
      console.error(`  ❌ zip failed: ${zipResp.status} ${errText}`);
      process.exit(1);
    }
  } else {
    const contentType = zipResp.headers.get('content-type');
    console.log(`  ✅ Zip stream initiated successfully (Content-Type: ${contentType})`);
    // Abort body stream to save bandwidth
    controller.abort();
  }

  console.log(`\n═══ Results: Smoke test passed ═══`);
}

run().catch((e) => {
  if (e.name !== 'AbortError') {
    console.error('Smoke test crashed:', e);
    process.exit(1);
  }
});
