/**
 * metadata-standard-v0.2.mjs - AI Metadata v0.2 结构规范与验证
 */

export function getDefaultV02Skeleton(source = {}, confidence = 'low', humanReviewReason = 'json_parse_failed') {
  const isFallback = confidence === 'low' && humanReviewReason !== '';
  const evidence = isFallback ? [{
    type: 'system',
    quote_or_summary: 'AI provider failed; fallback skeleton generated',
    supports: ['human_review_required']
  }] : [];
  
  const riskFlags = [];
  if (isFallback) {
    riskFlags.push('skeleton_fallback');
    if (humanReviewReason.includes('json') || humanReviewReason.includes('parse')) {
      riskFlags.push('ai_provider_json_parse_failed');
    }
  }

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
      human_review_reason: humanReviewReason !== undefined ? humanReviewReason : (isFallback ? 'AI Provider JSON 解析失败，已降级为 skeleton 结果' : ''),
      markdown_quality: 'partial',
      duplicate_candidate: false,
      retention_policy: 'keep_pending_review',
      risk_flags: riskFlags
    },
    evidence: evidence,
    recommended_catalog_path: '',
    catalog_change_type: 'needs_human_review'
  };
}

export function validateAndNormalizeV02(rawResult, source) {
  if (!rawResult || typeof rawResult !== 'object') {
    return getDefaultV02Skeleton(source, 'low', 'json_parse_failed');
  }

  // 如果缺少 primary_facets，生成低置信度骨架
  if (!rawResult.primary_facets || typeof rawResult.primary_facets !== 'object') {
    return getDefaultV02Skeleton(source, 'low', 'fields_missing');
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

**极其重要的指令：**
1. 你的完整且唯一的输出必须是一个且仅一个有效的 JSON 对象！
2. 绝对禁止在输出开头或结尾添加任何 Markdown 代码块标识（如 \`\`\`json 或 \`\`\`）。
3. 绝对禁止输出任何解释性文字、开场白或结束语（如"Here is the JSON"）。
4. 绝对禁止输出 <think> 标签或包含思维链过程。如果系统要求思考，请不要将思考过程输出到结果中。
5. 你返回的字符串必须能被系统直接执行 JSON.parse() 解析。
6. 所有字段必须符合 v0.2 schema。

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
