/**
 * MinerU API 工具函数
 * 文档参考：https://mineru.net/api/pub/v4
 *
 * 支持两种文件提交模式：
 *
 * 模式 A —— URL 模式（文件必须公网可访问）
 *   submitMinerUTask(fileUrl, fileName, config)
 *   适用场景：生产环境通过 CVM 端口转发暴露 MinIO presigned URL
 *
 * 模式 B —— 预签名上传模式（推荐，适用于所有环境）
 *   submitMinerUTaskByFile(file, config)
 *   流程：
 *   1. 向 MinerU 申请 OSS 预签名 PUT URL（批量接口）
 *   2. 浏览器直接 PUT 文件到 MinerU 的阿里云 OSS
 *   3. MinerU 自动触发解析
 *   无需服务器公网可达，完美适配内网部署
 */

import type { MinerUConfig } from '../store/types';

const PROXY_BASE = '/__proxy/mineru';

export interface MinerUTaskResult {
  taskId: string;
  state: 'pending' | 'processing' | 'done' | 'failed';
  progress?: number;
  markdown?: string;       // 解析后的 Markdown 文本
  zipUrl?: string;         // 完整结果 ZIP 下载链接
  errMsg?: string;
}

// ─── 模式 A：URL 提交 ─────────────────────────────────────────

/** 提交解析任务（通过文件公开 URL）*/
export async function submitMinerUTask(
  fileUrl: string,
  fileName: string,
  config: MinerUConfig,
): Promise<string> {
  const apiKey = config.apiKey?.trim();
  if (!apiKey) throw new Error('MinerU API Key 未配置，请在系统设置中填写');

  const endpoint = `${PROXY_BASE}/api/v4/extract/task/batch`;

  const body = {
    enable_formula: config.enableFormula ?? true,
    enable_table: config.enableTable ?? true,
    language: config.language || 'ch',
    is_ocr: config.enableOcr ?? false,
    model_version: config.modelVersion || 'pipeline',
    files: [
      {
        url: fileUrl,
        data_id: `cms-${Date.now()}`,
      },
    ],
  };

  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`MinerU 提交失败: HTTP ${res.status} — ${text}`);
  }

  const json = await res.json();
  if (json.code !== 0) {
    throw new Error(`MinerU 提交失败: ${json.msg || JSON.stringify(json)}`);
  }

  const batchId: string = json.data?.batch_id;
  if (!batchId) throw new Error('MinerU 未返回 batch_id');
  return batchId;
}

// ─── 模式 B：预签名上传（推荐）────────────────────────────────

/**
 * 提交解析任务（预签名上传模式）
 *
 * 步骤：
 * 1. POST /api/v4/file-urls/batch  → 获取 batch_id + OSS 预签名 PUT URL
 * 2. 浏览器直接 PUT 文件到 OSS URL（不经过本项目服务器）
 *
 * @returns batch_id（与 queryMinerUTask 兼容）
 */
export async function submitMinerUTaskByFile(
  file: File,
  config: MinerUConfig,
  onProgress?: (pct: number, msg: string) => void,
): Promise<string> {
  const apiKey = config.apiKey?.trim();
  if (!apiKey) throw new Error('MinerU API Key 未配置，请在系统设置中填写');

  // 第一步：申请预签名上传地址
  onProgress?.(5, '申请上传凭证...');

  const applyEndpoint = `${PROXY_BASE}/api/v4/file-urls/batch`;
  const applyBody = {
    enable_formula: config.enableFormula ?? true,
    enable_table: config.enableTable ?? true,
    language: config.language || 'ch',
    is_ocr: config.enableOcr ?? false,
    model_version: config.modelVersion || 'pipeline',
    files: [
      {
        name: file.name,
        data_id: `cms-${Date.now()}`,
      },
    ],
  };

  const applyRes = await fetch(applyEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(applyBody),
  });

  if (!applyRes.ok) {
    const text = await applyRes.text();
    throw new Error(`MinerU 申请上传凭证失败: HTTP ${applyRes.status} — ${text}`);
  }

  const applyJson = await applyRes.json();
  if (applyJson.code !== 0) {
    throw new Error(`MinerU 申请上传凭证失败: ${applyJson.msg || JSON.stringify(applyJson)}`);
  }

  const batchId: string = applyJson.data?.batch_id;
  const fileUrls: string[] = applyJson.data?.file_urls ?? [];

  if (!batchId) throw new Error('MinerU 未返回 batch_id');
  if (fileUrls.length === 0) throw new Error('MinerU 未返回 OSS 上传地址');

  // 第二步：直接 PUT 文件到 MinerU 的 OSS（不经过本项目服务器）
  onProgress?.(15, '上传文件到解析服务...');

  const ossUrl = fileUrls[0];
  const putRes = await fetch(ossUrl, {
    method: 'PUT',
    body: file,
    headers: {
      'Content-Type': file.type || 'application/octet-stream',
    },
  });

  if (!putRes.ok) {
    throw new Error(`文件上传到解析服务失败: HTTP ${putRes.status}`);
  }

  onProgress?.(25, '文件已提交，等待解析...');
  return batchId;
}

// ─── 查询任务状态（两种模式通用）────────────────────────────────

/** 查询批量任务状态
 *  官方文档：GET /api/v4/extract-results/batch/{batch_id}
 */
