/**
 * 全局类型定义文件
 * 统一管理所有数据模型类型，替换各页面内的内联类型定义
 */

// ==================== 基础类型 ====================

/** 资产/任务处理状态 */
export type AssetStatus = 'processing' | 'completed' | 'pending' | 'failed' | 'reviewing';

/** AI 分析状态 */
export type AiStatus = 'analyzed' | 'analyzing' | 'pending' | 'failed';

/** MinerU 解析状态 */
export type MinerUStatus = 'pending' | 'processing' | 'completed' | 'failed';

/** 批处理队列条目状态 */
export type BatchItemStatus =
  | 'pending'
  | 'uploading'
  | 'task-created'
  | 'tracking'
  | 'review-pending'
  | 'completed'
  | 'failed'
  | 'canceled'
  | 'error'
  | 'skipped'
  | 'uploaded'
  | 'mineru'
  | 'ai';
/** 排序选项 */
export type SortOption = 'newest' | 'oldest' | 'name' | 'size';

/** 视图模式 */
export type ViewMode = 'grid' | 'list';

/** 任务类型过滤 */
export type TaskFilter = 'all' | 'rawcode' | 'cleancode' | 'product';

/** Tab 过滤类型 */
export type TabFilter = 'all' | 'pending' | 'processing' | 'reviewing' | 'failed' | 'completed';

/** 标签颜色 */
export type TagColor =
  | 'blue' | 'purple' | 'red' | 'green' | 'orange'
  | 'indigo' | 'pink' | 'yellow' | 'teal' | 'cyan' | 'lime';

// ==================== 数据模型 ====================

/** Material 元数据（各阶段逐步写入，所有字段均为可选） */
export interface MaterialMetadata {
  // ── 上传阶段写入 ──
  fileUrl?: string;                // tmpfiles 公开访问 URL
  objectName?: string;             // MinIO 对象存储路径（如 "originals/1234/file.pdf"）
  fileName?: string;               // 原始文件名
  provider?: 'minio' | 'tmpfiles'; // 存储后端
  mimeType?: string;               // MIME 类型
  format?: string;                 // 文件格式（PDF / DOCX 等）
  pages?: string;                  // 页数

  // ── MinerU 解析阶段写入 ──
  markdownObjectName?: string;     // 解析产物 full.md 的 MinIO 路径
  markdownUrl?: string;            // full.md 的 presigned URL
  parsedFilesCount?: string;       // 解析产物文件数量
  parsedAt?: string;               // 解析完成时间（ISO 8601）

  // ── AI 分析阶段写入 ──
  subject?: string;                // 学科
  grade?: string;                  // 年级
  language?: string;               // 语言
  country?: string;                // 国家/地区
  type?: string;                   // 资料类型
  summary?: string;                // 内容摘要
  standard?: string;               // 课标/标准
  region?: string;                 // 地区
  aiConfidence?: string;           // AI 识别置信度（百分比）
  aiAnalyzedAt?: string;           // AI 分析完成时间（ISO 8601）

  // 扩展字段（兼容未来新增）
  [key: string]: any;
}

/**
 * 原始资料
 */
export interface Material {
  id: number;
  title: string;
  type: string;
  size: string;
  sizeBytes: number; // 用于排序
  uploadTime: string;
  uploadTimestamp: number; // 用于排序（Unix ms）
  status: AssetStatus;
  mineruStatus: MinerUStatus; // MinerU 解析状态
  aiStatus: AiStatus;         // AI 分析状态（基于 MinerU 输出的 Markdown）
  tags: string[];
  metadata: MaterialMetadata;
  uploader: string;
  previewUrl?: string;       // 本地 blob URL 或 tmpfiles 公开 URL，用于文件预览
  mineruZipUrl?: string;     // MinerU 解析后的 ZIP 文件下载链接
  // 各阶段时间戳
  uploadedAt?: number;       // 上传完成时间（Unix ms）
  mineruCompletedAt?: number; // MinerU 解析完成/失败时间（Unix ms）
  aiCompletedAt?: number;     // AI 分析完成/失败时间（Unix ms）
}

/**
 * 批处理队列条目（仅存储可序列化字段；文件二进制由运行时内存维护）
 */
export interface BatchQueueItem {
  id: string;
  fileName: string;
  fileSize: number;
  path: string;
  status: BatchItemStatus;
  progress: number;
  message?: string;
  materialId?: number;
  taskId?: string;
  objectName?: string;
  taskState?: string;
  taskStage?: string;
  mineruStartedAt?: number;
  createdAt: number;
  updatedAt: number;
}

/**
 * 批处理全局状态（用于跨页面查看进度与控制）
 */
export interface BatchProcessingState {
  items: BatchQueueItem[];
  running: boolean;
  paused: boolean;
  uiOpen: boolean;
}

/**
 * 处理中心任务
 */
