/**
 * metadata-standard-v0.2.mjs - AI Metadata v0.2 结构规范与验证
 */

export function getDefaultV02Skeleton(source = {}, confidence = 'low', humanReviewReason = '识别失败') {
  return {
    source: {
      material_id: source.materialId || '',
      file_name: source.filename || '',
      file_size: source.fileSize || 0,
      raw_object_name: source.rawObjectName || '',
      parsed_prefix: source.parsedPrefix || '',
      markdown_object_name: source.markdownObjectName || ''
    },
    primary_facets: {
      domain: { zh: '', en: '' },
      collection: { zh: '', en: '' },
      curriculum: { zh: '', en: '' },
      stage: { zh: '', en: '' },
      level: { zh: '', en: '' },
      subject: { zh: '', en: '' },
      resource_type: { zh: '', en: '' },
      component_role: { zh: '', en: '' }
    },
    descriptive_metadata: {
      series_title: '',
      edition: '',
      year: '',
      publisher_org: '',
      language: ''
    },
    search_tags: {
      topic_tags: [],
      skill_tags: []
    },
    governance: {
      confidence: confidence,
      human_review_required: true,
      human_review_reason: humanReviewReason,
      markdown_quality: 'partial',
      duplicate_candidate: false,
      retention_policy: 'keep_pending_review',
      risk_flags: []
    },
    evidence: [],
    recommended_catalog_path: '',
    catalog_change_type: 'needs_human_review'
  };
}

export function validateAndNormalizeV02(rawResult, source) {
  if (!rawResult || typeof rawResult !== 'object') {
    return getDefaultV02Skeleton(source, 'low', 'JSON解析失败');
  }

  // 如果缺少 primary_facets，生成低置信度骨架
  if (!rawResult.primary_facets || typeof rawResult.primary_facets !== 'object') {
    return getDefaultV02Skeleton(source, 'low', '缺少 primary_facets');
  }

  const result = {
    source: { ...getDefaultV02Skeleton(source).source, ...(rawResult.source || {}) },
    primary_facets: { ...getDefaultV02Skeleton(source).primary_facets, ...(rawResult.primary_facets || {}) },
    descriptive_metadata: { ...getDefaultV02Skeleton(source).descriptive_metadata, ...(rawResult.descriptive_metadata || {}) },
    search_tags: { ...getDefaultV02Skeleton(source).search_tags, ...(rawResult.search_tags || {}) },
    governance: { ...getDefaultV02Skeleton(source, 'high', '').governance, ...(rawResult.governance || {}) },
    evidence: Array.isArray(rawResult.evidence) ? rawResult.evidence : [],
    recommended_catalog_path: rawResult.recommended_catalog_path || '',
    catalog_change_type: rawResult.catalog_change_type || 'needs_human_review'
  };

  // 置信度规范化
  if (!['high', 'medium', 'low'].includes(result.governance.confidence)) {
    result.governance.confidence = 'low';
  }

  // low 必须 human_review_required=true
  if (result.governance.confidence === 'low') {
    result.governance.human_review_required = true;
    if (!result.governance.human_review_reason) {
      result.governance.human_review_reason = 'Low confidence';
    }
  }

  // proposed_new_tags 或未知集合 必须 review
  if (result.search_tags.proposed_new_tags && result.search_tags.proposed_new_tags.length > 0) {
    result.governance.human_review_required = true;
    if (!result.governance.human_review_reason) {
      result.governance.human_review_reason = 'Contains proposed new tags';
    }
  }

  // human_review_reason 不得为空
  if (result.governance.human_review_required && !result.governance.human_review_reason) {
    result.governance.human_review_reason = 'Review required';
  }

  return result;
}

export function generateV02Prompt() {
  return `你是一个专业的教育资源元数据提取助手。你的任务是从提供的 Markdown 文本中提取结构化信息。
请严格按照以下 JSON 格式返回结果，不要包含任何解释性文本或 Markdown 代码块标识。不要输出 <think> 标签或思维链过程，如果模型自带思维过程，请确保它在 JSON 块之外。

JSON 结构必须符合以下 AI Metadata v0.2 标准：
{
  "source": {
    "material_id": "",
    "file_name": "",
    "file_size": 0,
    "raw_object_name": "",
    "parsed_prefix": "",
    "markdown_object_name": ""
  },
  "primary_facets": {
    "domain": {"zh": "如: 基础教育/高等教育", "en": ""},
    "collection": {"zh": "如: 课内同步/课外拓展", "en": ""},
    "curriculum": {"zh": "如: 人教版/部编版", "en": ""},
    "stage": {"zh": "如: 小学/初中", "en": ""},
    "level": {"zh": "如: 一年级/初一", "en": ""},
    "subject": {"zh": "如: 语文/数学", "en": ""},
    "resource_type": {"zh": "如: 试卷/教案", "en": ""},
    "component_role": {"zh": "如: 学生用书/教师用书", "en": ""}
  },
  "descriptive_metadata": {
    "series_title": "",
    "edition": "",
    "year": "",
    "publisher_org": "",
    "language": "中文或英文等"
  },
  "search_tags": {
    "topic_tags": ["知识点1", "知识点2"],
    "skill_tags": ["能力1", "能力2"]
  },
  "governance": {
    "confidence": "high|medium|low",
    "human_review_required": true或false,
    "human_review_reason": "如果是低置信度或需要复核，提供原因",
    "markdown_quality": "full|partial|poor",
    "duplicate_candidate": false,
    "retention_policy": "keep|keep_pending_review|discard",
    "risk_flags": []
  },
  "evidence": ["提取关键信息的原文片段作为证据"],
  "recommended_catalog_path": "推荐挂载的目录路径",
  "catalog_change_type": "needs_human_review"
}
`;
}
