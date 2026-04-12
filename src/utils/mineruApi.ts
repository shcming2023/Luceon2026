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
import { submitLocalMinerUTask } from './mineruLocalApi';

const PROXY_BASE = '/__proxy/mineru';

export interface MinerUTaskResult {
  taskId: string;
  state: 'pending' | 'processing' | 'done' | 'failed';
  progress?: number;
  /** 已解析页数（MinerU extract_progress.extracted_pages） */
  extractedPages?: number;
  /** 总页数（MinerU extract_progress.total_pages） */
  totalPages?: number;
  markdown?: string;       // 解析后的 Markdown 文本
  zipUrl?: string;         // 完整结果 ZIP 下载链接
  markdownObjectName?: string;
  markdownUrl?: string;
  parsedFilesCount?: number;
  errMsg?: string;
}

function formatDuration(ms: number) {
  if (!Number.isFinite(ms) || ms <= 0) return '0s';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  if (min < 60) return `${min}m ${rem}s`;
  const hr = Math.floor(min / 60);
  const minRem = min % 60;
  return `${hr}h ${minRem}m`;
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

  // 第二步：通过后端代理 PUT 文件到 MinerU 的 OSS（避免浏览器直连 OSS 跨域问题）
  onProgress?.(15, '上传文件到解析服务...');

  const ossUrl = fileUrls[0];

  // 通过本项目的 upload-server 代理 PUT，服务端可直接访问阿里云 OSS
  const formData = new FormData();
  formData.append('file', file);
  formData.append('ossUrl', ossUrl);

  const putRes = await fetch('/__proxy/upload/parse/oss-put', {
    method: 'POST',
    body: formData,
    signal: AbortSignal.timeout(120_000), // 2分钟上传超时（#8）
  });

  if (!putRes.ok) {
    const errText = await putRes.text();
    throw new Error(`文件上传到解析服务失败: HTTP ${putRes.status} — ${errText.slice(0, 200)}`);
  }

  onProgress?.(25, `文件已提交，等待解析...（任务ID: ${batchId}）`);
  return batchId;
}

// ─── 查询任务状态（两种模式通用）────────────────────────────────

/** 查询批量任务状态
 *  官方文档：GET /api/v4/extract-results/batch/{batch_id}
 *  网络临时故障时自动重试 2 次（DNS EAI_AGAIN / 502 等）
 */
export async function queryMinerUTask(
  batchId: string,
  config: MinerUConfig,
): Promise<MinerUTaskResult> {
  const apiKey = config.apiKey?.trim();
  if (!apiKey) throw new Error('MinerU API Key 未配置');

  const endpoint = `${PROXY_BASE}/api/v4/extract-results/batch/${batchId}`;

  // 网络临时故障重试（DNS 解析失败、502、超时等），最多 3 次，间隔 3s
  let lastErr: unknown;
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(endpoint, {
        headers: { Authorization: `Bearer ${apiKey}` },
        signal: AbortSignal.timeout(15_000),
      });

      if (!res.ok) {
        const text = await res.text();
        // 5xx 可重试，4xx 直接抛出
        if (res.status >= 500 && i < 2) {
          lastErr = new Error(`MinerU 查询失败: HTTP ${res.status} — ${text}`);
          await new Promise((r) => setTimeout(r, 3000));
          continue;
        }
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
        : (fileResult.state === 'running' || fileResult.state === 'processing') ? 'processing'
        : 'pending';

      const extractedPages: number | undefined = fileResult.extract_progress?.extracted_pages;
      const totalPages: number | undefined = fileResult.extract_progress?.total_pages;

      // 用真实页数比例作为进度（0-100）
      const realProgress = (extractedPages != null && totalPages != null && totalPages > 0)
        ? Math.round((extractedPages / totalPages) * 100)
        : (fileResult.progress ?? 0);

      return {
        taskId: batchId,
        state,
        progress: realProgress,
        extractedPages,
        totalPages,
        zipUrl: fileResult.full_zip_url || fileResult.zip_url || undefined,
        errMsg: fileResult.err_msg || undefined,
      };
    } catch (err) {
      lastErr = err;
      const msg = err instanceof Error ? err.message : String(err);
      const cause = err instanceof Error && typeof (err as Error & { cause?: unknown }).cause === 'object'
        ? (err as Error & { cause?: { code?: string } }).cause
        : undefined;
      const causeCode = cause?.code ?? '';
      const isNetworkErr = err instanceof Error && err.name === 'TimeoutError'
        || ['EAI_AGAIN', 'ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'ECONNREFUSED'].includes(causeCode)
        || msg.includes('EAI_AGAIN') || msg.includes('ECONNRESET') || msg.includes('ETIMEDOUT');
      if (isNetworkErr && i < 2) {
        console.warn(`[MinerU] queryMinerUTask 网络错误 (${i + 1}/3):`, msg);
        await new Promise((r) => setTimeout(r, 3000));
        continue;
      }
      throw err;
    }
  }

  throw lastErr instanceof Error ? lastErr : new Error('MinerU 查询多次失败');
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
  materialId?: number | string,
  onTaskId?: (taskId: string) => void,
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
  materialId?: number | string,
  onTaskId?: (taskId: string) => void,
): Promise<MinerUTaskResult>;

