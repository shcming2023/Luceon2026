import { validateAndNormalizeV02 } from '../services/ai/metadata-standard-v0.2.mjs';
import { sampleMarkdown } from '../services/ai/metadata-sampler.mjs';

async function run() {
  console.log('[Test] Running ai-metadata-v02-smoke tests...');

  let passed = 0;
  let failed = 0;

  function assertEq(name, actual, expected) {
    if (actual === expected) {
      passed++;
      console.log(`[PASS] ${name}`);
    } else {
      failed++;
      console.error(`[FAIL] ${name}: Expected ${expected}, got ${actual}`);
    }
  }

  function assertTrue(name, condition) {
    if (condition) {
      passed++;
      console.log(`[PASS] ${name}`);
    } else {
      failed++;
      console.error(`[FAIL] ${name}: Condition was false`);
    }
  }

  // 1. Test validateAndNormalizeV02
  const source = { materialId: 'm-123', filename: 'test.pdf' };
  
  // 1.1 Invalid JSON
  const invalidJson = validateAndNormalizeV02(null, source);
  assertEq('Invalid JSON sets low confidence', invalidJson.governance.confidence, 'low');
  assertTrue('Invalid JSON sets review required', invalidJson.governance.human_review_required === true);

  // 1.2 Missing primary_facets
  const missingFacets = validateAndNormalizeV02({ descriptive_metadata: {} }, source);
  assertEq('Missing facets sets low confidence', missingFacets.governance.confidence, 'low');

  // 1.3 Valid high confidence
  const validHigh = validateAndNormalizeV02({
    primary_facets: { subject: { zh: '数学' } },
    governance: { confidence: 'high', human_review_required: false }
  }, source);
  assertEq('Valid high keeps high confidence', validHigh.governance.confidence, 'high');
  assertTrue('Valid high does not require review', validHigh.governance.human_review_required === false);

  // 1.4 Valid low confidence forces review
  const validLow = validateAndNormalizeV02({
    primary_facets: { subject: { zh: '数学' } },
    governance: { confidence: 'low', human_review_required: false, human_review_reason: '' }
  }, source);
  assertTrue('Low confidence forces review', validLow.governance.human_review_required === true);
  assertTrue('Low confidence adds reason', validLow.governance.human_review_reason.length > 0);

  // 1.5 Proposed tags forces review
  const newTags = validateAndNormalizeV02({
    primary_facets: { subject: { zh: '数学' } },
    search_tags: { proposed_new_tags: ['new_tag'] },
    governance: { confidence: 'high', human_review_required: false }
  }, source);
  assertTrue('Proposed tags force review', newTags.governance.human_review_required === true);
  assertTrue('Proposed tags add reason', newTags.governance.human_review_reason.includes('tags'));

  // 2. Test sampleMarkdown
  const longMarkdown = 'A'.repeat(100000);
  const sampleSource = { filename: 'long.md' };
  const { sampledContent, inputHash } = sampleMarkdown(longMarkdown, sampleSource, 50000);
  
  assertTrue('Sampled content is truncated', sampledContent.length <= 50000);
  assertTrue('Sampled content contains head marker', sampledContent.includes('=== HEAD ==='));
  assertTrue('Sampled content contains metadata marker', sampledContent.includes('=== FILE METADATA ==='));
  assertTrue('Sampled content generates input hash', inputHash.startsWith('sha256:'));

  console.log(`\nResults: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
