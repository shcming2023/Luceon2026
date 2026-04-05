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

/** 排序选项 */
export type SortOption = 'newest' | 'oldest' | 'name' | 'size';

/** 视图模式 */
export type ViewMode = 'grid' | 'list';

/** 任务类型过滤 */
export type TaskFilter = 'all' | 'rawcode' | 'cleancode' | 'product';

/** Tab 过滤类型 */
export type TabFilter = 'all' | 'pending' | 'processing' | 'reviewing' | 'failed' | 'completed';

/** 权限级别 */
export type PermissionLevel = 'internal' | 'production' | 'review' | 'public';

/** 标签颜色 */
export type TagColor =
  | 'blue' | 'purple' | 'red' | 'green' | 'orange'
  | 'indigo' | 'pink' | 'yellow' | 'teal' | 'cyan' | 'lime';

// ==================== 数据模型 ====================

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
  metadata: Record<string, string>;
  uploader: string;
  previewUrl?: string;       // 本地 blob URL 或 tmpfiles 公开 URL，用于文件预览
  mineruZipUrl?: string;     // MinerU 解析后的 ZIP 文件下载链接
  // 各阶段时间戳
  uploadedAt?: number;       // 上传完成时间（Unix ms）
  mineruCompletedAt?: number; // MinerU 解析完成/失败时间（Unix ms）
  aiCompletedAt?: number;     // AI 分析完成/失败时间（Unix ms）
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
  permission: PermissionLevel;
  tags: string[];
  metadata: Record<string, string | number>;
  lineage: AssetLineageNode[];
  history: AssetHistoryEntry[];
  versions: AssetVersion[];
  relatedAssets: { id: number; title: string; type: string; status: AssetStatus }[];
}

export interface AssetLineageNode {
  stage: string;
  label: string;
  color: string;
  status: AssetStatus;
  file: string | null;
  size: string | null;
}

export interface AssetHistoryEntry {
  id: number;
  action: string;
  time: string;
  operator: string;
  type: 'system' | 'ai' | 'user';
  note?: string;
  status: AssetStatus;
}

export interface AssetVersion {
  version: string;
  status: string;
  time: string;
  operator: string;
  note: string;
}

// ==================== Store 状态 ====================

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
}

export interface AiConfig {
  apiEndpoint: string;
  apiKey: string;
  model: string;
  timeout: number;
  prompts: AiPromptConfig;
}

/**
 * MinerU API 配置
 */
export interface MinerUConfig {
  apiMode: 'precise' | 'agent';
  apiEndpoint: string;
  apiKey: string;
  timeout: number;
  modelVersion: 'pipeline' | 'vlm';
  enableOcr: boolean;
  enableFormula: boolean;
  enableTable: boolean;
  language: string;
}

/**
 * 全局应用状态
 */
export interface AppState {
  materials: Material[];
  processTasks: ProcessTask[];
  tasks: Task[];
  products: Product[];
  flexibleTags: FlexibleTag[];
  aiRules: AiRule[];
  aiRuleSettings: AiRuleSettings;
  aiConfig: AiConfig;              // 大模型 API 配置
  mineruConfig: MinerUConfig;      // MinerU API 配置
  assetDetails: Record<number, AssetDetail>;
}

// ==================== Action 类型 ====================

export type AppAction =
  // 资料操作
  | { type: 'ADD_MATERIAL'; payload: Material }
  | { type: 'DELETE_MATERIAL'; payload: number[] }
  | { type: 'BATCH_ADD_TAGS'; payload: { ids: number[]; tags: string[] } }
  | { type: 'UPDATE_MATERIAL_TAGS'; payload: { id: number; tags: string[] } }
  | { type: 'UPDATE_MATERIAL_AI_STATUS'; payload: { id: number; aiStatus: AiStatus; status?: AssetStatus; tags?: string[]; metadata?: Record<string, string>; title?: string } }
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
  // 资产详情操作
  | { type: 'UPDATE_ASSET_PERMISSION'; payload: { id: number; permission: PermissionLevel } }
  | { type: 'UPDATE_ASSET_TAGS'; payload: { id: number; tags: string[] } }
  // 删除操作
  | { type: 'DELETE_PRODUCT'; payload: number[] }
  | { type: 'DELETE_FLEXIBLE_TAG'; payload: number[] }
  | { type: 'DELETE_AI_RULE'; payload: number[] };
