/**
 * metadata-taxonomy-v0.2.mjs - 受控分类与规范标签库
 *
 * 通过加载 metadata-taxonomy-v0.1.json 提供校验、归一化和 Prompt 生成。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const taxonomyPath = path.join(__dirname, 'metadata-taxonomy-v0.1.json');

let taxonomyFact = null;
let TAXONOMY = {};
let TAGS_TAXONOMY = {};

function loadTaxonomy() {
  if (taxonomyFact) return taxonomyFact;
  try {
    const content = fs.readFileSync(taxonomyPath, 'utf8');
    taxonomyFact = JSON.parse(content);
    TAXONOMY = taxonomyFact.controlled_facets || {};
    TAGS_TAXONOMY = taxonomyFact.tag_groups || {};
  } catch (err) {
    console.error('Failed to load metadata-taxonomy-v0.1.json', err);
    TAXONOMY = {};
    TAGS_TAXONOMY = {};
    taxonomyFact = { taxonomy_version: 'unknown', rules_version: 'unknown' };
  }
  return taxonomyFact;
}

// Ensure loaded
loadTaxonomy();

export function getTaxonomyVersion() {
  return {
    taxonomy_version: taxonomyFact.taxonomy_version || 'v0.1',
    rules_version: taxonomyFact.rules_version || 'v0.1'
  };
}

/**
 * Build dynamic prompt context based on taxonomy JSON
 */
export function buildTaxonomyPromptContext(options = {}) {
  let ctx = `Taxonomy Version: ${taxonomyFact.taxonomy_version}\nRules Version: ${taxonomyFact.rules_version}\n\n`;
  
  if (taxonomyFact.facet_rules) {
    ctx += "=== FACET RULES ===\n";
    for (const [facet, rules] of Object.entries(taxonomyFact.facet_rules)) {
      if (Array.isArray(rules) && rules.length > 0) {
        ctx += `- ${facet}:\n  * ${rules.join('\n  * ')}\n`;
      }
    }
    ctx += "\n";
  }

  ctx += "=== CONTROLLED FACETS ===\n";
  const facetsToInclude = options.facets || ['domain', 'collection', 'resource_type', 'component_role'];
  for (const facet of facetsToInclude) {
    if (TAXONOMY[facet]) {
      ctx += `[${facet.toUpperCase()}]\n`;
      TAXONOMY[facet].forEach(item => {
        let desc = `- ${item.zh} (${item.en})`;
        if (item.decision_rules && item.decision_rules.length > 0) {
          desc += `\n    Rule: ${item.decision_rules.join(' ')}`;
        }
        if (item.aliases && item.aliases.length > 0) {
          desc += `\n    Aliases: ${item.aliases.slice(0, 5).join(', ')}`;
        }
        ctx += desc + "\n";
      });
      ctx += "\n";
    }
  }

  ctx += "=== TAGS RULES ===\n";
  if (taxonomyFact.tag_rules) {
    for (const [tagGroup, rules] of Object.entries(taxonomyFact.tag_rules)) {
      if (Array.isArray(rules) && rules.length > 0) {
        ctx += `- ${tagGroup}:\n  * ${rules.join('\n  * ')}\n`;
      }
    }
    ctx += "\n";
  }
  if (TAGS_TAXONOMY.topic_tags) {
    ctx += "[TOPIC TAGS]\n";
    const topics = TAGS_TAXONOMY.topic_tags.map(t => t.zh).slice(0, 15).join(', ');
    ctx += `Examples: ${topics}...\n\n`;
  }
  if (TAGS_TAXONOMY.skill_tags) {
    ctx += "[SKILL TAGS]\n";
    const skills = TAGS_TAXONOMY.skill_tags.map(t => t.zh).slice(0, 10).join(', ');
    ctx += `Examples: ${skills}...\n\n`;
  }

  return ctx;
}

/**
 * 将原始字符串标准化为受控分类对象
 */
function normalizeTaxonomyValue(facetName, rawValue) {
  if (!rawValue) return null;
  const val = String(rawValue).toLowerCase().trim();
  const dict = TAXONOMY[facetName];
  if (!dict) return null;

  for (const item of dict) {
    if (item.id === val || item.zh.toLowerCase() === val || item.en.toLowerCase() === val) {
      return { id: item.id, zh: item.zh, en: item.en };
    }
    if (item.aliases && item.aliases.some(a => a.toLowerCase() === val)) {
      return { id: item.id, zh: item.zh, en: item.en };
    }
  }
  return null;
}

/**
 * 将标签数组标准化，分离命中标签和提议标签
 */
