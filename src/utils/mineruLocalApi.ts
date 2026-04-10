import type { MinerUConfig } from '../store/types';

export interface LocalMinerUTaskResult {
  taskId: string;
  state: 'done' | 'failed';
  markdown?: string;
  markdownObjectName?: string;
  markdownUrl?: string;
  parsedFilesCount?: number;
  errMsg?: string;
}

function normalizeEndpoint(endpoint: string) {
  return endpoint.trim().replace(/\/+$/, '');
}

export async function checkLocalMinerUHealth(endpoint: string) {
  const localEndpoint = normalizeEndpoint(endpoint);
  if (!localEndpoint) {
    return { ok: false, message: '未配置本地 MinerU 地址' };
  }

  try {
    const resp = await fetch('/__proxy/upload/parse/local-mineru/health', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ localEndpoint }),
      signal: AbortSignal.timeout(5000),
    });
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      throw new Error(data?.error || `HTTP ${resp.status}`);
    }
    return {
      ok: Boolean(data?.ok),
      message: String(data?.message || (data?.ok ? '本地 MinerU 可用' : '本地 MinerU 不可用')),
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function submitLocalMinerUTask(
  file: File,
  materialId: number | string,
  config: MinerUConfig,
  onProgress?: (pct: number, msg: string) => void,
): Promise<LocalMinerUTaskResult> {
  const localEndpoint = normalizeEndpoint(config.localEndpoint || '');
  if (!localEndpoint) {
    throw new Error('未配置本地 MinerU 地址');
  }

  onProgress?.(20, '上传文件到本地解析引擎...');

  const formData = new FormData();
  formData.append('file', file);
  formData.append('materialId', String(materialId));
  formData.append('localEndpoint', localEndpoint);
  formData.append('localTimeout', String(config.localTimeout || 300));
  formData.append('language', config.language || 'ch');
  formData.append('enableOcr', String(config.enableOcr ?? false));
  formData.append('enableFormula', String(config.enableFormula ?? true));
  formData.append('enableTable', String(config.enableTable ?? true));

  const resp = await fetch('/__proxy/upload/parse/local-mineru', {
    method: 'POST',
    body: formData,
    signal: AbortSignal.timeout(Math.max((config.localTimeout || 300) * 1000, 30_000)),
  });

  const data = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new Error(data?.error || `本地 MinerU 调用失败: HTTP ${resp.status}`);
  }
  if (!data?.markdown) {
    throw new Error('本地 MinerU 未返回 Markdown 内容');
  }

  onProgress?.(100, '本地解析完成');

  return {
    taskId: String(data.taskId || `local-${Date.now()}`),
    state: data.state === 'done' ? 'done' : 'failed',
    markdown: String(data.markdown),
    markdownObjectName: data.markdownObjectName ? String(data.markdownObjectName) : undefined,
    markdownUrl: data.markdownUrl ? String(data.markdownUrl) : undefined,
    parsedFilesCount: typeof data.parsedFilesCount === 'number' ? data.parsedFilesCount : undefined,
    errMsg: data.error ? String(data.error) : undefined,
  };
}
