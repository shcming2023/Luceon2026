import assert from 'node:assert';
import { buildEvidencePack } from '../services/ai/metadata-evidence-pack.mjs';
import { AiMetadataWorker } from '../services/ai/metadata-worker.mjs';

async function runTests() {
  console.log('--- AI Metadata Evidence Pack Smoke Test ---');

  // Test 1: Heading Outline Extraction
  console.log('Test 1: Heading Outline Extraction');
  const sampleMd = `
# Chapter 1
some text
## Exercise
1. Algebra
some text
### Final
  `;
  const pack1 = buildEvidencePack(sampleMd, { filename: 'test.md' });
  const outline = pack1.content.split('=== HEADING OUTLINE ===')[1].split('=== REPRESENTATIVE BODY SNIPPETS ===')[0];
  assert.ok(outline.includes('# Chapter 1'));
  assert.ok(outline.includes('## Exercise'));
  assert.ok(outline.includes('1. Algebra'));
  assert.ok(outline.includes('### Final'));
  console.log('Test 1 Pass ✅');

  // Test 2: Filename Signals
  console.log('Test 2: Filename Signals');
  const filename2 = 'Cambridge IGCSE(0580) Core and Extended Mathematics_2018(Cambridge University Press).pdf';
  const pack2 = buildEvidencePack('mock content', { filename: filename2 });
  const filenameSignals = pack2.content.split('=== FILENAME SIGNALS ===')[1].split('=== DOCUMENT SHAPE ===')[0];
  assert.ok(filenameSignals.includes('Cambridge'));
  assert.ok(filenameSignals.includes('IGCSE'));
  assert.ok(filenameSignals.includes('0580'));
  assert.ok(filenameSignals.includes('Core'));
  assert.ok(filenameSignals.includes('Extended'));
  assert.ok(filenameSignals.includes('Mathematics'));
  assert.ok(filenameSignals.includes('2018'));
  console.log('Test 2 Pass ✅');

  // Test 3: Worker selects evidence pack for large document
  console.log('Test 3: Worker selects evidence pack for large document');
  let workerResult = null;
  const mockMinio3 = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('a'.repeat(200000)); } }),
    saveObject: async () => {}
  };
  const worker3 = new AiMetadataWorker(mockMinio3);
  worker3.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'test',
      result: '{"classification_draft": {"domain": {"zh": "test"}}, "evidence": []}',
      rawResponse: '{}',
      traceDetails: { rawLooksTruncated: false },
      usage: {}
    };
  };
  worker3.transition = async (job, update) => { workerResult = update.result; };
  await worker3.processJob({ id: 'test-job-3', parseTaskId: 't3', inputMarkdownObjectName: 'x.md' });
  assert.equal(workerResult.aiClassificationSamplingMode, 'evidence-pack-v0.3');
  assert.equal(workerResult.aiClassificationInputOriginalLength, 200000);
  assert.ok(workerResult.aiClassificationInputSampledLength < 30000);
  assert.ok(workerResult.aiClassificationRawTrace.input.sections.evidenceCandidates);
  console.log('Test 3 Pass ✅');

  // Test 4: Worker uses legacy sampler for small document
  console.log('Test 4: Worker uses legacy sampler for small document');
  const mockMinio4 = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('small doc'); } }),
    saveObject: async () => {}
  };
  const worker4 = new AiMetadataWorker(mockMinio4);
  worker4.executeWithFallback = async (provider, markdown, settings, prompt) => {
    return {
      provider: 'ollama', model: 'test',
      result: '{"primary_facets": {"domain": {"zh": "test"}}, "evidence": [], "governance": {"confidence": "high"}}',
      rawResponse: '{}',
      traceDetails: { rawLooksTruncated: false },
      usage: {}
    };
  };
  worker4.transition = async (job, update) => { workerResult = update.result; };
  await worker4.processJob({ id: 'test-job-4', parseTaskId: 't4', inputMarkdownObjectName: 'x.md' });
  assert.equal(workerResult.aiClassificationSamplingMode, 'legacy-sampler-v0.2');
  assert.equal(workerResult.aiClassificationInputOriginalLength, 9);
  assert.equal(workerResult.aiClassificationInputSampledLength, 9);
  console.log('Test 4 Pass ✅');

  // Test 5: Draft Repair
  console.log('Test 5: Draft Repair mapping');
  const mockMinio5 = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('a'.repeat(200000)); } }),
    saveObject: async () => {}
  };
  const worker5 = new AiMetadataWorker(mockMinio5);
  let callCount5 = 0;
  worker5._runProviderPass = async (provider, job, settings, prompt, options) => {
    callCount5++;
    if (callCount5 === 1) {
      assert.ok(prompt.includes('classification_draft'), 'First pass should use draft prompt');
      return {
        provider: 'ollama', model: 'test',
        result: '{"classification_draft": {"domain": "学科教育", "subject": "数学"}, "evidence": [{"type": "content", "quote_or_summary": "Test evidence", "supports": []}]}',
        rawResponse: '{"classification_draft": {"domain": "学科教育", "subject": "数学"}, "evidence": [{"type": "content", "quote_or_summary": "Test evidence", "supports": []}]}',
        traceDetails: { rawLooksTruncated: false },
        usage: {}
      };
    } else {
      assert.ok(prompt.includes('**草稿内容（可能是旧式 JSON、扁平 JSON、或自然语言草稿，或包含 classification_draft 的草稿 JSON）：**'), 'Repair prompt should be updated');
      return {
        provider: 'ollama', model: 'test',
        result: '{"primary_facets": {"domain": {"zh": "01_学科教育"}, "subject": {"zh": "03_数学"}}, "governance": {"confidence": "high"}, "evidence": [{"type": "content", "quote_or_summary": "Test evidence", "supports": []}]}',
        rawResponse: '{"primary_facets": {"domain": {"zh": "01_学科教育"}, "subject": {"zh": "03_数学"}}, "governance": {"confidence": "high"}, "evidence": [{"type": "content", "quote_or_summary": "Test evidence", "supports": []}]}',
        traceDetails: { rawLooksTruncated: false },
        usage: {}
      };
    }
  };
  worker5.transition = async (job, update) => { workerResult = update.result; };
  await worker5.processJob({ id: 'test-job-5', parseTaskId: 't5', inputMarkdownObjectName: 'x.md' });
  assert.equal(workerResult.aiClassificationV02.controlled_classification.domain.zh, '学科教育');
  assert.equal(workerResult.aiClassificationV02.controlled_classification.subject.zh, '数学');
  assert.equal(workerResult.aiClassificationDegraded, undefined);
  console.log('Test 5 Pass ✅');

  console.log('--- AI Metadata Evidence Pack Smoke Test Success ---');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
