/**
 * metadata-standard-v0.2.mjs - AI Metadata v0.2 结构规范与验证
 */

export function normalizeEvidence(rawEvidence) {
  if (!Array.isArray(rawEvidence)) return [];
  const normalized = [];
  for (const ev of rawEvidence) {
    if (typeof ev === 'string') {
      if (ev.trim()) {
        normalized.push({
          type: 'unknown',
          quote_or_summary: ev.trim(),
          supports: []
        });
      }
    } else if (typeof ev === 'object' && ev !== null) {
      const type = ev.type || 'unknown';
      const validTypes = ['filename', 'title_page', 'toc', 'body', 'system', 'unknown'];
      const finalType = validTypes.includes(type) ? type : 'unknown';
      const quote = typeof ev.quote_or_summary === 'string' ? ev.quote_or_summary.trim() : '';
      if (quote) {
        normalized.push({
          type: finalType,
          quote_or_summary: quote,
          supports: Array.isArray(ev.supports) ? ev.supports : []
        });
      }
    }
  }
  return normalized.slice(0, 20); // 最多保留 20 条
}

export function generateSystemTags(source = {}) {
  const format_tags = [];
  const filenameStr = String(source.file_name || source.filename || '').toLowerCase();
  const mimeTypeStr = String(source.mimeType || '').toLowerCase();
  if (filenameStr.endsWith('.pdf') || mimeTypeStr.includes('pdf')) {
    format_tags.push({ zh: 'PDF', en: 'pdf' });
  }
  const profile = source.mineruExecutionProfile || {};
  if (profile.enableOcr === true || String(profile.parseMethod).toLowerCase() === 'ocr') {
    format_tags.push({ zh: 'OCR', en: 'ocr_enabled' });
  }

  const artifact_tags = [];
  const parsedCount = Number(source.parsedFilesCount) || 0;
  if (parsedCount > 0) artifact_tags.push({ zh: '含解析产物', en: 'has_parsed_artifacts' });
  if (parsedCount >= 50) artifact_tags.push({ zh: '多产物文档', en: 'many_artifacts' });

  const engine_tags = [];
  const backend = String(profile.backendEffective || profile.backendRequested || '').toLowerCase();
  if (backend.includes('pipeline')) engine_tags.push({ zh: 'Pipeline', en: 'pipeline' });
  else if (backend.includes('hybrid')) engine_tags.push({ zh: 'Hybrid', en: 'hybrid_auto_engine' });
  else if (backend.includes('vlm')) engine_tags.push({ zh: 'VLM', en: 'vlm_auto_engine' });
  
  if (profile.enableTable === true || profile.enableTable === 'true') engine_tags.push({ zh: '表格识别', en: 'table_enabled' });
  if (profile.enableFormula === true || profile.enableFormula === 'true') engine_tags.push({ zh: '公式识别', en: 'formula_enabled' });

  return { format_tags, artifact_tags, engine_tags };
}

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
    const reason = String(humanReviewReason || '').toLowerCase();
    if (
      reason.includes('json') ||
      reason.includes('parse') ||
      reason.includes('解析失败')
    ) {
      riskFlags.push('ai_provider_json_parse_failed');
    }
    if (reason.includes('repair') || reason.includes('修复失败')) {
      riskFlags.push('ai_provider_json_repair_failed');
    }
  }

  const parsedCount = Number(source.parsedFilesCount) || 0;
  const riskSignals = [...riskFlags];
  const qualitySignals = [];
  if (isFallback) riskSignals.push('skeleton_fallback');
  if (confidence === 'low') riskSignals.push('low_confidence');
  riskSignals.push('needs_human_review'); // Fallback always requires human review
  if (parsedCount === 0) qualitySignals.push('no_parsed_artifacts');
  if (evidence.length === 0) riskSignals.push('evidence_missing');

  return {
    source: {
      material_id: source.materialId || '',
      file_name: source.filename || '',
      file_size: source.fileSize || 0,
      mime_type: source.mimeType || '',
      raw_object_name: source.rawObjectName || '',
      parsed_prefix: source.parsedPrefix || '',
      markdown_object_name: source.markdownObjectName || '',
      parsed_files_count: source.parsedFilesCount || 0,
      mineru_execution_profile: source.mineruExecutionProfile || {}
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
    system_tags: generateSystemTags(source),
    governance_signals: {
      quality: qualitySignals,
      relationship: [],
      retention: [],
      risk: riskSignals
    },
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

  const systemSource = getDefaultV02Skeleton(source).source;
  const result = {
    source: { 
      ...systemSource,
      llm_source_hint: rawResult.source || undefined
    },
    primary_facets: { ...getDefaultV02Skeleton(source).primary_facets, ...(rawResult.primary_facets || {}) },
    descriptive_metadata: { ...getDefaultV02Skeleton(source).descriptive_metadata, ...(rawResult.descriptive_metadata || {}) },
    search_tags: { ...getDefaultV02Skeleton(source).search_tags, ...(rawResult.search_tags || {}) },
    governance: { ...getDefaultV02Skeleton(source, 'high', '').governance, ...(rawResult.governance || {}) },
    evidence: normalizeEvidence(rawResult.evidence),
    system_tags: generateSystemTags(source),
    governance_signals: {
      quality: [],
      relationship: [],
      retention: [],
      risk: []
    },
    recommended_catalog_path: rawResult.recommended_catalog_path || '',
    catalog_change_type: rawResult.catalog_change_type || 'needs_human_review'
  };

  // 置信度规范化
  if (!['high', 'medium', 'low'].includes(result.governance.confidence)) {
    result.governance.confidence = 'low';
  }

  // 最小受控值校验 (domain, subject, resource_type, component_role)
  result.governance.risk_flags = result.governance.risk_flags || [];
  
  if (!result.primary_facets.domain || !result.primary_facets.domain.zh) {
    result.governance.confidence = 'low';
    result.governance.human_review_required = true;
    if (!result.governance.risk_flags.includes('domain_missing')) {
      result.governance.risk_flags.push('domain_missing');
    }
  }
  
  if (!result.primary_facets.subject || !result.primary_facets.subject.zh) {
    result.governance.human_review_required = true;
    if (!result.governance.risk_flags.includes('subject_missing')) {
      result.governance.risk_flags.push('subject_missing');
    }
  }

  const resTypeZh = result.primary_facets.resource_type?.zh;
  const compRoleZh = result.primary_facets.component_role?.zh;
  if (!resTypeZh && !compRoleZh) {
    result.governance.human_review_required = true;
    if (!result.governance.risk_flags.includes('resource_type_missing')) {
      result.governance.risk_flags.push('resource_type_missing');
    }
  }

  if (result.evidence.length === 0) {
    result.governance.human_review_required = true;
    if (!result.governance.risk_flags.includes('evidence_missing')) {
      result.governance.risk_flags.push('evidence_missing');
    }
  }

  // low 必须 human_review_required=true
  if (result.governance.confidence === 'low') {
    result.governance.human_review_required = true;
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
    if (result.evidence.length === 0) {
      result.governance.human_review_reason = 'Evidence missing';
    } else {
      result.governance.human_review_reason = 'Review required';
    }
  }

  // 生成 governance_signals
  const riskSignals = new Set(result.governance.risk_flags);
  if (result.governance.confidence === 'low') riskSignals.add('low_confidence');
  if (result.governance.human_review_required) riskSignals.add('needs_human_review');
  if (result.evidence.length === 0) riskSignals.add('evidence_missing');
  
  const parsedCount = Number(source.parsedFilesCount) || 0;
  if (parsedCount === 0) result.governance_signals.quality.push('no_parsed_artifacts');
  
  result.governance_signals.risk = Array.from(riskSignals);

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

export function generateV02DraftPrompt() {
  return `你是一个专业的教育资源元数据提取助手。你的任务是从提供的 Markdown 文本中提取结构化信息草稿。

**指令：**
1. 请根据教育资源分类标准，提取资料的以下要素：
- title (标题)
- domain, collection, curriculum, stage, level, subject, resource_type, component_role
- evidence snippets (提取关键信息的原文片段作为证据)
- uncertainty (不确定性或需要复核的原因)
2. 你可以输出一定的文本描述和草稿，但尽量结构化，不要长篇大论。
3. 绝对禁止输出 <think> 标签或包含复杂的推理过程。`;
}

export function generateV02RepairPrompt(draftContent) {
  return `你是一个 JSON 修复与格式化助手。请根据以下提取的草稿内容，将其严格格式化为符合 AI Metadata v0.2 标准的唯一 JSON 对象。

**草稿内容（可能是旧式 JSON、扁平 JSON、或自然语言草稿）：**
${draftContent}

**极其重要的硬规则：**
1. 必须将输入转换为 v0.2 canonical schema。
2. 你的完整且唯一的输出必须是一个且仅一个有效的 JSON 对象！
3. 绝对禁止在输出开头或结尾添加任何 Markdown 代码块标识（如 \`\`\`json 或 \`\`\`）。
4. 绝对禁止输出任何解释性文字、开场白或结束语（如"Here is the JSON"）。
5. 绝对禁止输出 <think> 标签或包含思维链过程。
6. 你返回的字符串必须能被系统直接执行 JSON.parse() 解析。
7. 不允许缺少 primary_facets。
8. evidence 必须为数组。
9. governance 必须存在。

**旧字段映射建议：**
- domain -> primary_facets.domain
- subject -> primary_facets.subject
- resource_type -> primary_facets.resource_type
- component_role -> primary_facets.component_role
- evidence_snippets -> evidence
- title -> descriptive_metadata.series_title 或 evidence，不要当作根字段保留

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
  "evidence": [{"type": "content", "quote_or_summary": "提取关键信息的原文片段作为证据", "supports": []}],
  "recommended_catalog_path": "推荐挂载的目录路径",
  "catalog_change_type": "needs_human_review"
}
`;
}
