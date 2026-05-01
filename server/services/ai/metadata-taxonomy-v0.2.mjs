/**
 * metadata-taxonomy-v0.2.mjs - 受控分类与规范标签库
 * 
 * 定义 Luceon2026 AI Metadata 的受控分类（Taxonomy）和规范标签，
 * 严格按照生产级字典对齐，所有未覆盖字段均需人工复核。
 */

const TAXONOMY = {
  domain: [
    { id: '01_published_course', zh: '01_出版教材与成套课程', en: 'Published Coursebooks and Sets', aliases: ['出版教材与成套课程', '教材', 'published', 'coursebook', 'course', 'set'] },
    { id: '02_exam_assessment', zh: '02_考试测评与真题', en: 'Exams, Assessments and Past Papers', aliases: ['考试测评与真题', '考试', '真题', '测评', 'exam', 'assessment', 'past paper', 'past papers'] },
    { id: '03_school_exclusive', zh: '03_学校与机构专属资料', en: 'School and Institution Exclusive Materials', aliases: ['学校与机构专属资料', '机构资料', '学校专属', 'school exclusive', 'institution exclusive'] },
    { id: '04_cn_curriculum', zh: '04_中国课标与同步教辅', en: 'Chinese National Curriculum and Supplements', aliases: ['中国课标与同步教辅', '中国课标', '同步教辅', 'cn national', 'chinese curriculum', 'cn curriculum'] },
    { id: '05_specific_training', zh: '05_专项训练与讲义', en: 'Specific Training and Handouts', aliases: ['专项训练与讲义', '专项训练', '讲义', 'specific training', 'handout', 'handouts'] },
    { id: '06_corp_admin', zh: '06_公司行政经营资料', en: 'Corporate and Administrative Materials', aliases: ['公司行政经营资料', '行政经营', '公司资料', 'corporate', 'administrative', 'admin'] },
    { id: '99_pending', zh: '99_待识别与低置信度', en: 'Pending and Low Confidence', aliases: ['待识别与低置信度', '待识别', '低置信度', 'pending', 'unknown', 'low confidence'] }
  ],
  collection: [
    { id: 'reading_explorer', zh: 'Reading Explorer', en: 'Reading Explorer', aliases: ['reading explorer', 're'] },
    { id: 'oxford_discover', zh: 'Oxford Discover', en: 'Oxford Discover', aliases: ['oxford discover', 'od'] },
    { id: 'oxford_reading_tree', zh: 'Oxford Reading Tree', en: 'Oxford Reading Tree', aliases: ['oxford reading tree', 'ort', '牛津树'] },
    { id: 'wonders', zh: 'Wonders', en: 'Wonders', aliases: ['wonders'] },
    { id: 'journeys', zh: 'Journeys', en: 'Journeys', aliases: ['journeys'] },
    { id: 'our_world', zh: 'Our World', en: 'Our World', aliases: ['our world', 'ow'] },
    { id: 'grammar_cue', zh: 'Grammar Cue', en: 'Grammar Cue', aliases: ['grammar cue'] },
    { id: 'grammar_in_use', zh: 'Grammar in Use', en: 'Grammar in Use', aliases: ['grammar in use', '剑桥语法'] },
    { id: 'grammar_in_context', zh: 'Grammar in Context', en: 'Grammar in Context', aliases: ['grammar in context'] },
    { id: 'great_writing', zh: 'Great Writing', en: 'Great Writing', aliases: ['great writing', 'gw'] },
    { id: 'time_zones', zh: 'Time Zones', en: 'Time Zones', aliases: ['time zones'] },
    { id: 'envision', zh: 'Envision', en: 'Envision', aliases: ['envision'] },
    { id: 'sg_math', zh: '新加坡数学', en: 'Singapore Math', aliases: ['新加坡数学', 'singapore math', 'sg math'] },
    { id: 'cambridge_igcse', zh: 'Cambridge IGCSE', en: 'Cambridge IGCSE', aliases: ['cambridge igcse', 'igcse', 'cie igcse', '剑桥igcse'] },
    { id: 'ket_pet', zh: 'KET_PET', en: 'KET_PET', aliases: ['ket_pet', 'ket', 'pet', '剑桥英语考试'] },
    { id: 'toefl_junior_primary', zh: 'TOEFL Junior_Primary', en: 'TOEFL Junior_Primary', aliases: ['toefl junior_primary', 'toefl junior', 'toefl primary', '小托福'] },
    { id: 'cn_curriculum_supplements', zh: '中国课标同步教辅各学科', en: 'Chinese Curriculum Supplements', aliases: ['中国课标同步教辅各学科', '同步教辅', 'cn supplements'] },
    { id: 'school_exclusive', zh: '学校或机构专属资料', en: 'School Exclusive Materials', aliases: ['学校或机构专属资料', '机构专属'] },
    { id: 'specific_training', zh: '专项训练与讲义', en: 'Specific Training', aliases: ['专项训练与讲义', '专项训练'] },
    { id: 'other_intl_textbooks', zh: '其他国际教材', en: 'Other International Textbooks', aliases: ['其他国际教材', 'other international textbooks'] },
    { id: 'unknown', zh: '未知', en: 'Unknown', aliases: ['未知', 'unknown', 'n/a'] }
  ],
  curriculum: [
    { id: 'cambridge', zh: 'Cambridge', en: 'Cambridge', aliases: ['cambridge', 'cie', 'caie', '剑桥'] },
    { id: 'edexcel', zh: 'Edexcel', en: 'Edexcel', aliases: ['edexcel', 'pearson', '爱德思'] },
    { id: 'aqa', zh: 'AQA', en: 'AQA', aliases: ['aqa', 'oxford aqa'] },
    { id: 'cn_national', zh: '中国课标', en: 'Chinese National Curriculum', aliases: ['中国课标', '国标', 'cn national', '人教版', '外研版'] },
    { id: 'ib', zh: 'IB', en: 'IB', aliases: ['ib', 'international baccalaureate'] },
    { id: 'ap', zh: 'AP', en: 'AP', aliases: ['ap', 'advanced placement'] },
    { id: 'unknown', zh: '未知', en: 'Unknown', aliases: ['未知', 'unknown', 'n/a'] }
  ],
  stage: [
    { id: 'early_years', zh: '学前', en: 'Early Years', aliases: ['early years', 'kindergarten', '学前', '幼教'] },
    { id: 'primary', zh: '小学', en: 'Primary', aliases: ['primary', '小学', 'elementary'] },
    { id: 'middle', zh: '初中', en: 'Middle', aliases: ['middle', '初中', 'junior high', 'igcse', 'lower secondary'] },
    { id: 'high', zh: '高中', en: 'High', aliases: ['high', '高中', 'senior high', 'a-level', 'alevel', 'upper secondary'] },
    { id: 'higher_ed', zh: '高等教育', en: 'Higher Education', aliases: ['higher ed', '高等教育', 'university', 'college'] },
    { id: 'adult', zh: '成人', en: 'Adult', aliases: ['adult', '成人'] },
    { id: 'all', zh: '全学段', en: 'All Stages', aliases: ['all', '全学段', 'general'] }
  ],
  level: [
    // Primary
    { id: 'grade_1', zh: '一年级', en: 'Grade 1', aliases: ['grade 1', '一年级'] },
    { id: 'grade_2', zh: '二年级', en: 'Grade 2', aliases: ['grade 2', '二年级'] },
    { id: 'grade_3', zh: '三年级', en: 'Grade 3', aliases: ['grade 3', '三年级'] },
    { id: 'grade_4', zh: '四年级', en: 'Grade 4', aliases: ['grade 4', '四年级'] },
    { id: 'grade_5', zh: '五年级', en: 'Grade 5', aliases: ['grade 5', '五年级'] },
    { id: 'grade_6', zh: '六年级', en: 'Grade 6', aliases: ['grade 6', '六年级'] },
    // Middle
    { id: 'grade_7', zh: '七年级', en: 'Grade 7', aliases: ['grade 7', '七年级', '初一', 'year 8'] },
    { id: 'grade_8', zh: '八年级', en: 'Grade 8', aliases: ['grade 8', '八年级', '初二', 'year 9'] },
    { id: 'grade_9', zh: '九年级', en: 'Grade 9', aliases: ['grade 9', '九年级', '初三', 'year 10'] },
    // High
    { id: 'grade_10', zh: '十年级', en: 'Grade 10', aliases: ['grade 10', '十年级', '高一', 'year 11'] },
    { id: 'grade_11', zh: '十一年级', en: 'Grade 11', aliases: ['grade 11', '十一年级', '高二', 'year 12', 'as', 'as level'] },
    { id: 'grade_12', zh: '十二年级', en: 'Grade 12', aliases: ['grade 12', '十二年级', '高三', 'year 13', 'a2', 'a2 level'] },
    { id: 'unknown', zh: '未知', en: 'Unknown', aliases: ['未知', 'unknown', 'n/a'] }
  ],
  subject: [
    { id: 'english', zh: '英语', en: 'English', aliases: ['english', '英语', '英文', 'esl', 'efl', 'ela'] },
    { id: 'math', zh: '数学', en: 'Mathematics', aliases: ['math', 'mathematics', '数学', '数学学科', 'additional mathematics', 'further mathematics'] },
    { id: 'chinese', zh: '语文', en: 'Chinese', aliases: ['chinese', '语文', '中文'] },
    { id: 'science', zh: '科学', en: 'Science', aliases: ['science', '科学', 'combined science', 'coordinated sciences'] },
    { id: 'physics', zh: '物理', en: 'Physics', aliases: ['physics', '物理'] },
    { id: 'chemistry', zh: '化学', en: 'Chemistry', aliases: ['chemistry', '化学'] },
    { id: 'biology', zh: '生物', en: 'Biology', aliases: ['biology', '生物'] },
    { id: 'history', zh: '历史', en: 'History', aliases: ['history', '历史'] },
    { id: 'geography', zh: '地理', en: 'Geography', aliases: ['geography', '地理'] },
    { id: 'economics', zh: '经济', en: 'Economics', aliases: ['economics', '经济'] },
    { id: 'business', zh: '商业', en: 'Business Studies', aliases: ['business', '商业', 'business studies'] },
    { id: 'computer_science', zh: '计算机科学', en: 'Computer Science', aliases: ['computer science', '计算机科学', 'cs', 'ict'] },
    { id: 'unknown', zh: '未知', en: 'Unknown', aliases: ['未知', 'unknown', 'n/a', 'other'] }
  ],
  resource_type: [
    { id: 'textbook', zh: '教材', en: 'Textbook', aliases: ['教材', '课本', '教科书', 'textbook', 'coursebook', 'student book'] },
    { id: 'workbook', zh: '练习册', en: 'Workbook', aliases: ['练习册', '练习本', 'workbook', 'activity book', 'practice book'] },
    { id: 'exam_paper', zh: '试卷', en: 'Exam Paper', aliases: ['试卷', '考试卷', 'exam paper', 'test paper', 'mock paper'] },
    { id: 'past_paper', zh: '真题', en: 'Past Paper', aliases: ['真题', '历年真题', 'past paper'] },
    { id: 'handout', zh: '讲义', en: 'Handout', aliases: ['讲义', 'handout', 'notes', 'study notes'] },
    { id: 'slides', zh: '课件', en: 'Slides', aliases: ['课件', 'slides', 'presentation', 'ppt'] },
    { id: 'answer_key', zh: '答案解析', en: 'Answer Key', aliases: ['答案解析', '答案', 'answer key', 'solutions', 'mark scheme'] },
    { id: 'syllabus', zh: '大纲', en: 'Syllabus', aliases: ['大纲', '考纲', 'syllabus', 'specification'] },
    { id: 'vocabulary_list', zh: '词汇表', en: 'Vocabulary List', aliases: ['词汇表', 'vocabulary', 'vocab list', 'word list'] },
    { id: 'video_script', zh: '视频脚本', en: 'Video Script', aliases: ['视频脚本', 'video script', 'transcript', 'audio script'] },
    { id: 'admin_corp', zh: '行政经营资料', en: 'Administrative and Corporate Materials', aliases: ['行政经营资料', 'admin', 'corporate', '公司资料'] },
    { id: 'other', zh: '其他', en: 'Other', aliases: ['其他', 'other', 'misc'] },
    { id: 'pending', zh: '待识别', en: 'Pending', aliases: ['待识别', 'pending', 'unknown'] }
  ],
  component_role: [
    { id: 'main_content', zh: '主体资料', en: 'Main Content', aliases: ['主体资料', 'main content', '正文', 'body'] },
    { id: 'student_book', zh: '学生用书', en: 'Student Book', aliases: ['学生用书', '学生书', '学生教材', 'student book', 'sb', 'learner book'] },
    { id: 'teacher_book', zh: '教师用书', en: 'Teacher Book', aliases: ['教师用书', '教师书', '教师版', 'teacher book', 'tb', 'teacher guide'] },
    { id: 'workbook_component', zh: '练习册', en: 'Workbook', aliases: ['练习册', '练习本', 'workbook', 'wb', 'activity book'] },
    { id: 'answers', zh: '答案', en: 'Answers', aliases: ['答案', '参考答案', 'answers', 'key', 'answer key'] },
    { id: 'answer_explanations', zh: '答案解析', en: 'Answer Explanations', aliases: ['答案解析', '答案详解', 'explanations', 'solutions', 'mark scheme'] },
    { id: 'vocabulary', zh: '词汇表', en: 'Vocabulary', aliases: ['词汇表', 'vocabulary', 'glossary'] },
    { id: 'index_toc', zh: '目录/索引', en: 'Index/TOC', aliases: ['目录/索引', '目录', '索引', 'index', 'toc', 'table of contents'] },
    { id: 'appendix', zh: '附录', en: 'Appendix', aliases: ['附录', 'appendix', 'appendices'] },
    { id: 'slides_component', zh: '课件', en: 'Slides', aliases: ['课件', 'slides', 'presentation'] },
    { id: 'other_component', zh: '其他组件', en: 'Other Component', aliases: ['其他组件', 'other component', 'other'] },
    { id: 'pending', zh: '待识别', en: 'Pending', aliases: ['待识别', 'pending', 'unknown'] }
  ]
};