export interface ProcessTask {
  id: number;
  name: string;
  type: 'rawcode生成' | 'cleancode生成' | '成品生成';
  status: AssetStatus;
  stage: string;
  progress: number;
  input: string;
  output: string;
  assignee: string;
  startTime: string;
  estimatedTime: string;
  logs: LogEntry[];
  error?: string;
  reviewNote?: string;
  diffStats?: { added: number; removed: number; changed: number };
  materialId?: number; // 关联的原始资料ID
}

/**
 * 任务中心任务
 */
export interface Task {
  id: string;
  name: string;
  type: string;
  status: AssetStatus;
  priority: 'high' | 'medium' | 'low';
  createdAt: string;
  updatedAt: string;
  assignee: string;
  input: string;
  output: string;
  progress: number;
  logs: LogEntry[];
  error?: string;
  reviewNote?: string;
}

/**
 * 日志条目
 */
export interface LogEntry {
  time: string;
  level: 'info' | 'success' | 'warning' | 'error';
  msg: string;
}

/**
 * 成品
 */
export interface Product {
  id: number;
  title: string;
  type: string;
  description: string;
  items: string;
  useCount: number;
  rating: number;
  status: AssetStatus;
  tags: string[];
  metadata: {
    subject: string;
    grade: string;
    difficulty: string;
    standard: string;
  };
  source: string;
  createdAt: string;
  lineage: string[];
  color: string;
}

/**
 * 灵活标签
 */
export interface FlexibleTag {
  id: number;
  name: string;
  count: number;
  category: string;
  color: TagColor;
}

/**
 * AI 自动标注规则
 */
export interface AiRule {
  id: number;
  name: string;
  enabled: boolean;
  condition: string;
  action: string;
  priority: number;
  executedCount: number;
  successRate: number;
}

/**
 * AI 规则执行设置
 */
export interface AiRuleSettings {
  autoOnUpload: boolean;
  parallelExecution: boolean;
  requireManualReview: boolean;
  lowConfidenceAlert: boolean;
  confidenceThreshold: number;
}

/**
 * 资产详情（用于 AssetDetailPage 的完整数据）
 */
export interface AssetDetail {
  id: number;
  title: string;
  status: AssetStatus;
  aiStatus?: AiStatus;           // AI 分析状态（从 Material 动态构建时映射）
  assetId: string;
  tags: string[];
  metadata: Record<string, string | number>;
  relatedAssets: { id: number; title: string; type: string; status: AssetStatus }[];
}

// ==================== Store 状态 ====================

/**
 * 单个 AI 提供商配置（多策略容错用）
 */
export interface AiProvider {
  id: string;           // 唯一标识，如 'moonshot' | 'kimi' | 'openai' | 'ollama'
  name: string;         // 显示名称
  enabled: boolean;     // 是否启用此提供商
  apiEndpoint: string;  // API 端点（Ollama 为本地地址）
  apiKey: string;       // API Key（Ollama 可为空串）
  model: string;        // 模型名称
  timeout: number;      // 单次请求超时秒数
  priority: number;     // 数值越小优先级越高（1 最优先）
}

/**
 * AI 识别配置（API + 提示词）
 */
export interface AiPromptConfig {
  title: string;           // 资料名称识别提示词
  subject: string;         // 学科识别提示词
  grade: string;           // 年级识别提示词
  materialType: string;    // 资料类型提示词
  tags: string;            // 标签提取提示词
  summary: string;         // 摘要生成提示词
  language: string;        // 语言识别提示词
  country: string;         // 国家/地区识别提示词
}

/**
 * MinIO 存储配置
 */
export interface MinioConfig {
  [key: string]: unknown;  // 允许作为 Record<string, unknown> 使用
  storageBackend: 'minio' | 'tmpfiles'; // 存储后端
  endpoint: string;        // MinIO 端点（IP 或域名）
  port: number;            // MinIO API 端口
  useSSL: boolean;         // 是否使用 SSL
  accessKey: string;       // Access Key
  secretKey: string;       // Secret Key
  bucket: string;          // 原始资料 Bucket 名称
  parsedBucket: string;    // MinerU 解析产物 Bucket 名称
  presignedExpiry: number; // 预签名 URL 有效期（秒）
}

export interface AiConfig {
  [key: string]: unknown;  // 允许作为 Record<string, unknown> 使用
  // === 多提供商（新增）===
  providers?: AiProvider[];  // 多个 AI 提供商，按 priority 顺序尝试
  // === 旧单提供商字段（保留向后兼容）===
  apiEndpoint: string;
  apiKey: string;
  model: string;
  timeout: number;
  prompts: AiPromptConfig;
  maxFileSize: number; // AI 分析最大文件大小（字节），默认 50MB
  maxMarkdownChars?: number; // AI 分析输入 Markdown 最大字符数（建议 <= 200k）
  enabledFileTypes: string[]; // 支持的文件类型
  enableThinking?: boolean; // 是否启用 AI 深度思考模式（Qwen3 thinking mode），默认 false
}

/**
 * MinerU API 配置
 */
