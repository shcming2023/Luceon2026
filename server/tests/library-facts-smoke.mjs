/**
 * library-facts-smoke.mjs
 * 
 * 测试 /asset-details/:id 的 PUT merge 语义。
 */

import http from 'http';

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`❌ FAILED: ${message}`);
    testsFailed++;
  } else {
    testsPassed++;
  }
}

async function request(method, path, body = null) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: 'localhost',
      port: process.env.DB_PORT || 8789,
      path,
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {}
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: data ? JSON.parse(data) : null });
        } catch(e) {
          resolve({ status: res.statusCode, data });
        }
      });
    });
    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

async function runTest() {
  console.log('=== P0/P1 Library Facts & Asset Details Merge Smoke Test ===\n');

  console.log('Test 1: assetDetails PUT merge semantics');
  {
    const id = 'test-asset-1';
    
    // 1. Initial PUT
    const initialPayload = {
      status: 'processing',
      uploadTimestamp: 1600000000000,
      metadata: {
        parsedFilesCount: 100,
        someOldField: 'old'
      }
    };
    let res = await request('PUT', `/asset-details/${id}`, initialPayload);
    assert(res.status === 200, 'Initial PUT should succeed');

    // 2. Merge PUT
    const mergePayload = {
      status: 'completed',
      metadata: {
        newField: 'new'
      }
    };
    res = await request('PUT', `/asset-details/${id}`, mergePayload);
    assert(res.status === 200, 'Merge PUT should succeed');

    // 3. GET to verify
    res = await request('GET', `/asset-details/${id}`);
    assert(res.status === 200, 'GET should succeed');
    const data = res.data;
    
    assert(data.status === 'completed', 'status should be updated');
    assert(data.uploadTimestamp === 1600000000000, 'uploadTimestamp should not be overwritten');
    assert(data.metadata?.parsedFilesCount === 100, 'metadata.parsedFilesCount should not be overwritten');
    assert(data.metadata?.someOldField === 'old', 'metadata.someOldField should not be overwritten');
    assert(data.metadata?.newField === 'new', 'metadata.newField should be added');
    
    console.log('Test 1 Pass ✅\n');
  }

  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ All Asset Details Merge tests passed!');
    process.exit(0);
  }
}

// 确保在真实运行的 db-server 或启动一个临时的 db-server 实例上测试
runTest().catch(console.error);
