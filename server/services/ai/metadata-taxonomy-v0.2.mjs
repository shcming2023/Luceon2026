/**
 * metadata-taxonomy-v0.2.mjs - 受控分类与规范标签库
 * 
 * 定义 Luceon2026 AI Metadata 的受控分类（Taxonomy）和规范标签，
 * 并提供标准化方法，用于收敛大模型的自由输出。
 */

const TAXONOMY = {
  domain: [
    { id: 'academic', zh: '学术', en: 'Academic', aliases: ['学术研究', '高等教育', 'academic', 'research', 'higher education'] },
    { id: 'exam', zh: '考试', en: 'Exam', aliases: ['考试资料', '真题', '模拟题', 'exam', 'test', 'assessment'] },
    { id: 'travel', zh: '旅行', en: 'Travel', aliases: ['旅游', '出国', '行程', 'travel', 'trip', 'journey'] },
    { id: 'general', zh: '通用', en: 'General', aliases: ['综合', '一般', 'general', 'comprehensive'] }
  ],
  collection: [
    { id: 'igcse', zh: 'IGCSE', en: 'IGCSE', aliases: ['igcse', 'cambridge igcse'] },
    { id: 'alevel', zh: 'A-Level', en: 'A-Level', aliases: ['a-level', 'alevel', 'al'] },
    { id: 'gaokao', zh: '高考', en: 'Gaokao', aliases: ['gaokao', 'chinese national college entrance exam'] }
  ],
  curriculum: [
    { id: 'cambridge', zh: '剑桥', en: 'Cambridge', aliases: ['cambridge', 'cie', 'caie'] },
    { id: 'edexcel', zh: '爱德思', en: 'Edexcel', aliases: ['edexcel', 'pearson'] },
    { id: 'cn_national', zh: '国标', en: 'CN National', aliases: ['cn_national', '中国国家课程', '人教版'] }
  ],
  stage: [
    { id: 'primary', zh: '小学', en: 'Primary', aliases: ['primary', '小学', 'elementary'] },
    { id: 'middle', zh: '初中', en: 'Middle', aliases: ['middle', '初中', 'junior high'] },
    { id: 'high', zh: '高中', en: 'High', aliases: ['high', '高中', 'senior high'] }
  ],
  level: [
    { id: 'grade_9', zh: '九年级', en: 'Grade 9', aliases: ['grade 9', '九年级', '初三'] },
    { id: 'grade_10', zh: '十年级', en: 'Grade 10', aliases: ['grade 10', '十年级', '高一'] },
    { id: 'grade_11', zh: '十一年级', en: 'Grade 11', aliases: ['grade 11', '十一年级', '高二'] },
    { id: 'grade_12', zh: '十二年级', en: 'Grade 12', aliases: ['grade 12', '十二年级', '高三'] }
  ],
  subject: [
    { id: 'math', zh: '数学', en: 'Mathematics', aliases: ['math', 'mathematics', '数学', '理科'] },
    { id: 'english', zh: '英语', en: 'English', aliases: ['english', '英语', 'esl', 'efl'] },
    { id: 'physics', zh: '物理', en: 'Physics', aliases: ['physics', '物理'] },
    { id: 'chemistry', zh: '化学', en: 'Chemistry', aliases: ['chemistry', '化学'] },
    { id: 'biology', zh: '生物', en: 'Biology', aliases: ['biology', '生物'] },
    { id: 'personal_items', zh: '个人物品', en: 'Personal Items', aliases: ['personal items', '个人物品', '行李', '行李清单'] }
  ],
  resource_type: [
    { id: 'coursebook', zh: '教材', en: 'Coursebook', aliases: ['coursebook', 'textbook', '教材', '课本'] },
    { id: 'past_paper', zh: '真题', en: 'Past Paper', aliases: ['past paper', '真题', '历年真题'] },
    { id: 'syllabus', zh: '大纲', en: 'Syllabus', aliases: ['syllabus', '考纲', '大纲'] },
    { id: 'list', zh: '清单', en: 'List', aliases: ['list', '清单', '列表'] }
  ],
  component_role: [
    { id: 'main_content', zh: '正文', en: 'Main Content', aliases: ['main content', '正文', '主体'] },
    { id: 'answer_key', zh: '答案', en: 'Answer Key', aliases: ['answer key', '答案', '参考答案'] },
    { id: 'index', zh: '索引', en: 'Index', aliases: ['index', '索引', '目录'] }
  ]
};

const TAGS_TAXONOMY = {
  topic_tags: [
    { id: 'algebra', zh: '代数', en: 'Algebra', aliases: ['代数', 'algebra'] },
    { id: 'geometry', zh: '几何', en: 'Geometry', aliases: ['几何', 'geometry'] },
    { id: 'mechanics', zh: '力学', en: 'Mechanics', aliases: ['力学', 'mechanics'] },
    { id: 'reading', zh: '阅读理解', en: 'Reading Comprehension', aliases: ['阅读理解', 'reading comprehension', 'reading'] },
    { id: 'travel_tips', zh: '旅行提示', en: 'Travel Tips', aliases: ['旅行提示', 'travel tips', '注意事项'] }
  ],
  skill_tags: [
    { id: 'problem_solving', zh: '问题解决', en: 'Problem Solving', aliases: ['问题解决', 'problem solving'] },
    { id: 'critical_thinking', zh: '批判性思维', en: 'Critical Thinking', aliases: ['批判性思维', 'critical thinking'] },
    { id: 'analytical', zh: '分析能力', en: 'Analytical Skills', aliases: ['分析能力', 'analytical', 'analysis'] }
  ]
};

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
