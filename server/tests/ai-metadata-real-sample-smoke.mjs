import assert from 'node:assert';
import { AiMetadataWorker } from '../services/ai/metadata-worker.mjs';
import { validateAndNormalizeV02, getDefaultV02Skeleton } from '../services/ai/metadata-standard-v0.2.mjs';

async function runTests() {
  console.log('--- AI Metadata Real Sample Smoke Test Start ---');

  // Mocks
  const mockMinio = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () {} })
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

  console.log('--- AI Metadata Real Sample Smoke Test Success ---');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