const TAGS_TAXONOMY = {
  topic_tags: [
    { id: 'algebra', zh: '代数', en: 'Algebra', aliases: ['代数', 'algebra'] },
    { id: 'geometry', zh: '几何', en: 'Geometry', aliases: ['几何', 'geometry'] },
    { id: 'mechanics', zh: '力学', en: 'Mechanics', aliases: ['力学', 'mechanics'] },
    { id: 'reading', zh: '阅读理解', en: 'Reading Comprehension', aliases: ['阅读理解', 'reading comprehension', 'reading'] },
    { id: 'writing', zh: '写作', en: 'Writing', aliases: ['写作', 'writing'] },
    { id: 'listening', zh: '听力', en: 'Listening', aliases: ['听力', 'listening'] },
    { id: 'speaking', zh: '口语', en: 'Speaking', aliases: ['口语', 'speaking'] },
    { id: 'grammar', zh: '语法', en: 'Grammar', aliases: ['语法', 'grammar'] },
    { id: 'vocabulary', zh: '词汇', en: 'Vocabulary', aliases: ['词汇', 'vocabulary', 'vocab'] }
  ],
  skill_tags: [
    { id: 'problem_solving', zh: '问题解决', en: 'Problem Solving', aliases: ['问题解决', 'problem solving'] },
    { id: 'critical_thinking', zh: '批判性思维', en: 'Critical Thinking', aliases: ['批判性思维', 'critical thinking'] },
    { id: 'analytical', zh: '分析能力', en: 'Analytical Skills', aliases: ['分析能力', 'analytical', 'analysis'] },
    { id: 'calculation', zh: '计算', en: 'Calculation', aliases: ['计算', 'calculation'] }
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

  // 非教育资料检测：如果 domain 最终映射为 06（行政）或 99（待识别），需人工复核
  if (controlled.domain && (controlled.domain.id === '06_corp_admin' || controlled.domain.id === '99_pending')) {
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
