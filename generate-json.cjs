const fs = require('fs');

const taxonomy = {
  "taxonomy_version": "v0.1",
  "rules_version": "v0.1",
  "status": "active",
  "review_status": {
    "status": "production_seed",
    "review_required": true,
    "last_reviewed_at": null
  },
  "migration": {
    "from": "metadata-taxonomy-v0.2.mjs static taxonomy",
    "strategy": "json_fact_source_with_dynamic_prompt_and_validator",
    "reclassification_supported": true
  },
  "facet_rules": {
    "domain": ["domain 是资料域，不是内容主题。"],
    "collection": ["collection 是套系/集合，不是单个文件标题。"],
    "resource_type": [
      "resource_type 是资料本体类型。",
      "试卷/范文/写作素材 必须能按规则优先归为 试卷，同时可把 范文/写作素材 放到 topic/proposed tags 或 component_role"
    ],
    "component_role": [
      "component_role 是套系或文档结构角色。",
      "写作任务说明、参考范文、仿写练习 不能整体 unmatched，至少要能拆分或归为多个组件信号，若当前结构只允许一个 component_role，则主值进入 主体资料 或 写作任务说明，其余进入 tags/proposed/review detail。",
      "answer key / 参考答案 / 答案详解 要正确区分答案与答案解析。"
    ]
  },
  "tag_rules": {
    "topic_tags": ["topic_tags 是内容主题，不承载主分类。"],
    "skill_tags": ["skill_tags 是能力目标，不承载主分类。"],
    "proposed_new_tags": ["proposed_new_tags 不能进入正式 tags。"]
  },
  "controlled_facets": {
    "domain": [
      { "id": "01_出版教材与成套课程", "zh": "出版教材与成套课程", "en": "Published Textbooks & Courseware", "status": "approved", "aliases": ["Cambridge IGCSE Coursebook", "Reading Explorer Student Book", "教材", "课本", "coursebook", "textbook"], "decision_rules": ["If it is a formally published textbook, coursebook, or complete course series."], "positive_examples": ["Cambridge IGCSE Coursebook", "Reading Explorer Student Book"], "negative_examples": ["中考真题"] },
      { "id": "02_考试测评与真题", "zh": "考试测评与真题", "en": "Exams & Past Papers", "status": "approved", "aliases": ["中考模拟", "二模卷", "二模", "真题", "模拟题", "试卷", "past paper", "mock paper", "test paper", "中考备考"], "decision_rules": ["If the material is an exam paper, mock test, or past paper."], "positive_examples": ["中考英语二模卷", "2023年北京中考数学真题"], "negative_examples": ["同步教辅"] },
      { "id": "03_学校与机构专属资料", "zh": "学校与机构专属资料", "en": "School & Institution Exclusive Materials", "status": "approved", "aliases": ["内部讲义", "校本教材"], "decision_rules": ["Exclusive materials developed by a school or training institution."], "positive_examples": ["某某附中内部训练题"], "negative_examples": ["公开出版物"] },
      { "id": "04_中国课标与同步教辅", "zh": "中国课标与同步教辅", "en": "Chinese Curriculum & Supplementary Materials", "status": "approved", "aliases": ["基础教育", "中小学", "中国课标", "初中英语", "中考英语"], "decision_rules": ["Materials strictly following the Chinese national curriculum."], "positive_examples": ["人教版初二英语同步练习", "中考英语"], "negative_examples": ["IGCSE教材"] },
      { "id": "05_专项训练与讲义", "zh": "专项训练与讲义", "en": "Targeted Training & Handouts", "status": "approved", "aliases": ["专项训练", "专项突破", "讲义", "写作特训"], "decision_rules": ["Materials focused on a specific skill or topic."], "positive_examples": ["初中英语写作提分讲义", "阅读理解专项训练"], "negative_examples": ["全科教材"] },
      { "id": "06_公司行政经营资料", "zh": "公司行政经营资料", "en": "Corporate & Administrative Materials", "status": "approved", "aliases": ["出国清单", "行政资料", "公司经营", "管理规章"], "decision_rules": ["Non-educational materials related to business, operations, or administration."], "positive_examples": ["出国留学清单.pdf", "员工手册"], "negative_examples": ["Educational textbook"] },
      { "id": "99_待识别与低置信度", "zh": "待识别与低置信度", "en": "Pending & Low Confidence", "status": "approved", "aliases": ["未知", "待识别", "unknown"], "decision_rules": ["Use this if the domain cannot be confidently determined."], "positive_examples": ["A corrupted or completely unrelated text fragment."], "negative_examples": [] }
    ],
    "collection": [
      {"id": "Reading Explorer", "zh": "Reading Explorer", "en": "Reading Explorer", "status": "approved", "aliases": ["Reading Explorer Student Book", "Reading Explorer Answer Key"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Oxford Discover", "zh": "Oxford Discover", "en": "Oxford Discover", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Oxford Reading Tree", "zh": "Oxford Reading Tree", "en": "Oxford Reading Tree", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Wonders", "zh": "Wonders", "en": "Wonders", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Journeys", "zh": "Journeys", "en": "Journeys", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Our World", "zh": "Our World", "en": "Our World", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Grammar Cue", "zh": "Grammar Cue", "en": "Grammar Cue", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Grammar in Use", "zh": "Grammar in Use", "en": "Grammar in Use", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Grammar in Context", "zh": "Grammar in Context", "en": "Grammar in Context", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Great Writing", "zh": "Great Writing", "en": "Great Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Time Zones", "zh": "Time Zones", "en": "Time Zones", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Envision", "zh": "Envision", "en": "Envision", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "新加坡数学", "zh": "新加坡数学", "en": "Singapore Math", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Cambridge IGCSE", "zh": "Cambridge IGCSE", "en": "Cambridge IGCSE", "status": "approved", "aliases": ["Cambridge IGCSE Additional Mathematics"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "KET_PET", "zh": "KET_PET", "en": "KET_PET", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "TOEFL Junior_Primary", "zh": "TOEFL Junior_Primary", "en": "TOEFL Junior_Primary", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "中国课标同步教辅各学科", "zh": "中国课标同步教辅各学科", "en": "Chinese Curriculum Supplementary Materials", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "学校或机构专属资料", "zh": "学校或机构专属资料", "en": "School/Institution Exclusive", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "专项训练与讲义", "zh": "专项训练与讲义", "en": "Targeted Training & Handouts", "status": "approved", "aliases": ["中考模拟", "二模卷", "中考写作", "初中英语写作"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "其他国际教材", "zh": "其他国际教材", "en": "Other International Curricula", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "未知", "zh": "未知", "en": "Unknown", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "curriculum": [
      {"id": "Cambridge", "zh": "Cambridge", "en": "Cambridge", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Edexcel", "zh": "Edexcel", "en": "Edexcel", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "AQA", "zh": "AQA", "en": "AQA", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "IB", "zh": "IB", "en": "IB", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "AP", "zh": "AP", "en": "AP", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "中国课标", "zh": "中国课标", "en": "Chinese Curriculum", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Common Core", "zh": "Common Core", "en": "Common Core", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "新加坡数学", "zh": "新加坡数学", "en": "Singapore Math", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "KET_PET", "zh": "KET_PET", "en": "KET_PET", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "TOEFL Junior_Primary", "zh": "TOEFL Junior_Primary", "en": "TOEFL Junior_Primary", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "未知", "zh": "未知", "en": "Unknown", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "stage": [
      {"id": "学前", "zh": "学前", "en": "Preschool", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "小学", "zh": "小学", "en": "Primary School", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "初中", "zh": "初中", "en": "Middle School", "status": "approved", "aliases": ["中考备考", "初中备考", "Grade 8", "Grade 9"], "decision_rules": ["若包含中考备考、初中备考、Grade 8/9、初中等，进入初中", "IGCSE 可进入初中或高中，若不确定必须 review"], "positive_examples": [], "negative_examples": []},
      {"id": "高中", "zh": "高中", "en": "High School", "status": "approved", "aliases": [], "decision_rules": ["IGCSE 可进入初中或高中，若不确定必须 review"], "positive_examples": [], "negative_examples": []},
      {"id": "高等教育", "zh": "高等教育", "en": "Higher Education", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "成人", "zh": "成人", "en": "Adult", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "全学段", "zh": "全学段", "en": "All Stages", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "level": [
      {"id": "G1-G12", "zh": "一年级-十二年级", "en": "G1-G12", "status": "approved", "aliases": ["G1-G12"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "初一", "zh": "初一", "en": "Grade 7", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "初二", "zh": "初二", "en": "Grade 8", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "初三", "zh": "初三", "en": "Grade 9", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "高一", "zh": "高一", "en": "Grade 10", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "高二", "zh": "高二", "en": "Grade 11", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "高三", "zh": "高三", "en": "Grade 12", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Level 1-6", "zh": "Level 1-6", "en": "Level 1-6", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "Foundation", "zh": "Foundation", "en": "Foundation", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "IGCSE", "zh": "IGCSE", "en": "IGCSE", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "AS", "zh": "AS", "en": "AS", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "A2", "zh": "A2", "en": "A2", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "未知", "zh": "未知", "en": "Unknown", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "subject": [
      {"id": "英语", "zh": "英语", "en": "English", "status": "approved", "aliases": ["English", "ESL", "EFL", "ELA", "英文", "初中英语", "中考英语"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "数学", "zh": "数学", "en": "Mathematics", "status": "approved", "aliases": ["Math", "Mathematics", "Additional Mathematics", "Further Mathematics", "数学学科"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "语文", "zh": "语文", "en": "Chinese", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "科学", "zh": "科学", "en": "Science", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "物理", "zh": "物理", "en": "Physics", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "化学", "zh": "化学", "en": "Chemistry", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "生物", "zh": "生物", "en": "Biology", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "历史", "zh": "历史", "en": "History", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "地理", "zh": "地理", "en": "Geography", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "经济", "zh": "经济", "en": "Economics", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "商业", "zh": "商业", "en": "Business", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "计算机科学", "zh": "计算机科学", "en": "Computer Science", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "写作", "zh": "写作", "en": "Writing", "status": "approved", "aliases": ["Reading", "Writing", "English Writing", "写作能力"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "阅读", "zh": "阅读", "en": "Reading", "status": "approved", "aliases": ["Reading"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "未知", "zh": "未知", "en": "Unknown", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "resource_type": [
      {"id": "教材", "zh": "教材", "en": "Textbook", "status": "approved", "aliases": ["课本", "教科书", "coursebook", "textbook"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "练习册", "zh": "练习册", "en": "Workbook", "status": "approved", "aliases": ["练习本", "workbook", "practice book"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "试卷", "zh": "试卷", "en": "Exam Paper", "status": "approved", "aliases": ["考试卷", "mock paper", "test paper", "中考模拟", "二模卷"], "decision_rules": ["试卷/范文/写作素材 必须能按规则优先归为 试卷"], "positive_examples": [], "negative_examples": []},
      {"id": "真题", "zh": "真题", "en": "Past Paper", "status": "approved", "aliases": ["past paper"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "模拟卷", "zh": "模拟卷", "en": "Mock Paper", "status": "approved", "aliases": ["模拟卷"], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "讲义", "zh": "讲义", "en": "Handout", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "课件", "zh": "课件", "en": "Courseware", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "答案解析", "zh": "答案解析", "en": "Answer Key & Explanations", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "大纲", "zh": "大纲", "en": "Syllabus", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "词汇表", "zh": "词汇表", "en": "Vocabulary List", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "视频脚本", "zh": "视频脚本", "en": "Video Script", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "范文", "zh": "范文", "en": "Sample Essay", "status": "approved", "aliases": ["范文"], "decision_rules": ["当有试卷时优先归为试卷，范文可做 topic"], "positive_examples": [], "negative_examples": []},
      {"id": "写作素材", "zh": "写作素材", "en": "Writing Materials", "status": "approved", "aliases": ["写作素材"], "decision_rules": ["可放在 topic_tags 或 component_role。若为试卷，优先为试卷"], "positive_examples": [], "negative_examples": []},
      {"id": "行政经营资料", "zh": "行政经营资料", "en": "Admin & Ops Materials", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "其他", "zh": "其他", "en": "Other", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "待识别", "zh": "待识别", "en": "Pending", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "component_role": [
      {"id": "主体资料", "zh": "主体资料", "en": "Main Material", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "学生用书", "zh": "学生用书", "en": "Student Book", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "教师用书", "zh": "教师用书", "en": "Teacher Book", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "练习册", "zh": "练习册", "en": "Workbook", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "答案", "zh": "答案", "en": "Answer Key", "status": "approved", "aliases": ["answer key", "参考答案"], "decision_rules": ["要正确区分答案与答案解析"], "positive_examples": [], "negative_examples": []},
      {"id": "答案解析", "zh": "答案解析", "en": "Answer Explanation", "status": "approved", "aliases": ["答案详解"], "decision_rules": ["要正确区分答案与答案解析"], "positive_examples": [], "negative_examples": []},
      {"id": "词汇表", "zh": "词汇表", "en": "Vocabulary", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "目录/索引", "zh": "目录/索引", "en": "Table of Contents/Index", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "附录", "zh": "附录", "en": "Appendix", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "课件", "zh": "课件", "en": "Courseware", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "写作任务说明", "zh": "写作任务说明", "en": "Writing Prompt", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "参考范文", "zh": "参考范文", "en": "Sample Essay", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "仿写练习", "zh": "仿写练习", "en": "Imitation Practice", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "其他组件", "zh": "其他组件", "en": "Other Component", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "待识别", "zh": "待识别", "en": "Pending", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ]
  },
  "tag_groups": {
    "topic_tags": [
      {"id": "代数", "zh": "代数", "en": "Algebra", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "几何", "zh": "几何", "en": "Geometry", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "力学", "zh": "力学", "en": "Mechanics", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "阅读理解", "zh": "阅读理解", "en": "Reading Comprehension", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "写作", "zh": "写作", "en": "Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "应用文写作", "zh": "应用文写作", "en": "Practical Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "议论文写作", "zh": "议论文写作", "en": "Argumentative Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "中考英语", "zh": "中考英语", "en": "Zhongkao English", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "演讲稿", "zh": "演讲稿", "en": "Speech", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "通知", "zh": "通知", "en": "Notice", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "倡议书", "zh": "倡议书", "en": "Proposal", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "邮件", "zh": "邮件", "en": "Email", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "环保", "zh": "环保", "en": "Environment", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "校园生活", "zh": "校园生活", "en": "Campus Life", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "个人成长", "zh": "个人成长", "en": "Personal Growth", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "社会实践", "zh": "社会实践", "en": "Social Practice", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "语法", "zh": "语法", "en": "Grammar", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "词汇", "zh": "词汇", "en": "Vocabulary", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "听力", "zh": "听力", "en": "Listening", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "口语", "zh": "口语", "en": "Speaking", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ],
    "skill_tags": [
      {"id": "问题解决", "zh": "问题解决", "en": "Problem Solving", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "批判性思维", "zh": "批判性思维", "en": "Critical Thinking", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "分析能力", "zh": "分析能力", "en": "Analytical Skills", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "计算", "zh": "计算", "en": "Calculation", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "阅读理解", "zh": "阅读理解", "en": "Reading Comprehension", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "写作能力", "zh": "写作能力", "en": "Writing Skills", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "应用文写作", "zh": "应用文写作", "en": "Practical Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "议论文写作", "zh": "议论文写作", "en": "Argumentative Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "口语表达", "zh": "口语表达", "en": "Oral Expression", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "信息提取", "zh": "信息提取", "en": "Information Extraction", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "仿写", "zh": "仿写", "en": "Imitation Writing", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []},
      {"id": "论证表达", "zh": "论证表达", "en": "Argumentative Expression", "status": "approved", "aliases": [], "decision_rules": [], "positive_examples": [], "negative_examples": []}
    ]
  }
};

fs.writeFileSync('server/services/ai/metadata-taxonomy-v0.1.json', JSON.stringify(taxonomy, null, 2), 'utf8');
