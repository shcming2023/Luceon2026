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

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (url && typeof url === 'string') {
      if (url.includes('/materials/mat-test1')) {
        return { ok: true, json: async () => ({
          id: "mat-test1",
          fileName: "出国.pdf",
          fileSize: 33814,
          mimeType: "application/pdf",
          metadata: {
            objectName: "originals/mat-test1/source.pdf",
            parsedPrefix: "parsed/mat-test1/",
            markdownObjectName: "parsed/mat-test1/full.md",
            parsedFilesCount: 8
          }
        }) };
      }
      if (url.includes('/materials/mat-test2')) {
        return { ok: true, json: async () => ({
          id: "mat-test2"
        }) };
      }
      if (url.includes('/materials/mat-test4')) {
        return { ok: true, json: async () => ({
          id: "mat-test4",
          fileName: "出国.pdf",
          fileSize: 33814,
          mimeType: "application/pdf",
          metadata: {
            objectName: "originals/mat-test4/source.pdf",
            parsedPrefix: "parsed/mat-test4/",
            markdownObjectName: "parsed/mat-test4/full.md",
            parsedFilesCount: 8
          }
        }) };
      }
      if (url.includes('/materials/')) {
        return { ok: true, json: async () => ({}) };
      }
      if (url.includes('/tasks/test-task-16')) {
        return { ok: true, json: async () => ({
          id: 'test-task-16',
          state: 'completed',
          metadata: {
            originalFilename: "出国.pdf",
            originalFileSize: 33493,
            objectName: "originals/mat-x/source.pdf",
            parsedPrefix: "parsed/mat-x/",
            markdownObjectName: "parsed/mat-x/full.md",
            parsedFilesCount: 8,
            mimeType: "application/pdf",
            mineruExecutionProfile: { backendEffective: "pipeline" }
          }
        }) };
      }
      if (url.includes('/tasks/test-task-3')) {
        return { ok: true, json: async () => ({
          id: 'test-task',
          state: 'completed',
          metadata: {
            originalFilename: "出国_task.pdf",
            originalFileSize: 999,
            objectName: "originals/mat-test/source.pdf",
            parsedPrefix: "parsed/mat-test/",
            markdownObjectName: "parsed/mat-test/full.md",
            parsedFilesCount: 2,
            mimeType: "application/pdf",
            mineruExecutionProfile: { backendEffective: "pipeline" }
          }
        }) };
      }
      if (url.includes('/tasks/test-task-2')) {
        return { ok: true, json: async () => ({ 
          id: 'test-task-2',
          state: 'completed',
          metadata: { 
            originalFilename: 'fallback.pdf', 
            originalFileSize: 12345,
            mimeType: 'application/pdf',
            objectName: 'originals/mat-test2/source.pdf',
            parsedPrefix: 'parsed/mat-test2/',
            markdownObjectName: 'parsed/mat-test2/full.md',
            parsedFilesCount: 5,
            mineruExecutionProfile: { enableOcr: true, backendEffective: 'pipeline' } 
          }
        }) };
      }
      if (url.includes('/tasks/')) {
        return { ok: true, json: async () => ({ 
          id: 'test-task',
          state: 'completed',
          metadata: { 
            originalFilename: 'test.pdf', 
            mineruExecutionProfile: { enableOcr: true, backendEffective: 'pipeline' } 
          }
        }) };
      }
      if (url.includes('/settings')) {
        return { ok: true, json: async () => ({ aiConfig: { providers: [{ id: 'ollama', enabled: true }] } }) };
      }
      if (url.includes('/jobs')) {
        return { ok: true, json: async () => ({}) };
      }
    }
    return { ok: true, json: async () => ({}) };
  };

  global.getTaskById = async (id) => {
    if (id === 'test-task-16') {
      return {
        metadata: {
          originalFilename: "出国.pdf",
          originalFileSize: 33493,
          objectName: "originals/mat-x/source.pdf",
          parsedPrefix: "parsed/mat-x/",
          markdownObjectName: "parsed/mat-x/full.md",
          parsedFilesCount: 8,
          mimeType: "application/pdf",
          mineruExecutionProfile: { backendEffective: "pipeline" }
        }
      };
    }
    return {};
  };
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
      domain: { zh: '01_出版教材与成套课程', en: '01_Published_Course' },
      collection: { zh: 'Cambridge IGCSE', en: 'Cambridge IGCSE' },
      curriculum: { zh: 'CIE', en: 'CIE' },
      stage: { zh: '初中', en: 'Middle School' },
      level: { zh: 'IGCSE', en: 'IGCSE' },
      subject: { zh: '数学', en: 'Math' },
      resource_type: { zh: '教材', en: 'Textbook' },
      component_role: { zh: '主体资料', en: 'Main Content' }
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
  assert.equal(norm1.controlled_classification.domain.id, '01_出版教材与成套课程');
  assert.equal(norm1.controlled_classification.collection.id, 'Cambridge IGCSE');
  assert.equal(norm1.controlled_classification.subject.id, '数学');
  assert.equal(norm1.controlled_classification.resource_type.id, '教材');
  assert.equal(norm1.controlled_classification.component_role.id, '主体资料');
  
  // Test rawPreview stringification for object
  let rawString1 = '';
  if (typeof res1.result === 'object') {
    rawString1 = JSON.stringify(res1.result);
  } else {
    rawString1 = String(res1.result);
  }
  assert.notEqual(rawString1, '[object Object]');
  console.log('Case 1 Pass ✅');

  // Case 2: Reading Explorer 2 Student Book & Answer Key
  console.log('Case 2: Reading Explorer 2 Student Book & Answer Key');
  const mockMathResult = {
    primary_facets: {
      domain: { zh: '01_出版教材与成套课程' },
      collection: { zh: 'Reading Explorer' },
      subject: { zh: '英语' },
      resource_type: { zh: '答案解析' },
      component_role: { zh: '答案' }
    },
    governance: { confidence: 'high', human_review_required: false }
  };
  const provider2 = createMockProvider(mockMathResult, false, true);
  let res2 = await worker1.executeWithFallback(provider2, 'Sample', {});
  let v02_2 = worker1.extractJson(res2.result);
  const norm2 = validateAndNormalizeV02(v02_2, {});
  assert.equal(norm2.controlled_classification.collection.id, 'Reading Explorer');
  assert.equal(norm2.controlled_classification.subject.id, '英语');
  assert.equal(norm2.controlled_classification.resource_type.id, '答案解析');
  assert.equal(norm2.controlled_classification.component_role.id, '答案');
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
  assert.equal(norm3.controlled_classification.domain.id, '06_公司行政经营资料');
  assert.equal(norm3.classification_review.required, true);
  assert.ok(norm3.classification_review.reasons.includes('non_education_domain'), 'Should log non_education_domain reason');
  console.log('Case 3 Pass ✅');

  // Case 3a: Unknown Collection
  console.log('Case 3a: Unknown Collection');
  const mockUnknownResult = {
    primary_facets: {
      domain: { zh: '01_出版教材与成套课程' },
      collection: { zh: '未知奇奇怪怪' },
      subject: { zh: '数学' },
      resource_type: { zh: '讲义' },
      component_role: { zh: '主体资料' }
    },
    governance: { confidence: 'high', human_review_required: false }
  };
  const provider3a = createMockProvider(mockUnknownResult);
  let res3a = await worker1.executeWithFallback(provider3a, 'Sample', {});
  let v02_3a = worker1.extractJson(res3a.result);
  const norm3a = validateAndNormalizeV02(v02_3a, {});
  assert.equal(norm3a.classification_review.required, true);
  assert.equal(norm3a.classification_review.unmatched_facets.collection, '未知奇奇怪怪');
  console.log('Case 3a Pass ✅');

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
      return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"subject": {"zh": "数学"}}, "governance": {"confidence": "high"}, "evidence": []}', usage: {} };
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

  // Case 8: System tags, combined tags, and summary generation
  console.log('Case 8: System tags, combined tags, and summary');
  const mockSystemTagsResult = {
    primary_facets: {
      domain: { zh: '01_出版教材与成套课程' },
      subject: { zh: '物理' },
      level: { zh: '高一' },
      resource_type: { zh: '真题' }
    },
    search_tags: {
      topic_tags: ['力学'],
      skill_tags: ['分析能力', '未知乱七八糟技能']
    },
    evidence: [],
    governance: { confidence: 'high', human_review_required: false }
  };
  worker1.executeWithFallback = async () => ({ provider: 'mock', model: 'mock', result: JSON.stringify(mockSystemTagsResult), usage: {} });
  
  await worker1.processJob({ id: 'test-job-8', parseTaskId: 'test-task-8', materialId: 'm8', inputMarkdownObjectName: 'test.pdf' });
  
  assert.equal(finalResultObj.grade, '高一');
  assert.equal(finalResultObj.summary, '物理 · 高一 · 真题');
  
  const tagsStr = JSON.stringify(finalResultObj.tags);
  assert.ok(tagsStr.includes('力学'), 'tags should include search_tags.topic_tags');
  assert.ok(tagsStr.includes('分析能力'), 'tags should include search_tags.skill_tags');
  assert.ok(!tagsStr.includes('未知乱七八糟技能'), 'proposed_new_tags must not enter Material.tags');
  assert.ok(tagsStr.includes('ocr_enabled') || tagsStr.includes('OCR') || tagsStr.includes('pdf'), 'tags should include format_tags');
  assert.ok(tagsStr.includes('pipeline') || tagsStr.includes('Pipeline'), 'tags should include engine_tags');
  assert.ok(finalResultObj.aiClassificationV02.system_tags.format_tags.length > 0, 'system_tags.format_tags should be populated');
  
  console.log('Case 8 Pass ✅');

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
      return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"subject": {"zh": "数学"}}, "governance": {"confidence": "high"}, "evidence": []}', usage: {} };
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

  // Case 12: schema-invalid triggers repair
  console.log('Case 12: schema-invalid triggers repair');
  callCount = 0;
  let repairPromptReceived = '';
  worker1.executeWithFallback = async (provider, markdown, settings) => {
    callCount++;
    if (callCount === 1) {
      return { provider: 'ollama', model: 'qwen3.5', result: '{"title": "出国行李清单", "domain": "travel", "subject": "personal_items", "resource_type": "list", "evidence_snippets": ["出国", "护照签证"]}', usage: {} };
    } else {
      repairPromptReceived = settings.systemPrompt;
      return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"domain": {"zh": "travel"}, "subject": {"zh": "personal_items"}, "resource_type": {"zh": "list"}}, "evidence": ["出国", "护照签证"], "governance": {"confidence": "high"}}', usage: {} };
    }
  };
  await worker1.processJob({ id: 'test-job-12', parseTaskId: 'test-task-12', materialId: 'm12', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, true);
  assert.ok(repairPromptReceived.includes('出国行李清单'));
  assert.equal(finalResultObj.aiClassificationRepairSucceeded, true);
  assert.equal(finalResultObj.aiClassificationDegraded, undefined);
  assert.equal(finalResultObj.aiClassificationV02.primary_facets.domain.zh, 'travel');
  assert.equal(Array.isArray(finalResultObj.aiClassificationV02.evidence), true);
  console.log('Case 12 Pass ✅');

  // Case 13: schema-invalid repair fails -> skeleton explicitly
  console.log('Case 13: schema-invalid repair fails -> skeleton explicitly');
  callCount = 0;
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    callCount++;
    if (callCount === 1) {
      return { provider: 'ollama', model: 'qwen3.5', result: '{"title": "出国行李清单", "domain": "travel"}', usage: {} };
    } else {
      return { provider: 'ollama', model: 'qwen3.5', result: '{"still_flat": "yes"}', usage: {} };
    }
  };
  await worker1.processJob({ id: 'test-job-13', parseTaskId: 'test-task-13', materialId: 'm13', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationProvider, 'skeleton');
  assert.equal(finalResultObj.aiClassificationDegraded, true);
  assert.equal(finalResultObj.aiClassificationErrorSource, 'ai-metadata-schema-invalid-repair-failed');
  assert.ok(finalResultObj.aiClassificationDegradedReason);
  assert.ok(finalResultObj.aiClassificationV02.governance.risk_flags.includes('skeleton_fallback'));
  console.log('Case 13 Pass ✅');

  // Case 14: Valid v0.2 JSON -> no repair
  console.log('Case 14: Valid v0.2 JSON -> no repair');
  callCount = 0;
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    callCount++;
    return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"domain": {"zh": "travel"}}, "evidence": ["出国"], "governance": {"confidence": "high"}}', usage: {} };
  };
  await worker1.processJob({ id: 'test-job-14', parseTaskId: 'test-task-14', materialId: 'm14', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(callCount, 1);
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, undefined);
  assert.equal(finalResultObj.aiClassificationDegraded, undefined);
  console.log('Case 14 Pass ✅');

  // Case 15: fields_missing fallback gets degraded flags
  console.log('Case 15: fields_missing fallback gets degraded flags');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {}, "evidence": [], "governance": {"human_review_reason": "fields_missing", "risk_flags": ["skeleton_fallback"]}}', usage: {} };
  };
  await worker1.processJob({ id: 'test-job-15', parseTaskId: 'test-task-15', materialId: 'm15', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationDegraded, true);
  assert.equal(finalResultObj.aiClassificationErrorSource, 'ai-metadata-schema-invalid');
  console.log('Case 15 Pass ✅');

  // Case 16: LLM output source must not override system source
  console.log('Case 16: LLM output source must not override system source');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return { provider: 'ollama', model: 'qwen3.5', result: '{"source": {"raw_object_name": "模型乱写", "markdown_object_name": "模型乱写"}, "primary_facets": {"domain": {"zh": "travel"}, "subject": {"zh": "personal_items"}, "resource_type": {"zh": "list"}}, "evidence": ["出国", "护照签证"], "governance": {"confidence": "high"}}', usage: {} };
  };
  await worker1.processJob({ id: 'test-job-16', parseTaskId: 'test-task-16', materialId: 'mat-x', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationV02.source.raw_object_name, 'originals/mat-x/source.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.markdown_object_name, 'parsed/mat-x/full.md');
  assert.equal(finalResultObj.aiClassificationV02.source.file_name, '出国.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.llm_source_hint.raw_object_name, '模型乱写');
  console.log('Case 16 Pass ✅');

  // Case 17: sourceMeta 优先从 Material 补齐
  console.log('Case 17: sourceMeta 优先从 Material 补齐');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return { provider: 'ollama', model: 'qwen3.5', result: '{"primary_facets": {"domain": {"zh": "travel"}}, "evidence": ["出国"], "governance": {"confidence": "high"}}', usage: {} };
  };
  await worker1.processJob({ id: 'test-job-17', parseTaskId: 'non-existent-task', materialId: 'mat-test1', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationV02.source.file_name, '出国.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.file_size, 33814);
  assert.equal(finalResultObj.aiClassificationV02.source.mime_type, 'application/pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.raw_object_name, 'originals/mat-test1/source.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.parsed_prefix, 'parsed/mat-test1/');
  assert.equal(finalResultObj.aiClassificationV02.source.markdown_object_name, 'parsed/mat-test1/full.md');
  assert.equal(finalResultObj.aiClassificationV02.source.parsed_files_count, 8);
  console.log('Case 17 Pass ✅');

  // Case 18: ParseTask 补齐 fallback 仍有效
  console.log('Case 18: ParseTask 补齐 fallback 仍有效');
  await worker1.processJob({ id: 'test-job-18', parseTaskId: 'test-task-2', materialId: 'mat-test2', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationV02.source.file_name, 'fallback.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.file_size, 12345);
  assert.equal(finalResultObj.aiClassificationV02.source.mime_type, 'application/pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.raw_object_name, 'originals/mat-test2/source.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.parsed_prefix, 'parsed/mat-test2/');
  assert.equal(finalResultObj.aiClassificationV02.source.markdown_object_name, 'parsed/mat-test2/full.md');
  assert.equal(finalResultObj.aiClassificationV02.source.parsed_files_count, 5);
  console.log('Case 18 Pass ✅');

  // Case 19: LLM source 污染仍被隔离
  console.log('Case 19: LLM source 污染仍被隔离');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return { provider: 'ollama', model: 'qwen3.5', result: '{"source": {"raw_object_name": "模型乱写", "markdown_object_name": "模型乱写"}, "primary_facets": {"domain": {"zh": "travel"}}, "evidence": ["出国"], "governance": {"confidence": "high"}}', usage: {} };
  };
  await worker1.processJob({ id: 'test-job-19', parseTaskId: 'test-task-3', materialId: 'mat-test', inputMarkdownObjectName: 'test.md' });
  
  assert.notEqual(finalResultObj.aiClassificationV02.source.raw_object_name, '模型乱写');
  assert.notEqual(finalResultObj.aiClassificationV02.source.markdown_object_name, '模型乱写');
  assert.equal(finalResultObj.aiClassificationV02.source.llm_source_hint.raw_object_name, '模型乱写');
  console.log('Case 19 Pass ✅');

  // Case 20: provider failed skeleton 路径也有完整 source
  console.log('Case 20: provider failed skeleton 路径也有完整 source');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    throw new Error("Simulated Provider Failure");
  };
  await worker1.processJob({ id: 'test-job-20', parseTaskId: 'test-task-4', materialId: 'mat-test4', inputMarkdownObjectName: 'test.md' });
  
  assert.equal(finalResultObj.aiClassificationProvider, 'skeleton');
  assert.equal(finalResultObj.aiClassificationV02.source.file_name, '出国.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.raw_object_name, 'originals/mat-test4/source.pdf');
  assert.equal(finalResultObj.aiClassificationV02.source.markdown_object_name, 'parsed/mat-test4/full.md');
  assert.equal(finalResultObj.aiClassificationV02.source.parsed_files_count, 8);
  console.log('Case 20 Pass ✅');

  // Case 21: fenced valid flat JSON -> failureKind = schema_invalid, rawLooksTruncated=false
  console.log('Case 21: fenced valid flat JSON -> failureKind = schema_invalid, rawLooksTruncated=false');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'qwen3.5',
      result: '```json\n{"domain": "01_出版教材与成套课程"}\n```',
      rawResponse: '```json\n{"domain": "01_出版教材与成套课程"}\n```',
      traceDetails: {
        rawLooksTruncated: false
      },
      usage: {}
    };
  };
  await worker1.processJob({ id: 'test-job-21', parseTaskId: 'test-task-21', materialId: 'm21', inputMarkdownObjectName: 'test.md' });
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.failureKind, 'schema_invalid');
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.schemaInvalid, true);
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.jsonParseFailed, false);
  console.log('Case 21 Pass ✅');

  // Case 22: malformed JSON -> failureKind = json_parse_failed
  console.log('Case 22: malformed JSON -> failureKind = json_parse_failed');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'qwen3.5',
      result: '{"domain": "01_',
      rawResponse: '{"domain": "01_',
      traceDetails: {
        rawLooksTruncated: true
      },
      usage: {}
    };
  };
  await worker1.processJob({ id: 'test-job-22', parseTaskId: 'test-task-22', materialId: 'm22', inputMarkdownObjectName: 'test.md' });
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.failureKind, 'json_parse_failed');
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.jsonParseFailed, true);
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.schemaInvalid, false);
  console.log('Case 22 Pass ✅');

  // Case 23: JSON array -> failureKind = non_object_json
  console.log('Case 23: JSON array -> failureKind = non_object_json');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'qwen3.5',
      result: '[]',
      rawResponse: '[]',
      traceDetails: {
        rawLooksTruncated: false
      },
      usage: {}
    };
  };
  await worker1.processJob({ id: 'test-job-23', parseTaskId: 'test-task-23', materialId: 'm23', inputMarkdownObjectName: 'test.md' });
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.failureKind, 'non_object_json');
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.jsonParseFailed, true);
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.schemaInvalid, false);
  console.log('Case 23 Pass ✅');

  // Case 24: valid v0.2 JSON -> failureKind=null, no repair
  console.log('Case 24: valid v0.2 JSON -> failureKind=null, no repair');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'qwen3.5',
      result: '{"primary_facets": {"domain": {"zh": "travel"}}, "evidence": ["出国"], "governance": {"confidence": "high"}}',
      rawResponse: '{"primary_facets": {"domain": {"zh": "travel"}}, "evidence": ["出国"], "governance": {"confidence": "high"}}',
      traceDetails: {
        rawLooksTruncated: false
      },
      usage: {}
    };
  };
  await worker1.processJob({ id: 'test-job-24', parseTaskId: 'test-task-24', materialId: 'm24', inputMarkdownObjectName: 'test.md' });
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.failureKind, null);
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.jsonParseFailed, false);
  assert.equal(finalResultObj.aiClassificationRawTrace.firstPass.schemaInvalid, false);
  assert.equal(finalResultObj.aiClassificationTwoPassAttempted, undefined);
  console.log('Case 24 Pass ✅');

  worker1.executeWithFallback = originalExecute;
  worker1.transition = originalTransition;

  // Test 1: Ollama request always sends think:false
  console.log('Test 1: Ollama request always sends think:false');
  const { OllamaProvider } = await import('../services/ai/providers/ollama.mjs');
  let requestedThink = null;
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

  // Test 5: Ollama fetch dispatcher timeout 对齐 & headers timeout 分类
  console.log('Test 5: Ollama fetch dispatcher timeout 对齐 & headers timeout 分类');
  let fetchOptions = null;
  globalThis.fetch = async (url, options) => {
    if (typeof url === 'string' && !url.includes('/api/chat')) {
      return originalFetch(url, options);
    }
    fetchOptions = options;
    const err = new TypeError('fetch failed');
    err.cause = { code: 'UND_ERR_HEADERS_TIMEOUT', message: 'Headers Timeout Error' };
    throw err;
  };
  const ollama5 = new OllamaProvider({ timeoutMs: 12345 });
  try {
    await ollama5.extractMetadata('test', { expectJson: true });
    assert.fail('Should have thrown timeout error');
  } catch (err) {
    assert.ok(fetchOptions.dispatcher);
    assert.equal(err.timeoutKind, 'headers-timeout');
    assert.equal(err.headersTimeoutMs, 12345);
    assert.equal(err.bodyTimeoutMs, 12345);
    assert.equal(err.cause.code, 'UND_ERR_HEADERS_TIMEOUT');
  }
  console.log('Test 5 Pass ✅');

  // Test 6: repairProviderDetails 透传 timeoutKind & skeleton 语义不变
  console.log('Test 6: repairProviderDetails 透传 timeoutKind & skeleton 语义不变');
  let callIndex6 = 0;
  globalThis.fetch = async (url, options) => {
    if (typeof url === 'string' && !url.includes('/api/chat')) {
      return originalFetch(url, options);
    }
    callIndex6++;
    if (callIndex6 === 1) return { ok: true, json: async () => ({ message: { content: 'invalid json' } }) };
    const err = new TypeError('fetch failed');
    err.cause = { code: 'UND_ERR_HEADERS_TIMEOUT', message: 'Headers Timeout Error' };
    throw err;
  };
  const worker6 = new AiMetadataWorker({
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('mock'); } }),
    saveObject: async () => {}
  });
  worker6.createProvider = () => new OllamaProvider({ timeoutMs: 54321 });
  let finalResult6 = {};
  worker6.transition = async (job, update) => { finalResult6 = update.result; };
  await worker6.processJob({ id: 'test-job-t6', parseTaskId: 't6', inputMarkdownObjectName: 'x.md' });

  assert.equal(finalResult6.aiClassificationRepairProviderDetails.timeoutKind, 'headers-timeout');
  assert.equal(finalResult6.aiClassificationRepairProviderDetails.headersTimeoutMs, 54321);
  assert.equal(finalResult6.aiClassificationRepairProviderDetails.bodyTimeoutMs, 54321);
  assert.equal(finalResult6.aiClassificationProvider, 'skeleton');
  assert.equal(finalResult6.aiClassificationModel, 'skeleton');
  assert.equal(finalResult6.aiClassificationDegraded, true);
  assert.equal(finalResult6.aiClassificationErrorSource, 'ollama-json-repair-timeout');
  assert.ok(finalResult6.aiClassificationDegradedReason.includes('repair 阶段超时'));
  console.log('Test 6 Pass ✅');

  // Case 25: Non-education domain isolates education fields
  console.log('Case 25: Non-education domain isolates education fields');
  worker1.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'qwen3.5',
      result: '{"primary_facets": {"domain": {"zh": "06_公司行政经营资料"}, "curriculum": {"zh": "中国课标"}, "collection": {"zh": "同步教辅"}, "level": {"zh": "初三"}}, "evidence": ["公司行政"], "governance": {"confidence": "high"}}',
      rawResponse: '...',
      traceDetails: { rawLooksTruncated: false },
      usage: {}
    };
  };
  await worker1.processJob({ id: 'test-job-25', parseTaskId: 'test-task-25', materialId: 'm25', inputMarkdownObjectName: 'test.md' });
  assert.equal(finalResultObj.aiClassificationV02.controlled_classification.domain.id, '06_公司行政经营资料');
  assert.equal(finalResultObj.aiClassificationV02.controlled_classification.curriculum, undefined);
  assert.equal(finalResultObj.aiClassificationV02.controlled_classification.collection, undefined);
  assert.equal(finalResultObj.aiClassificationV02.controlled_classification.level, undefined);
  assert.ok(finalResultObj.aiClassificationV02.classification_review.reasons.includes('non_education_domain'));
  assert.ok(finalResultObj.aiClassificationV02.classification_review.reasons.includes('unmatched_curriculum'));
  assert.equal(finalResultObj.aiClassificationV02.classification_review.unmatched_facets.curriculum, '01_中国课标');
  console.log('Case 25 Pass ✅');

  globalThis.fetch = originalFetch;

  console.log('--- AI Metadata Real Sample Smoke Test Success ---');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
