/**
 * 全局 Mock 数据
 * 从各页面组件中集中迁移的静态数据，统一作为全局 Store 的初始状态
 */

import type {
  Material,
  ProcessTask,
  Task,
  Product,
  FlexibleTag,
  AiRule,
  AiRuleSettings,
  AssetDetail,
  AiConfig,
  AiProvider,
  MinerUConfig,
  MinioConfig,
  BatchProcessingState,
} from './types';

// ==================== 原始资料 ====================

/** 原始资料列表（初始数据） */
export const initialMaterials: Material[] = [];

// ==================== 处理任务 ====================

/** 处理中心任务列表（初始数据） */
export const initialProcessTasks: ProcessTask[] = [];

// ==================== 批处理队列 ====================

export const initialBatchProcessing: BatchProcessingState = {
  items: [],
  running: false,
  paused: false,
  uiOpen: false,
};

// ==================== 任务中心 ====================

/** 任务中心任务列表（初始数据） */
export const initialTasks: Task[] = [];

// ==================== 成品库 ====================

/** 成品列表（初始数据） */
export const initialProducts: Product[] = [];

// ==================== 标签 ====================

/** 灵活标签（初始数据） */
export const initialFlexibleTags: FlexibleTag[] = [];

// ==================== AI 规则 ====================

/** AI 自动标注规则（初始数据） */
export const initialAiRules: AiRule[] = [];

/** AI 规则执行设置（初始数据） */
export const initialAiRuleSettings: AiRuleSettings = {
  autoOnUpload: true,
  parallelExecution: true,
  requireManualReview: false,
  lowConfidenceAlert: true,
  confidenceThreshold: 85,
};

// ==================== AI 识别配置 ====================

/** AI 多提供商默认配置（按优先级排列） */
export const initialAiProviders: AiProvider[] = [
  {
    id: 'ollama',
    name: 'Ollama（默认）',
    enabled: true,
    apiEndpoint: 'http://host.docker.internal:11434/v1/chat/completions',
    apiKey: '',
    model: 'qwen3.5:9b',
    timeout: 600,
    priority: 1,
  },
  {
    id: 'moonshot',
    name: 'Moonshot',
    enabled: false,
    apiEndpoint: 'https://api.moonshot.cn/v1/chat/completions',
    apiKey: '',
    model: 'moonshot-v1-32k',
    timeout: 300,
    priority: 2,
  },
  {
    id: 'kimi',
    name: 'Kimi',
    enabled: false,
    apiEndpoint: 'https://api.moonshot.cn/v1/chat/completions',
    apiKey: '',
    model: 'moonshot-v1-32k',
    timeout: 300,
    priority: 3,
  },
  {
    id: 'openai',
    name: 'OpenAI',
    enabled: false,
    apiEndpoint: 'https://api.openai.com/v1/chat/completions',
    apiKey: '',
    model: 'gpt-4o-mini',
    timeout: 300,
    priority: 4,
  },
];

/** AI 识别配置（默认值） */
export const initialAiConfig: AiConfig = {
  providers: initialAiProviders,
  apiEndpoint: 'http://host.docker.internal:11434/v1/chat/completions',
  apiKey: '',
  model: 'qwen2.5:14b',
  timeout: 600,
  maxFileSize: 50 * 1024 * 1024, // AI 分析最大 50MB
  maxMarkdownChars: 100000, // 14B 模型建议 100k 以内，降低内存占用
  enabledFileTypes: ['.pdf', '.docx', '.doc', '.txt', '.md'],
  prompts: {
    title: '根据以下教育资料的内容，生成一个简洁准确的中文资料名称。要求：包含学科、年级、内容类型等关键信息，长度不超过30字。',
    subject: '分析以下教育资料的内容，识别其所属学科。返回值仅限：数学、语文、英语、物理、化学、生物、历史、地理、政治、科学、综合、其他。',
    grade: '根据以下教育资料的难度和知识点范围，判断其适用的年级。返回值格式：G1~G12（G1=小学一年级，G7=初中一年级，G10=高中一年级），或"通用"。',
    materialType: '判断以下教育资料的类型分类。可选值：课本、讲义、练习册、试卷、答案、教案、课件、大纲、其他。',
    tags: '从以下教育资料的 Markdown 内容中提取 3~8 个关键标签，包含学科、年级、考试类型、教材版本、知识点等维度。以逗号分隔的纯文本形式返回。',
    summary: '用 2-3 句话概括以下教育资料的核心内容和适用场景，用于在列表中快速了解资料概况。',
    language: '识别以下教育资料使用的语言。返回值仅限：中文、英文、双语、其他。',
    country: '根据以下教育资料的内容（课程体系、考试标准、教材版本等），判断其适用的国家/地区。返回值仅限：中国、英国、美国、新加坡、澳大利亚、加拿大、其他。',
  },
};

/** MinerU API 配置（默认值） */
export const initialMinerUConfig: MinerUConfig = {
  engine: 'local',
  localEndpoint: 'http://192.168.31.33:8083',
  localTimeout: 3600,
  localBackend: 'hybrid-auto-engine',
  localMaxPages: 1000,
  localOcrLanguage: 'ch',
  apiMode: 'precise',
  apiEndpoint: 'https://mineru.net/api/v4/extract/task',
  apiKey: '',  // 请在系统设置页面配置 MinerU API Key，或通过环境变量注入
  timeout: 1200,
  modelVersion: 'vlm',
  enableOcr: false,
  enableFormula: true,
  enableTable: true,
  language: 'ch',
  maxFileSize: 100 * 1024 * 1024, // MinerU 解析最大 100MB
  maxPages: 500, // 最大 500 页
};

// ==================== 资产详情 ====================

/** 资产详情（初始数据） */
export const initialAssetDetails: Record<number, AssetDetail> = {};

// ==================== MinIO 存储配置 ====================

/** MinIO 存储配置（默认值） */
export const initialMinioConfig: MinioConfig = {
  storageBackend: 'minio',
  endpoint: 'minio',
  port: 9000,
  useSSL: false,
  accessKey: 'minioadmin',
  secretKey: 'minioadmin',
  bucket: 'eduassets',
  parsedBucket: 'eduassets-parsed',
  presignedExpiry: 86400, // 24 小时有效期，适配长时间批处理场景
};
