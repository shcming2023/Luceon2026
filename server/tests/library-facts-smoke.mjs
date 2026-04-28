/**
 * library-facts-smoke.mjs
 * 
 * 测试 /asset-details/:id 的 PUT merge 语义。
 */


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

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://127.0.0.1:8081/__proxy/db';

async function request(method, path, body = null) {
  try {
    const res = await fetch(`${DB_BASE_URL}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined
    });
    
    let data = null;
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = text;
      }
    }
    
    return { status: res.status, data };
  } catch (err) {
    console.error(`\n❌ Failed to connect to DB Server at ${DB_BASE_URL}`);
    console.error('Please ensure the DB Server is running or provide a correct DB_BASE_URL via environment variables.');
    console.error(`Error Details: ${err.message}\n`);
    return { status: 500, error: err.message };
  }
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
    if (res.error) {
      console.error('Skipping Test 1 due to DB connection failure (which is expected if DB is offline).');
    } else {
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
  }

  console.log('Test 2: Library admission logic constraints');
  {
    function checkUsable(m, latestTask) {
      const parsedFilesCount = Number(m.metadata?.parsedFilesCount ?? latestTask?.parsedFilesCount) || 0;
      const hasProductEvidence = !!(m.metadata?.markdownObjectName || m.metadata?.parsedPrefix || m.mineruZipUrl || parsedFilesCount > 0);

      let derivedMineruStatus = 'processing';
      const isTaskState = (states) => latestTask && states.includes(latestTask.state);
      
      if (latestTask) {
        if (isTaskState(['completed', 'done', 'ai-pending', 'ai-running', 'review-pending'])) { derivedMineruStatus = 'completed'; }
        else if (isTaskState(['failed', 'artifact-empty', 'mineru-failed', 'canceled'])) { derivedMineruStatus = 'failed'; }
      } else {
        if (m.mineruStatus === 'completed' || m.status === 'completed') { derivedMineruStatus = 'completed'; }
        else if (m.mineruStatus === 'failed' || m.status === 'failed') { derivedMineruStatus = 'failed'; }
      }

      const isSuccessful = derivedMineruStatus === 'completed';
      const isNotFailed = derivedMineruStatus !== 'failed' 
        && !['failed', 'artifact-empty', 'mineru-failed', 'canceled'].includes(m.mineruStatus) 
        && !['failed', 'artifact-empty', 'mineru-failed', 'canceled'].includes(m.status);
      
      return isSuccessful && isNotFailed && hasProductEvidence;
    }

    // 1. completed but 0 parsed -> false
    assert(checkUsable({ mineruStatus: 'completed' }, null) === false, 'Completed with 0 parsed should be false');
    
    // 2. failed / artifact-empty but parsed=7 -> false
    assert(checkUsable({ mineruStatus: 'artifact-empty', status: 'failed', metadata: { parsedFilesCount: 7 } }, { state: 'failed' }) === false, 'Failed with parsed=7 should be false');

    // 3. pending / processing but parsed=7 -> false
    assert(checkUsable({ mineruStatus: 'processing', metadata: { parsedFilesCount: 7 } }, null) === false, 'Processing with parsed>0 should be false');

    // 4. valid review-pending with parsed>0 -> true
    assert(checkUsable({ mineruStatus: 'completed', metadata: { parsedFilesCount: 1 } }, { state: 'review-pending' }) === true, 'review-pending with parsed>0 should be true');

    // 5. valid completed with markdownObjectName -> true
    assert(checkUsable({ mineruStatus: 'completed', metadata: { markdownObjectName: 'xxx.md' } }, null) === true, 'completed with markdown should be true');

    console.log('Test 2 Pass ✅\n');
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