function normalizeTags(groupName, rawTagsArray) {
  const normalized = [];
  const proposed = [];
  
  if (!Array.isArray(rawTagsArray)) return { normalized, proposed };
  
  const dict = TAGS_TAXONOMY[groupName];
  if (!dict) return { normalized, proposed: rawTagsArray.map(t => String(t).trim()).filter(Boolean) };

  for (const rawTag of rawTagsArray) {
    if (!rawTag) continue;
    let val = '';
    if (typeof rawTag === 'string') {
      val = rawTag.toLowerCase().trim();
    } else if (typeof rawTag === 'object') {
      val = (rawTag.zh || rawTag.en || '').toLowerCase().trim();
    }
    
    if (!val) continue;

    let matched = false;
    for (const item of dict) {
      if (item.id === val || item.zh.toLowerCase() === val || item.en.toLowerCase() === val || (item.aliases && item.aliases.some(a => a.toLowerCase() === val))) {
        // 防止重复
        if (!normalized.some(n => n.id === item.id)) {
          normalized.push({ id: item.id, zh: item.zh, en: item.en });
        }
        matched = true;
        break;
      }
    }

    if (!matched) {
      // 过滤太长或太短的垃圾标签
      const originalVal = typeof rawTag === 'string' ? rawTag.trim() : (rawTag.zh || rawTag.en || '').trim();
      if (originalVal.length > 1 && originalVal.length < 30) {
        if (!proposed.includes(originalVal)) {
          proposed.push(originalVal);
        }
      }
    }
  }
  
  return { normalized, proposed };
}

/**
 * 执行受控分类与规范标签标准化
 */
export function applyTaxonomyControl(v02Data) {
  const controlled = {};
  const unmatched = {};
  let reviewReasons = [];

  const facetsToCheck = ['domain', 'collection', 'curriculum', 'stage', 'level', 'subject', 'resource_type', 'component_role'];
  
  for (const facet of facetsToCheck) {
    let rawVal = v02Data.primary_facets?.[facet];
    if (rawVal && typeof rawVal === 'object') {
      rawVal = rawVal.zh || rawVal.en;
    }
    if (rawVal && typeof rawVal === 'string' && rawVal.trim().length > 0) {
      const normVal = normalizeTaxonomyValue(facet, rawVal);
      if (normVal) {
        controlled[facet] = normVal;
      } else {
        unmatched[facet] = String(rawVal);
        if (!reviewReasons.includes(`unmatched_${facet}`)) {
          reviewReasons.push(`unmatched_${facet}`);
        }
      }
    }
  }

  // 非教育资料检测：如果 domain 最终映射为 06（行政）或 99（待识别），需人工复核
  if (controlled.domain && (controlled.domain.id === '06_公司行政经营资料' || controlled.domain.id === '99_待识别与低置信度')) {
    if (!reviewReasons.includes('non_education_domain')) {
      reviewReasons.push('non_education_domain');
    }
  }

  // 核心字段如果无法归一，必须review
  if (unmatched.subject || unmatched.domain) {
     if (!reviewReasons.includes('unmatched_core_facets')) {
        reviewReasons.push('unmatched_core_facets');
     }
  }

  const normalized_tags = { topic_tags: [], skill_tags: [] };
  const proposed_new_tags = [];

  // Tags 归一
  if (v02Data.search_tags) {
    if (v02Data.search_tags.topic_tags) {
      const result = normalizeTags('topic_tags', v02Data.search_tags.topic_tags);
      normalized_tags.topic_tags = result.normalized;
      result.proposed.forEach(p => {
        proposed_new_tags.push({ group: 'topic_tags', value: p, reason: 'not_in_taxonomy' });
      });
    }
    if (v02Data.search_tags.skill_tags) {
      const result = normalizeTags('skill_tags', v02Data.search_tags.skill_tags);
      normalized_tags.skill_tags = result.normalized;
      result.proposed.forEach(p => {
        proposed_new_tags.push({ group: 'skill_tags', value: p, reason: 'not_in_taxonomy' });
      });
    }
  }

  if (proposed_new_tags.length > 0 && !reviewReasons.includes('proposed_new_tags')) {
    reviewReasons.push('proposed_new_tags');
  }

  const requiresReview = reviewReasons.length > 0;

  return {
    controlled_classification: controlled,
    normalized_tags: normalized_tags,
    proposed_new_tags: proposed_new_tags,
    classification_review: {
      required: requiresReview,
      reasons: reviewReasons,
      unmatched_facets: unmatched
    }
  };
}
