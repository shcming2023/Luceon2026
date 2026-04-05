/**
 * Overleaf 备份系统 API 工具
 * 自动携带 x-access-token 请求头
 */

const STORAGE_KEY = "overleaf_access_token";
const API_BASE = "/api";

export function getBackupToken(): string {
  return localStorage.getItem(STORAGE_KEY) ?? "";
}

export function setBackupToken(token: string): void {
  localStorage.setItem(STORAGE_KEY, token);
}

function buildHeaders(extra?: HeadersInit): Record<string, string> {
  const token = getBackupToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers["x-access-token"] = token;
  }
  if (extra) {
    Object.assign(headers, extra);
  }
  return headers;
}

export async function backupFetch<T = unknown>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const opts: RequestInit = {
    ...options,
    headers: buildHeaders(options?.headers as HeadersInit | undefined),
  };

  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) {
    let errMsg = res.statusText;
    try {
      const json = await res.json();
      errMsg = json.error || errMsg;
    } catch (_) {
      // ignore parse error
    }
    throw new Error(errMsg);
  }
  return res.json() as Promise<T>;
}

/** 带 token 的文件上传（multipart/form-data，不设 Content-Type 让浏览器自动设置） */
export async function backupUpload<T = unknown>(
  path: string,
  formData: FormData
): Promise<T> {
  const token = getBackupToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers["x-access-token"] = token;
  }

  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!res.ok) {
    let errMsg = res.statusText;
    try {
      const json = await res.json();
      errMsg = json.error || errMsg;
    } catch (_) {
      // ignore
    }
    throw new Error(errMsg);
  }
  return res.json() as Promise<T>;
}

export function formatSize(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  for (const unit of units) {
    if (value < 1024) return `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(1)} TB`;
}