export async function runMinerUPipeline(
  fileOrUrl: File | string,
  fileNameOrConfig: string | MinerUConfig,
  configOrProgress?: MinerUConfig | ((progress: number, state: string) => void),
  onProgressArg?: ((progress: number, state: string) => void) | number | string,
  materialIdArg?: number | string | ((taskId: string) => void),
  onTaskIdArg?: (taskId: string) => void,
): Promise<MinerUTaskResult> {
  const isFileMode = fileOrUrl instanceof File;
  const config = (isFileMode ? fileNameOrConfig : configOrProgress) as MinerUConfig;
  const onProgress = (isFileMode
    ? configOrProgress
    : onProgressArg) as ((p: number, s: string) => void) | undefined;
  const materialId = (isFileMode ? onProgressArg : materialIdArg) as number | string | undefined;
  const onTaskId = (isFileMode ? materialIdArg : onTaskIdArg) as ((taskId: string) => void) | undefined;

  if (config.engine === 'local') {
    const file = isFileMode
      ? fileOrUrl as File
      : await (async () => {
          onProgress?.(5, '从远程地址下载文件...');
          const response = await fetch(fileOrUrl as string, {
            signal: AbortSignal.timeout(Math.max((config.localTimeout || 300) * 1000, 30_000)),
          });
          if (!response.ok) {
            throw new Error(`下载文件失败: HTTP ${response.status}`);
          }
          const blob = await response.blob();
          return new File(
            [blob],
            fileNameOrConfig as string,
            { type: blob.type || 'application/octet-stream' },
          );
        })();

    const localResult = await submitLocalMinerUTask(file, materialId || Date.now(), config, onProgress);
    if (localResult.state === 'failed') {
      throw new Error(localResult.errMsg || '本地 MinerU 解析失败');
    }
    onTaskId?.(localResult.taskId);
    return localResult;
  }

  const MAX_RETRIES = 3;
  const RETRY_DELAYS = [10_000, 30_000, 60_000];

  let lastError: Error = new Error('MinerU 解析失败');

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    const retryLabel = MAX_RETRIES > 1 ? ` (第 ${attempt}/${MAX_RETRIES} 次)` : '';
    const startedAt = Date.now();
    let lastProgressAt = startedAt;
    let lastExtractedPages: number | null = null;

    try {
      let batchId: string;

      if (isFileMode) {
        onProgress?.(0, `准备上传...${retryLabel}`);
        batchId = await submitMinerUTaskByFile(
          fileOrUrl as File,
          fileNameOrConfig as MinerUConfig,
          onProgress,
        );
      } else {
        onProgress?.(0, `提交解析任务...${retryLabel}`);
        batchId = await submitMinerUTask(
          fileOrUrl as string,
          fileNameOrConfig as string,
          configOrProgress as MinerUConfig,
        );
      }

      onTaskId?.(batchId);

      // 轮询状态
      const maxAttempts = Math.ceil((config.timeout || 1200) / 5);
      let pollAttempt = 0;
      const stallRetryMs = 120_000;

      while (pollAttempt < maxAttempts) {
        await new Promise((r) => setTimeout(r, 5000));
        pollAttempt++;

        const result = await queryMinerUTask(batchId, config);

        // 优先用真实页数进度，次用轮询估算
        let displayPct: number;
        if (result.extractedPages != null && result.totalPages != null && result.totalPages > 0) {
          // 真实进度：25%(上传完成) ~ 95%
          displayPct = Math.round(25 + (result.extractedPages / result.totalPages) * 70);
        } else {
          displayPct = Math.min(Math.round(25 + (pollAttempt / maxAttempts) * 70), 95);
        }

        const now = Date.now();
        const elapsed = now - startedAt;
        const pageInfo = (result.extractedPages != null && result.totalPages != null)
          ? ` (${result.extractedPages}/${result.totalPages} 页)`
          : ` (${pollAttempt}/${maxAttempts})`;
        const waitInfo = `（已等待 ${formatDuration(elapsed)} / 超时 ${config.timeout || 1200}s）`;

        if (result.extractedPages != null && result.totalPages != null) {
          if (result.extractedPages !== lastExtractedPages) {
            lastExtractedPages = result.extractedPages;
            lastProgressAt = now;
          }
          if (result.state !== 'done' && result.totalPages > 0 && result.extractedPages >= result.totalPages) {
            displayPct = Math.max(displayPct, 97);
            onProgress?.(displayPct, `解析完成，等待结果生成...${retryLabel}${pageInfo}${waitInfo}`);
          } else {
            onProgress?.(displayPct, `解析中${retryLabel}${pageInfo}${waitInfo}`);
          }
        } else {
          onProgress?.(displayPct, `解析中${retryLabel}${pageInfo}${waitInfo}`);
        }

        if (result.state === 'done') {
          onProgress?.(100, '解析完成');
          return result;
        }

        if (result.state === 'failed') {
          throw new Error(`MinerU 解析失败: ${result.errMsg || '未知错误'}`);
        }

        if (Date.now() - lastProgressAt > stallRetryMs) {
          throw new Error('MinerU 长时间无进度，自动重试');
        }
      }

      throw new Error(`MinerU 解析超时（已等待 ${config.timeout || 1200} 秒）`);

    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));

      // 超时不重试（服务端已超时，重试无意义）
      if (lastError.message.includes('超时') || attempt >= MAX_RETRIES) {
        break;
      }

      const waitSec = RETRY_DELAYS[attempt - 1] / 1000;
      console.warn(`[MinerU] 第 ${attempt} 次失败，${waitSec}s 后重试:`, lastError.message);
      onProgress?.(0, `解析失败，${waitSec}s 后自动重试 (${attempt}/${MAX_RETRIES})...`);
      await new Promise((r) => setTimeout(r, RETRY_DELAYS[attempt - 1]));
    }
  }

  throw lastError;
}
