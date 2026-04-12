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

  const timeoutSec = config.localTimeout || 300;
  const startAt = Date.now();

  const health = await checkLocalMinerUHealth(localEndpoint);
  if (!health.ok) {
    throw new Error(`本地 MinerU 不可用：${health.message}（请检查本地 MinerU 地址/端口与服务是否在线）`);
  }

  onProgress?.(20, `上传文件到本地解析引擎...（超时 ${timeoutSec}s）`);

  const formData = new FormData();
  formData.append('file', file);
  formData.append('materialId', String(materialId));
  formData.append('localEndpoint', localEndpoint);
  formData.append('localTimeout', String(config.localTimeout || 300));
  formData.append('backend', config.localBackend || 'hybrid-auto-engine');
  formData.append('maxPages', String(config.localMaxPages || 1000));
  formData.append('ocrLanguage', config.localOcrLanguage || config.language || 'ch');
  formData.append('language', config.language || 'ch');
  formData.append('enableOcr', String(config.enableOcr ?? false));
  formData.append('enableFormula', String(config.enableFormula ?? true));
  formData.append('enableTable', String(config.enableTable ?? true));

  let resp: Response;
  let heartbeat: ReturnType<typeof setInterval> | null = null;
  try {
    heartbeat = setInterval(() => {
      const elapsed = Date.now() - startAt;
      onProgress?.(20, `上传文件到本地解析引擎...（已等待 ${formatDuration(elapsed)} / 超时 ${timeoutSec}s）`);
    }, 10_000);

    resp = await fetch('/__proxy/upload/parse/local-mineru', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(Math.max(timeoutSec * 1000 + 5_000, 30_000)),
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    const name = (error as { name?: string } | null)?.name || '';
    const isTimeout =
      name === 'AbortError' ||
      name === 'TimeoutError' ||
      msg.includes('signal timed out') ||
      msg.toLowerCase().includes('timeout');
    if (isTimeout) {
      throw new Error(`本地 MinerU 请求超时（${timeoutSec}s）：${localEndpoint}`);
    }
    throw new Error(`本地 MinerU 请求失败：${msg}`);
  } finally {
    if (heartbeat) clearInterval(heartbeat);
  }

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
