import { validateAndNormalizeV02, getDefaultV02Skeleton } from '../services/ai/metadata-standard-v0.2.mjs';
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
    primary_facets: { domain: { zh: 'general' }, subject: { zh: 'math' }, resource_type: { zh: 'coursebook' } },
    governance: { confidence: 'high', human_review_required: false },
    evidence: ['test']
  }, source);
  assertEq('Valid high keeps high confidence', validHigh.governance.confidence, 'high');
  assertTrue('Valid high does not require review', validHigh.governance.human_review_required === false);

  // 1.4 Valid low confidence forces review
  const validLow = validateAndNormalizeV02({
    primary_facets: { domain: { zh: 'general' }, subject: { zh: 'math' }, resource_type: { zh: 'coursebook' } },
    governance: { confidence: 'low', human_review_required: false, human_review_reason: '' },
    evidence: ['test']
  }, source);
  assertTrue('Low confidence forces review', validLow.governance.human_review_required === true);
  assertTrue('Low confidence adds reason', validLow.governance.human_review_reason.length > 0);

  // 1.5 Proposed tags forces review
  const newTags = validateAndNormalizeV02({
    primary_facets: { domain: { zh: 'general' }, subject: { zh: 'math' }, resource_type: { zh: 'coursebook' } },
    search_tags: { proposed_new_tags: ['new_tag'] },
    governance: { confidence: 'high', human_review_required: false },
    evidence: ['test']
  }, source);
  assertTrue('Proposed tags force review', newTags.governance.human_review_required === true);
  assertTrue('Proposed tags add reason', newTags.governance.human_review_reason.includes('tags'));

  // 1.6 Test getDefaultV02Skeleton risk flags
  const chineseReasonSkeleton = getDefaultV02Skeleton(source, 'low', 'AI Provider JSON 解析失败，已降级为 skeleton 结果');
  assertTrue('Chinese parsing failure adds skeleton_fallback flag', chineseReasonSkeleton.governance.risk_flags.includes('skeleton_fallback'));
  assertTrue('Chinese parsing failure adds ai_provider_json_parse_failed flag', chineseReasonSkeleton.governance.risk_flags.includes('ai_provider_json_parse_failed'));
  
  const englishReasonSkeleton = getDefaultV02Skeleton(source, 'low', 'json_parse_failed');
  assertTrue('English JSON reason adds skeleton_fallback flag', englishReasonSkeleton.governance.risk_flags.includes('skeleton_fallback'));
  assertTrue('English JSON reason adds ai_provider_json_parse_failed flag', englishReasonSkeleton.governance.risk_flags.includes('ai_provider_json_parse_failed'));

  // 1.7 Evidence normalization
  const normalizedEv = validateAndNormalizeV02({
    primary_facets: { domain: { zh: '1' }, subject: { zh: '1' }, resource_type: { zh: '1' } },
    evidence: ['just a string', { type: 'body', quote_or_summary: 'quote', supports: ['x'] }, { invalid: 'object' }]
  }, source);
  assertTrue('Evidence normalized', Array.isArray(normalizedEv.evidence) && normalizedEv.evidence.length === 2);
  assertEq('String evidence type is unknown', normalizedEv.evidence[0].type, 'unknown');
  assertEq('Object evidence type preserved', normalizedEv.evidence[1].type, 'body');

  // 1.8 Missing facets
  const missingDomain = validateAndNormalizeV02({
    primary_facets: { subject: { zh: '数学' }, resource_type: { zh: '试卷' } },
    evidence: ['x']
  }, source);
  assertEq('Missing domain sets low confidence', missingDomain.governance.confidence, 'low');
  assertTrue('Missing domain forces review', missingDomain.governance.human_review_required === true);
  assertTrue('Missing domain adds risk flag', missingDomain.governance.risk_flags.includes('domain_missing'));

  // 1.9 System tags & governance signals
  const resultSysTags = validateAndNormalizeV02({
    primary_facets: { domain: { zh: '1' }, subject: { zh: '1' }, resource_type: { zh: '1' } },
    evidence: ['a'],
    governance: { confidence: 'high' }
  }, { ...source, parsedFilesCount: 0, mineruExecutionProfile: { enableOcr: true, backendEffective: 'pipeline' } });
  
  assertTrue('System tags format generated', resultSysTags.system_tags.format_tags.some(t => t.en === 'ocr_enabled'));
  assertTrue('System tags engine generated', resultSysTags.system_tags.engine_tags.some(t => t.en === 'pipeline'));
  assertTrue('Governance signals quality generated', resultSysTags.governance_signals.quality.includes('no_parsed_artifacts'));

  // 2. Test sampleMarkdown
  const longMarkdown = 'A'.repeat(100000);
  const sampleSource = { filename: 'long.md' };
  const { sampledContent, inputHash } = sampleMarkdown(longMarkdown, sampleSource, 50000);
  
  assertTrue('Sampled content is truncated', sampledContent.length <= 50000);
  assertTrue('Sampled content contains head marker', sampledContent.includes('=== HEAD ==='));
  assertTrue('Sampled content contains metadata marker', sampledContent.includes('=== FILE METADATA ==='));
  assertTrue('Sampled content generates input hash', inputHash.startsWith('sha256:'));

  // 3. Test Taxonomy Control
  const taxonomyResult1 = validateAndNormalizeV02({
    primary_facets: { domain: 'English', subject: 'esl', resource_type: '课本' },
    search_tags: { topic_tags: ['reading', '北极岛屿旅行知识'], skill_tags: ['分析能力'] },
    governance: { confidence: 'high' },
    evidence: ['x']
  }, source);
  
  // 'English' domain should fail. 'academic', 'exam', 'travel', 'general' are valid domains.
  assertTrue('Domain unmapped goes to review', taxonomyResult1.classification_review.required === true);
  assertEq('Domain unmapped value captured', taxonomyResult1.classification_review.unmatched_facets.domain, 'English');
  assertEq('Subject alias normalized', taxonomyResult1.controlled_classification.subject?.id, 'english');
  assertEq('Resource type alias normalized', taxonomyResult1.controlled_classification.resource_type?.id, 'coursebook');
  
  assertTrue('Topic tag reading normalized', taxonomyResult1.normalized_tags.topic_tags.some(t => t.id === 'reading'));
  assertTrue('Topic tag unmapped proposed', taxonomyResult1.proposed_new_tags.some(t => t.value === '北极岛屿旅行知识'));
  assertTrue('Skill tag normalized', taxonomyResult1.normalized_tags.skill_tags.some(t => t.id === 'analytical'));
  
  // 3.1 Skeleton Taxonomy
  const skeletonResult = getDefaultV02Skeleton(source, 'low', 'test');
  assertTrue('Skeleton classification review required', skeletonResult.classification_review.required === true);
  assertEq('Skeleton taxonomy fields empty', skeletonResult.controlled_classification.domain, null);
  assertEq('Skeleton normalized tags empty', skeletonResult.normalized_tags.topic_tags.length, 0);

  console.log(`\nResults: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