export interface MinerUConfig {
  [key: string]: unknown;  // 允许作为 Record<string, unknown> 使用
  engine: 'local' | 'cloud';
  localEndpoint: string;
  localTimeout: number;
  localBackend: string;
  localMaxPages: number;
  localOcrLanguage: string;
  apiMode: 'precise' | 'agent';
  apiEndpoint: string;
  apiKey: string;
  timeout: number;
  modelVersion: 'pipeline' | 'vlm';
  enableOcr: boolean;
  enableFormula: boolean;
  enableTable: boolean;
  language: string;
  maxFileSize: number; // MinerU 解析最大文件大小（字节），默认 100MB
  maxPages: number; // 最大页数限制，默认 500 页
}

/**
 * 全局应用状态
 */
export interface AppState {
  materials: Material[];
  processTasks: ProcessTask[];
  tasks: Task[];
  products: Product[];
  batchProcessing: BatchProcessingState;
  flexibleTags: FlexibleTag[];
  aiRules: AiRule[];
  aiRuleSettings: AiRuleSettings;
  aiConfig: AiConfig;              // 大模型 API 配置
  mineruConfig: MinerUConfig;      // MinerU API 配置
  minioConfig: MinioConfig;        // MinIO 存储配置
  assetDetails: Record<number, AssetDetail>;
  _dataSource?: 'localStorage' | 'db-server' | 'initial';
}

// ==================== Action 类型 ====================

export type AppAction =
  // 数据库 hydration（启动时从 db-server 加载）
  | { type: 'HYDRATE_FROM_DB'; payload: Partial<AppState> }
  // 批处理队列
  | { type: 'BATCH_ADD_FILES'; payload: { items: Array<Pick<BatchQueueItem, 'id' | 'fileName' | 'fileSize' | 'path'>>; openUi?: boolean } }
  | { type: 'BATCH_UPDATE_ITEM'; payload: { id: string; updates: Partial<BatchQueueItem> } }
  | { type: 'BATCH_REMOVE_ITEM'; payload: { id: string } }
  | { type: 'BATCH_CLEAR' }
  | { type: 'BATCH_SET_RUNNING'; payload: { running: boolean } }
  | { type: 'BATCH_SET_PAUSED'; payload: { paused: boolean } }
  | { type: 'BATCH_SET_UI_OPEN'; payload: { uiOpen: boolean } }
  | { type: 'BATCH_SET_OPTIONS'; payload: Partial<BatchProcessingState> }
  // 资料操作
  | { type: 'ADD_MATERIAL'; payload: Material }
  | { type: 'DELETE_MATERIAL'; payload: number[] }
  | { type: 'UPDATE_MATERIAL'; payload: { id: number; updates: Partial<Material> } }
  | { type: 'BATCH_ADD_TAGS'; payload: { ids: number[]; tags: string[] } }
  | { type: 'UPDATE_MATERIAL_TAGS'; payload: { id: number; tags: string[] } }
  | { type: 'UPDATE_MATERIAL_AI_STATUS'; payload: { id: number; aiStatus: AiStatus; status?: AssetStatus; tags?: string[]; metadata?: MaterialMetadata; title?: string } }
  | { type: 'UPDATE_MATERIAL_MINERU_STATUS'; payload: { id: number; mineruStatus: MinerUStatus; aiStatus?: AiStatus; status?: AssetStatus; mineruCompletedAt?: number } }
  | { type: 'UPDATE_MATERIAL_PREVIEW_URL'; payload: { id: number; previewUrl: string } }
  | { type: 'UPDATE_MATERIAL_MINERU_ZIP_URL'; payload: { id: number; mineruZipUrl: string } }
  // 处理任务操作
  | { type: 'ADD_PROCESS_TASK'; payload: ProcessTask }
  | { type: 'UPDATE_PROCESS_TASK'; payload: { id: number; status: AssetStatus; materialId?: number } }
  | { type: 'UPDATE_PROCESS_TASK_STATUS'; payload: { id: number; status: AssetStatus } }
  // 任务中心操作
  | { type: 'UPDATE_TASK_STATUS'; payload: { id: string; status: AssetStatus } }
  // AI 规则操作
  | { type: 'TOGGLE_AI_RULE'; payload: { id: number } }
  | { type: 'UPDATE_AI_RULE_SETTINGS'; payload: Partial<AiRuleSettings> }
  | { type: 'UPDATE_AI_CONFIG'; payload: Partial<AiConfig> }
  | { type: 'UPDATE_MINERU_CONFIG'; payload: Partial<MinerUConfig> }
  | { type: 'UPDATE_MINIO_CONFIG'; payload: Partial<MinioConfig> }
  // 资产详情操作
  | { type: 'UPDATE_ASSET_TAGS'; payload: { id: number; tags: string[] } }
  // 删除操作
  | { type: 'ADD_PRODUCT'; payload: Product }
  | { type: 'DELETE_PRODUCT'; payload: number[] }
  | { type: 'DELETE_FLEXIBLE_TAG'; payload: number[] }
  | { type: 'DELETE_AI_RULE'; payload: number[] }
  | { type: 'SET_MATERIALS'; payload: Material[] };