export async function queryMinerUTask(
  batchId: string,
  config: MinerUConfig,
): Promise<MinerUTaskResult> {
  const apiKey = config.apiKey?.trim();
  if (!apiKey) throw new Error('MinerU API Key 未配置');

  const endpoint = `${PROXY_BASE}/api/v4/extract-results/batch/${batchId}`;

  const res = await fetch(endpoint, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`MinerU 查询失败: HTTP ${res.status} — ${text}`);
  }

  const json = await res.json();
  if (json.code !== 0) {
    throw new Error(`MinerU 查询失败: ${json.msg || JSON.stringify(json)}`);
  }

  const fileResult = json.data?.extract_result?.[0];
  if (!fileResult) {
    return { taskId: batchId, state: 'processing', progress: 0 };
  }

  const state: MinerUTaskResult['state'] =
    fileResult.state === 'done' ? 'done'
    : fileResult.state === 'failed' ? 'failed'
    : fileResult.state === 'running' ? 'processing'
    : 'pending';

  return {
    taskId: batchId,
    state,
    progress: fileResult.progress ?? 0,
    zipUrl: fileResult.full_zip_url || fileResult.zip_url || undefined,
    errMsg: fileResult.err_msg || undefined,
  };
}

// ─── 从 MinIO 或 CDN 获取 Markdown 内容 ──────────────────────

/**
 * 从 presigned URL 或 CDN URL 获取 Markdown 文本内容
 * - 优先使用 presigned URL（upload-server 经由后端存储的 full.md）
 * - 兜底使用 CDN URL 代理（zipUrl 模式）
 */
export async function fetchMinerUMarkdown(
  markdownUrl: string | undefined,
  zipUrl?: string,
): Promise<string> {
  // 优先：使用 presigned markdown URL（从 MinIO 存储获取）
  if (markdownUrl) {
    try {
      const res = await fetch(markdownUrl);
      if (res.ok) return await res.text();
    } catch {
      // fallback
    }
  }

  // 兜底：从 ZIP URL 推导 CDN markdown 路径
  if (zipUrl) {
    const mdUrl = zipUrl.replace(/\.zip$/, '/auto/full.md');
    const proxyMdUrl = mdUrl.replace(
      /^https?:\/\/cdn-mineru\.openxlab\.org\.cn/,
      '/__proxy/mineru-cdn',
    );
    try {
      const res = await fetch(proxyMdUrl);
      if (res.ok) return await res.text();
    } catch {
      // ignore
    }
  }

  return '';
}

// ─── 主流水线（模式 B，推荐）────────────────────────────────────

/**
 * 完整解析流水线（预签名上传模式）
 *
 * @param file         原始 File 对象
 * @param config       MinerU 配置
 * @param onProgress   进度回调 (0-100, msg)
 */
export async function runMinerUPipeline(
  file: File,
  config: MinerUConfig,
  onProgress?: (progress: number, state: string) => void,
): Promise<MinerUTaskResult>;

/**
 * 完整解析流水线（URL 模式，兼容旧接口）
 *
 * @param fileUrl      文件公开访问 URL（需公网可达）
 * @param fileName     文件名
 * @param config       MinerU 配置
 * @param onProgress   进度回调 (0-100, msg)
 * @deprecated 推荐使用 File 对象重载，不依赖公网可达性
 */
export async function runMinerUPipeline(
  fileUrl: string,
  fileName: string,
  config: MinerUConfig,
  onProgress?: (progress: number, state: string) => void,
): Promise<MinerUTaskResult>;

export async function runMinerUPipeline(
  fileOrUrl: File | string,
  fileNameOrConfig: string | MinerUConfig,
  configOrProgress?: MinerUConfig | ((progress: number, state: string) => void),
  onProgressArg?: (progress: number, state: string) => void,
): Promise<MinerUTaskResult> {
  let batchId: string;

  // 判断调用模式
  if (fileOrUrl instanceof File) {
    // 模式 B：File 对象
    const cb = configOrProgress as ((p: number, s: string) => void) | undefined;
    cb?.(0, '准备上传...');
    batchId = await submitMinerUTaskByFile(
      fileOrUrl,
      fileNameOrConfig as MinerUConfig,
      cb,
    );
  } else {
    // 模式 A：URL 字符串（兼容旧调用）
    const cb = onProgressArg;
    cb?.(0, '提交解析任务...');
    batchId = await submitMinerUTask(
      fileOrUrl,
      fileNameOrConfig as string,
      configOrProgress as MinerUConfig,
    );
  }

  // 轮询状态（两种模式相同）
  const config = (fileOrUrl instanceof File ? fileNameOrConfig : configOrProgress) as MinerUConfig;
  const onProgress = fileOrUrl instanceof File
    ? (configOrProgress as ((p: number, s: string) => void) | undefined)
    : onProgressArg;

  const maxAttempts = Math.ceil((config.timeout || 1200) / 5);
  let attempt = 0;

  while (attempt < maxAttempts) {
    await new Promise((r) => setTimeout(r, 5000));
    attempt++;

    const result = await queryMinerUTask(batchId, config);
    const pct = Math.min(Math.round(25 + (attempt / maxAttempts) * 70), 95);
    onProgress?.(
      result.progress ? Math.max(pct, Math.round(25 + result.progress * 0.7)) : pct,
      `解析中... (${attempt}/${maxAttempts})`,
    );

    if (result.state === 'done') {
      onProgress?.(100, '解析完成');
      return result;
    }

    if (result.state === 'failed') {
      throw new Error(`MinerU 解析失败: ${result.errMsg || '未知错误'}`);
    }
  }

  throw new Error(`MinerU 解析超时（已等待 ${config.timeout || 1200} 秒）`);
}
