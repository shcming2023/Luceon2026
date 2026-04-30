import assert from 'node:assert';
import { AiMetadataWorker } from '../services/ai/metadata-worker.mjs';
import { validateAndNormalizeV02, getDefaultV02Skeleton } from '../services/ai/metadata-standard-v0.2.mjs';

async function runTests() {
  console.log('--- AI Metadata Real Sample Smoke Test Start ---');

  // Mocks
  const mockMinio = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('# mock content'); } })
  };

  const createMockProvider = (mockResult, simulateFail = false, contentWrapper = false) => {
    return {
      id: 'mock-provider',
      model: 'mock-model',
      extractMetadata: async (md) => {
        if (simulateFail) throw new Error('Simulated provider failure');
        
        let resultObj = typeof mockResult === 'string' ? mockResult : mockResult;
        if (contentWrapper) {
          resultObj = { content: JSON.stringify(resultObj) };
        }
        
        return {
          provider: 'mock-provider',
          model: 'mock-model',
          result: resultObj,
          usage: { total_duration_ms: 100 }
        };
      }
    };
  };

  global.getTaskById = async () => ({});
  global.updateJob = async () => true;
  global.logTaskEvent = async () => {};
  global.getSettings = async () => ({ aiConfig: { providers: [{ id: 'ollama', enabled: true }], ollamaTwoPassJsonRepair: true } });

  // Case 1: Cambridge IGCSE Coursebook
  console.log('Case 1: Cambridge IGCSE Coursebook');
  const worker1 = new AiMetadataWorker(mockMinio);
  worker1.extractJson = worker1.extractJson.bind(worker1); // test it isolated
  
  const mockIgcseResult = {
    source: {},
    primary_facets: {
      domain: { zh: '基础教育', en: 'K12' },
      collection: { zh: 'Cambridge IGCSE', en: 'Cambridge IGCSE' },
      curriculum: { zh: 'CIE', en: 'CIE' },
      stage: { zh: '初中', en: 'Middle School' },
      level: { zh: 'IGCSE', en: 'IGCSE' },
      subject: { zh: '英语', en: 'English' },
      resource_type: { zh: '课本', en: 'Coursebook' },
      component_role: { zh: '学生用书', en: 'Student Book' }
    },
    descriptive_metadata: {},
    search_tags: {},
    governance: { confidence: 'high', human_review_required: false },
    evidence: []
  };

  const provider1 = createMockProvider(mockIgcseResult);
  let res1;
  try {
    res1 = await worker1.executeWithFallback(provider1, 'Sample', {});
  } catch (e) {
    assert.fail(e);
  }
  let v02_1 = worker1.extractJson(res1.result);
  const norm1 = validateAndNormalizeV02(v02_1, {});
  assert.equal(norm1.primary_facets.domain.zh, '基础教育');
  assert.equal(norm1.primary_facets.collection.zh, 'Cambridge IGCSE');
  assert.equal(norm1.primary_facets.subject.zh, '英语');
  assert.equal(norm1.primary_facets.resource_type.zh, '课本');
  
  // Test rawPreview stringification for object
  let rawString1 = '';
  if (typeof res1.result === 'object') {
    rawString1 = JSON.stringify(res1.result);
  } else {
    rawString1 = String(res1.result);
  }
  assert.notEqual(rawString1, '[object Object]');
  console.log('Case 1 Pass ✅');

  // Case 2: Cambridge IGCSE Additional Mathematics (Answer Key) with content wrapper
  console.log('Case 2: Mathematics Answer Key (content wrapper)');
  const mockMathResult = {
    primary_facets: {
      domain: { zh: '基础教育' },
      collection: { zh: 'Cambridge IGCSE' },
      subject: { zh: '数学' },
      resource_type: { zh: '答案' },
      component_role: { zh: '教师用书' }
    },
    governance: { confidence: 'high', human_review_required: false }
  };
  const provider2 = createMockProvider(mockMathResult, false, true);
  let res2 = await worker1.executeWithFallback(provider2, 'Sample', {});
  let v02_2 = worker1.extractJson(res2.result);
  const norm2 = validateAndNormalizeV02(v02_2, {});
  assert.equal(norm2.primary_facets.subject.zh, '数学');
  assert.equal(norm2.primary_facets.resource_type.zh, '答案');
  assert.equal(norm2.governance.confidence, 'high');
  console.log('Case 2 Pass ✅');

  // Case 3: Non-education material (Travel Checklist)
  console.log('Case 3: Non-education material');
  const mockTravelResult = {
    primary_facets: {
      domain: { zh: '06_公司行政经营资料' },
      collection: {}, subject: {}, resource_type: {}, component_role: {}
    },
    governance: { confidence: 'low', human_review_required: true, human_review_reason: 'Not education material' }
  };
  const provider3 = createMockProvider(mockTravelResult);
  let res3 = await worker1.executeWithFallback(provider3, 'Sample', {});
  let v02_3 = worker1.extractJson(res3.result);
  const norm3 = validateAndNormalizeV02(v02_3, {});
  assert.equal(norm3.primary_facets.domain.zh, '06_公司行政经营资料');
  assert.equal(norm3.governance.confidence, 'low');
  assert.equal(norm3.governance.human_review_required, true);
  console.log('Case 3 Pass ✅');

  // Case 4: JSON parsing failure (Provider Returns Plain text)
  console.log('Case 4: JSON Failure');
  const provider4 = createMockProvider("I am an AI, I can't do this.");
  let res4 = await worker1.executeWithFallback(provider4, 'Sample', {});
  
  let jsonParseFailed = false;
  try {
    const p = worker1.extractJson(res4.result);
    if (!p || Object.keys(p).length === 0) jsonParseFailed = true;
  } catch(e) { jsonParseFailed = true; }
  
  assert.equal(jsonParseFailed, true);
  
  // Simulate processJob degradation
  let resultV02 = getDefaultV02Skeleton({}, 'low', 'AI Provider JSON 解析失败，已降级为 skeleton 结果');
  let finalResult = {
    aiClassificationDegraded: true,
    aiClassificationDegradedReason: 'AI Provider JSON 解析失败，已降级为 skeleton 结果',
    aiClassificationErrorSource: 'ollama-json-parse-failed',
    aiClassificationV02: resultV02
  };
  
  assert.equal(finalResult.aiClassificationDegraded, true);
  assert.equal(finalResult.aiClassificationErrorSource, 'ollama-json-parse-failed');
  assert.equal(finalResult.aiClassificationV02.governance.confidence, 'low');
  assert.equal(finalResult.aiClassificationV02.governance.human_review_required, true);
  assert.equal(finalResult.aiClassificationV02.governance.risk_flags.includes('skeleton_fallback'), true);
  assert.equal(finalResult.aiClassificationV02.evidence.length, 1);
  assert.equal(finalResult.aiClassificationV02.evidence[0].quote_or_summary, 'AI provider failed; fallback skeleton generated');
  
  console.log('Case 4 Pass ✅');

  // Case 5: extractJson Edge Cases
  console.log('Case 5: extractJson Edge Cases');
  
  const testJson1 = '{"success": true}';
  assert.deepEqual(worker1.extractJson(testJson1), {success: true});

  const testJson2 = '<think>I should output JSON</think>\n{"success": true}';
  assert.deepEqual(worker1.extractJson(testJson2), {success: true});

  const testJson3 = 'Here is the result:\n{"success": true}\nHope this helps!';
  assert.deepEqual(worker1.extractJson(testJson3), {success: true});

  const testJson4 = 'Some invalid text without JSON';
  let jsonFail4 = false;
  try {
    const p = worker1.extractJson(testJson4);
    if (!p || Object.keys(p).length === 0) jsonFail4 = true;
  } catch(e) { jsonFail4 = true; }
  assert.equal(jsonFail4, true);

  console.log('Case 5 Pass ✅');

  // Case 6: Two-Pass repair success test
  console.log('Case 6: Two-Pass Repair Success');
  const originalExecute = worker1.executeWithFallback;
  let callCount = 0;
  worker1.executeWithFallback = async (provider, markdown, settings) => {
    callCount++;
    if (callCount === 1) {
      assert.equal(settings.expectJson, false, 'First pass should have expectJson: false');
      return { provider: 'ollama', model: 'qwen3.5', result: 'Draft with some text...', usage: {} };
    } else {
      assert.equal(settings.expectJson, true, 'Repair pass should have expectJson: true');
      assert.equal(settings.num_predict, 3072, 'Repair pass should have num_predict: 3072');
      assert.ok(settings.temperature === 0 || settings.temperature === 0.1, 'Repair pass should have low temperature');
      return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"subject": {"zh": "数学"}}, "governance": {"confidence": "high"}}', usage: {} };
    }
  };

  const originalTransition = worker1.transition;
  let finalResultObj = null;
  worker1.transition = async (job, update, event, level, payload) => {
    if (update.state === 'review-pending' || update.state === 'confirmed') {
      finalResultObj = update.result;
    }
  };

  await worker1.processJob({ id: 'test-job', parseTaskId: 'test-task', materialId: 'm1', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, true);
  assert.equal(finalResultObj.aiClassificationRepairSucceeded, true);
  assert.equal(finalResultObj.aiClassificationDegraded, undefined);
  assert.equal(finalResultObj.aiClassificationV02.primary_facets.subject.zh, '数学');
  assert.equal(finalResultObj.aiClassificationRepairProviderDetails, undefined);
  
  console.log('Case 6 Pass ✅');
  
  // Case 7: Two-Pass repair failed test
  console.log('Case 7: Two-Pass Repair Failed');
  callCount = 0;
  worker1.executeWithFallback = async (provider, markdown, settings) => {
    callCount++;
    if (callCount === 1) {
      assert.equal(settings.expectJson, false);
      return { provider: 'ollama', model: 'qwen3.5', result: 'Draft with some text...', usage: {} };
    } else {
      assert.equal(settings.expectJson, true);
      assert.ok(settings.temperature === 0 || settings.temperature === 0.1);
      
      const parseErr = new Error(`Failed to parse JSON from Ollama response`);
      parseErr.details = {
        rawContentPreview: 'Still invalid!',
        rawContentLength: 14,
        rawLooksTruncated: false,
        rawContainsThinkTag: false,
        responseFormatRequested: true,
        expectJson: true
      };
      throw parseErr;
    }
  };
  
  await worker1.processJob({ id: 'test-job-2', parseTaskId: 'test-task-2', materialId: 'm2', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, true);
  assert.equal(finalResultObj.aiClassificationRepairSucceeded, false);
  assert.equal(finalResultObj.aiClassificationDegraded, true);
  assert.equal(finalResultObj.aiClassificationErrorSource, 'ollama-json-repair-failed');
  assert.equal(finalResultObj.aiClassificationV02.governance.risk_flags.includes('ai_provider_json_repair_failed'), true);
  assert.equal(finalResultObj.aiClassificationRepairProviderDetails.rawContentPreview, 'Still invalid!');
  assert.equal(finalResultObj.aiClassificationRepairProviderDetails.rawLooksTruncated, false);
  assert.equal(finalResultObj.aiClassificationRepairProviderDetails.expectJson, true);
  assert.equal(finalResultObj.aiClassificationRepairRetryAttempted, undefined);
  
  console.log('Case 7 Pass ✅');

  // Case 9: Repair first attempt truncated, retry success
  console.log('Case 9: Repair first attempt truncated, retry success');
  callCount = 0;
  worker1.executeWithFallback = async (provider, markdown, settings) => {
    callCount++;
    if (callCount === 1) {
      assert.equal(settings.expectJson, false);
      return { provider: 'ollama', model: 'qwen3.5', result: 'Draft with some text...', usage: {} };
    } else if (callCount === 2) {
      assert.equal(settings.expectJson, true);
      assert.equal(settings.num_predict, 3072);
      
      const parseErr = new Error(`Failed to parse JSON from Ollama response`);
      parseErr.details = {
        rawContentPreview: 'Truncated...',
        rawContentLength: 100,
        rawLooksTruncated: true,
        rawContainsThinkTag: false,
        responseFormatRequested: true,
        expectJson: true
      };
      throw parseErr;
    } else {
      assert.equal(settings.expectJson, true);
      assert.equal(settings.temperature, 0);
      assert.equal(settings.num_predict, 4096);
      return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"subject": {"zh": "数学"}}, "governance": {"confidence": "high"}}', usage: {} };
    }
  };
  
  await worker1.processJob({ id: 'test-job-9', parseTaskId: 'test-task-9', materialId: 'm9', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, true);
  assert.equal(finalResultObj.aiClassificationRepairRetryAttempted, true);
  assert.equal(finalResultObj.aiClassificationRepairRetryReason, 'raw-truncated');
  assert.equal(finalResultObj.aiClassificationRepairRetrySucceeded, true);
  assert.equal(finalResultObj.aiClassificationRepairSucceeded, true);
  assert.equal(finalResultObj.aiClassificationDegraded, undefined);
  
  console.log('Case 9 Pass ✅');

  // Case 11: Repair retry also fails
  console.log('Case 11: Repair retry also fails');
  callCount = 0;
  worker1.executeWithFallback = async (provider, markdown, settings) => {
    callCount++;
    if (callCount === 1) {
      return { provider: 'ollama', model: 'qwen3.5', result: 'Draft with some text...', usage: {} };
    } else if (callCount === 2) {
      const parseErr = new Error(`Failed to parse JSON from Ollama response`);
      parseErr.details = {
        rawContentPreview: 'Truncated 1...',
        rawContentLength: 100,
        rawLooksTruncated: true,
        rawContainsThinkTag: false,
        responseFormatRequested: true,
        expectJson: true
      };
      throw parseErr;
    } else {
      const parseErr = new Error(`Failed to parse JSON from Ollama response`);
      parseErr.details = {
        rawContentPreview: 'Truncated 2...',
        rawContentLength: 100,
        rawLooksTruncated: true,
        rawContainsThinkTag: false,
        responseFormatRequested: true,
        expectJson: true
      };
      throw parseErr;
    }
  };
  
  await worker1.processJob({ id: 'test-job-11', parseTaskId: 'test-task-11', materialId: 'm11', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, true);
  assert.equal(finalResultObj.aiClassificationRepairRetryAttempted, true);
  assert.equal(finalResultObj.aiClassificationRepairRetrySucceeded, false);
  assert.equal(finalResultObj.aiClassificationErrorSource, 'ollama-json-repair-failed');
  assert.equal(finalResultObj.aiClassificationRepairFirstFailureDetails.rawContentPreview, 'Truncated 1...');
  assert.equal(finalResultObj.aiClassificationRepairProviderDetails.rawContentPreview, 'Truncated 2...');
  
  console.log('Case 11 Pass ✅');

  worker1.executeWithFallback = originalExecute;
  worker1.transition = originalTransition;

  // Test 1: Ollama request always sends think:false
  console.log('Test 1: Ollama request always sends think:false');
  const { OllamaProvider } = await import('../services/ai/providers/ollama.mjs');
  let requestedThink = null;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    requestedThink = body.think;
    return { ok: true, json: async () => ({ message: { content: '{"a":1}' } }) };
  };
  const ollama1 = new OllamaProvider();
  await ollama1.extractMetadata('test', { expectJson: true, options: { think: true } });
  assert.equal(requestedThink, false);
  console.log('Test 1 Pass ✅');

  // Test 2: think 内容不落盘
  console.log('Test 2: think 内容不落盘');
  globalThis.fetch = async (url, options) => {
    return { ok: true, json: async () => ({ message: { content: '<think>secret reasoning</think>{"title":"ok"}' } }) };
  };
  let savedContent = '';
  const mockMinio2 = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('mock'); } }),
    saveObject: async (name, buf, type) => { savedContent = buf.toString('utf8'); }
  };
  const worker2 = new AiMetadataWorker(mockMinio2);
  worker2.createProvider = () => new OllamaProvider();
  worker2.transition = async (job, update) => { finalResultObj = update.result; };
  await worker2.processJob({ id: 'test-job-t2', parseTaskId: 't2', inputMarkdownObjectName: 'x.md' });
  assert.equal(savedContent.includes('secret reasoning'), false);
  assert.equal(savedContent, '{"title":"ok"}');
  assert.equal(finalResultObj.aiClassificationRawObjectName.includes('first-pass.txt'), true);
  console.log('Test 2 Pass ✅');

  // Test 3: repair 失败保存 raw object 摘要
  console.log('Test 3: repair 失败保存 raw object 摘要');
  let callIndex3 = 0;
  globalThis.fetch = async (url, options) => {
    callIndex3++;
    if (callIndex3 === 1) return { ok: true, json: async () => ({ message: { content: 'invalid draft' } }) };
    return { ok: true, json: async () => ({ message: { content: '{"a":1,' } }) };
  };
  let savedContents3 = [];
  const mockMinio3 = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('mock'); } }),
    saveObject: async (name, buf, type) => { savedContents3.push(name); }
  };
  const worker3 = new AiMetadataWorker(mockMinio3);
  worker3.createProvider = () => new OllamaProvider();
  worker3.transition = async (job, update) => { finalResultObj = update.result; };
  await worker3.processJob({ id: 'test-job-t3', parseTaskId: 't3', inputMarkdownObjectName: 'x.md' });
  assert.equal(finalResultObj.aiClassificationProvider, 'skeleton');
  assert.ok(finalResultObj.aiClassificationRepairProviderDetails.rawObjectName.includes('repair-retry-pass.txt'));
  assert.ok(finalResultObj.aiClassificationRepairProviderDetails.rawContentHash);
  assert.ok(finalResultObj.aiClassificationRepairProviderDetails.rawContentHead);
  assert.ok(finalResultObj.aiClassificationRepairProviderDetails.rawContentTail);
  assert.equal(savedContents3.length, 3); // draft, repair, retry
  console.log('Test 3 Pass ✅');

  // Test 4: storage 不可用不阻断 AI
  console.log('Test 4: storage 不可用不阻断 AI');
  globalThis.fetch = async (url, options) => {
    return { ok: true, json: async () => ({ message: { content: '{"title":"ok"}' } }) };
  };
  const mockMinio4 = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('mock'); } }),
    saveObject: async (name, buf, type) => { throw new Error('MinIO is down'); }
  };
  const worker4 = new AiMetadataWorker(mockMinio4);
  worker4.createProvider = () => new OllamaProvider();
  worker4.transition = async (job, update) => { finalResultObj = update.result; };
  await worker4.processJob({ id: 'test-job-t4', parseTaskId: 't4', inputMarkdownObjectName: 'x.md' });
  assert.equal(finalResultObj.aiClassificationRawPersistFailedReason, 'MinIO is down');
  console.log('Test 4 Pass ✅');

  globalThis.fetch = originalFetch;

  console.log('--- AI Metadata Real Sample Smoke Test Success ---');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
