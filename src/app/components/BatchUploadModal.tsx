import { useEffect, useMemo, useRef, useState } from 'react';
import {
  X,
  Play,
  Pause,
  SkipForward,
  RotateCcw,
  ExternalLink,
  AlertCircle,
  CheckCircle2,
  Loader,
  File as FileIcon,
  Folder,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { checkLocalMinerUHealth } from '../../utils/mineruLocalApi';
import type { BatchQueueItem } from '../../store/types';
import { generateNumericIdFromUuid } from '../../utils/id';

const runtimeFileMap: Map<string, File> =
  (globalThis as { __luceonBatchFileMap?: Map<string, File> }).__luceonBatchFileMap ||
  ((globalThis as { __luceonBatchFileMap?: Map<string, File> }).__luceonBatchFileMap = new Map<string, File>());

export function batchRegisterFiles(items: Array<{ id: string; file: File }>) {
  for (const it of items) runtimeFileMap.set(it.id, it.file);
}

function batchGetFile(id: string) {
  return runtimeFileMap.get(id);
}

function batchRemoveFile(id: string) {
  runtimeFileMap.delete(id);
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(idx === 0 ? 0 : 2)} ${units[idx]}`;
}

function formatAgo(ts: number) {
  const diff = Date.now() - ts;
  if (!Number.isFinite(diff) || diff < 0) return '刚刚';
  const sec = Math.floor(diff / 1000);
  if (sec < 3) return '刚刚';
  if (sec < 60) return `${sec}s 前`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}min 前`;
  const hr = Math.floor(min / 60);
  return `${hr}h 前`;
}

function fixFilenameEncoding(filename: string | undefined): string {
  if (!filename) return '';

  // 检测是否包含典型的编码错误字符。乱码文件名可能混入 C1 控制字符，
  // 例如 "附" 被误解码为 "é\u0099\u0084"。
  const hasMojiChars = /[\u0080-\u00FF]/.test(filename);
  if (!hasMojiChars) return filename;

  try {
    // 将按 Latin-1 误解码的字符还原为原始字节，再按 UTF-8 解码。
    const latin1Bytes = Uint8Array.from(
      Array.from(filename, (char) => char.charCodeAt(0) & 0xff)
    );
    const utf8String = new TextDecoder('utf-8').decode(latin1Bytes);

    // 验证修复后的字符串是否包含中文字符（确认修复成功）
    if (/[\u4E00-\u9FFF]/.test(utf8String)) {
      return utf8String;
    }
  } catch {
  }

  return filename;
}

function formatClock(ms: number) {
  if (!Number.isFinite(ms) || ms <= 0) return '00:00';
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  const mm = String(min).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return `${mm}:${ss}`;
}

function clampPct(pct: number) {
  if (!Number.isFinite(pct)) return 0;
  return Math.max(0, Math.min(100, pct));
}

function normalizePctInput(p: number) {
  if (!Number.isFinite(p)) return 0;
  if (p >= 0 && p <= 1) return p * 100;
  return p;
}

function getStallThresholdMs(
  status: string,
  aiTimeoutSec: number,
  mineruTimeoutSec: number,
  mineruEngine: 'local' | 'cloud',
  mineruLocalTimeoutSec: number,
) {
  if (status === 'uploading') return 120_000;
  if (status === 'task-created' || status === 'tracking') {
    const t = mineruEngine === 'local' ? (mineruLocalTimeoutSec || 300) : (mineruTimeoutSec || 300);
    return Math.max(120_000, t * 1000 + 60_000);
  }
  if (status === 'ai') return Math.max(60_000, (aiTimeoutSec || 300) * 1000 + 30_000);
  if (status === 'mineru') {
    const t = mineruEngine === 'local' ? (mineruLocalTimeoutSec || 300) : (mineruTimeoutSec || 300);
    return Math.max(120_000, t * 1000 + 30_000);
  }
  return 300_000;
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit & { timeoutMs?: number } = {}) {
  const { timeoutMs, ...rest } = init;
  const controller = new AbortController();
  const t = timeoutMs != null ? timeoutMs : 60_000;
  const timer = setTimeout(() => controller.abort(), t);
  try {
    return await fetch(input, { ...rest, signal: controller.signal });
  } catch (error) {
    const name = (error as { name?: string } | null)?.name;
    if (name === 'AbortError') {
      throw new Error(`请求超时（${Math.round(t / 1000)}s）`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export function BatchProcessingController() {
  const { state, dispatch } = useAppStore();
  const [working, setWorking] = useState(false);
  const wasRunningRef = useRef(false);
  const lastWarnRef = useRef(new Map<string, number>());

  const items = state.batchProcessing.items;
  const running = state.batchProcessing.running;
  const paused = state.batchProcessing.paused;

  const nextPending = useMemo(
    () => items.find((i) => i.status === 'pending'),
    [items],
  );

  const activeItem = useMemo(
    () => items.find((i) => i.status === 'uploading' || i.status === 'task-created' || i.status === 'tracking'),
    [items],
  );

  useEffect(() => {
    if (!state.batchProcessing.running || state.batchProcessing.paused) return;
    const tick = () => {
      if (!activeItem) return;
      const threshold = getStallThresholdMs(
        activeItem.status,
        state.aiConfig.timeout as number,
        state.mineruConfig.timeout as number,
        state.mineruConfig.engine,
        state.mineruConfig.localTimeout as number,
      );
      const staleMs = Date.now() - activeItem.updatedAt;
      if (staleMs < threshold) return;

      const lastWarnAt = lastWarnRef.current.get(activeItem.id) ?? 0;
      if (Date.now() - lastWarnAt < 60_000) return;
      lastWarnRef.current.set(activeItem.id, Date.now());

      toast.warning(`长时间无进度：${fixFilenameEncoding(activeItem.fileName)}`, {
        description: `阶段：${activeItem.status}，最后更新：${formatAgo(activeItem.updatedAt)}`,
        action: {
          label: '暂停队列',
          onClick: () => dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: true } }),
        },
      });
    };

    const timer = setInterval(tick, 5000);
    return () => clearInterval(timer);
  }, [activeItem, dispatch, state.aiConfig.timeout, state.batchProcessing.paused, state.batchProcessing.running, state.mineruConfig.timeout]);

  useEffect(() => {
    if (!running || paused) return;
    const timer = window.setInterval(() => {
      const trackables = items.filter((i) =>
        (i.status === 'task-created' || i.status === 'tracking') && typeof i.taskId === 'string' && i.taskId.trim(),
      );
      if (trackables.length === 0) return;

      for (const it of trackables) {
        const taskId = String(it.taskId || '').trim();
        if (!taskId) continue;
        fetchWithTimeout(`/__proxy/db/tasks/${encodeURIComponent(taskId)}`, { timeoutMs: 8000 })
          .then((r) => (r.ok ? r.json().catch(() => null) : Promise.reject(new Error(`HTTP ${r.status}`))))
          .then((task) => {
            const stateKey = String(task?.state || '').trim();
            const stageKey = String(task?.stage || '').trim();
            const progress = Number(task?.progress);
            const message = String(task?.message || '').trim();
            const errorMessage = String(task?.errorMessage || '').trim();
            const terminal = new Set(['completed', 'review-pending', 'failed', 'canceled']);
            if (terminal.has(stateKey)) {
              const status =
                stateKey === 'review-pending'
                  ? 'review-pending'
                  : stateKey === 'completed'
                    ? 'completed'
                    : stateKey === 'canceled'
                      ? 'canceled'
                      : 'failed';
              const hint =
                status === 'failed' || status === 'canceled'
                  ? '（可打开任务页重跑）'
                  : status === 'review-pending'
                    ? '（可打开任务页查看详情）'
                    : '';
              dispatch({
                type: 'BATCH_UPDATE_ITEM',
                payload: {
                  id: it.id,
                  updates: {
                    status,
                    progress: 100,
                    taskState: stateKey,
                    taskStage: stageKey,
                    message: errorMessage
                      ? `处理失败：${errorMessage}${hint}`
                      : message
                        ? `${message}${hint}`
                        : status === 'review-pending'
                          ? `待人工复核${hint}`
                          : status === 'completed'
                            ? '处理完成'
                            : `处理失败${hint}`,
                  },
                },
              });
              return;
            }

            dispatch({
              type: 'BATCH_UPDATE_ITEM',
              payload: {
                id: it.id,
                updates: {
                  status: 'tracking',
                  progress: Number.isFinite(progress) ? Math.max(0, Math.min(99, progress)) : it.progress,
                  taskState: stateKey || it.taskState,
                  taskStage: stageKey || it.taskStage,
                  message: message || (stageKey ? `处理中：${stageKey}` : '处理中'),
                },
              },
            });
          })
          .catch(() => {});
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [dispatch, items, paused, running]);

  useEffect(() => {
    if (!running || paused || working) return;

    if (!nextPending) {
      const stillActive = items.some((i) => i.status === 'uploading' || i.status === 'task-created' || i.status === 'tracking');
      if (stillActive) return;

      if (wasRunningRef.current) {
        const created = items.filter((i) => typeof i.taskId === 'string' && i.taskId.trim()).length;
        const done = items.filter((i) => i.status === 'completed').length;
        const reviewPending = items.filter((i) => i.status === 'review-pending').length;
        const failed = items.filter((i) => i.status === 'failed' || i.status === 'canceled').length;
        const uploadFailed = items.filter((i) => i.status === 'error').length;
        const skipped = items.filter((i) => i.status === 'skipped').length;
        toast(
          '批量队列已结束',
          {
            description: [
              `已创建任务 ${created}`,
              `处理完成 ${done}`,
              `待复核 ${reviewPending}`,
              `处理失败 ${failed}`,
              `上传失败 ${uploadFailed}`,
              skipped > 0 ? `跳过 ${skipped}` : null,
            ].filter(Boolean).join('，'),
            duration: 8000,
          },
        );
      }
      wasRunningRef.current = false;
      dispatch({ type: 'BATCH_SET_RUNNING', payload: { running: false } });
      dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: false } });
      return;
    }

    wasRunningRef.current = true;

    const file = batchGetFile(nextPending.id);
    if (!file) {
      dispatch({
        type: 'BATCH_UPDATE_ITEM',
        payload: { id: nextPending.id, updates: { status: 'error', message: '缺少文件句柄（可能刷新页面导致）' } },
      });
      return;
    }

    const updateItem = (id: string, updates: Partial<BatchQueueItem>) => {
      const nextUpdates: Partial<BatchQueueItem> = {
        ...updates,
        ...(updates.progress != null ? { progress: clampPct(updates.progress) } : {}),
      };
      dispatch({ type: 'BATCH_UPDATE_ITEM', payload: { id, updates: nextUpdates } });
    };

    const processOne = async (item: BatchQueueItem, f: File) => {
      let materialId: number | undefined;
      try {
        const uploadHealth = await fetchWithTimeout('/__proxy/upload/health', { timeoutMs: 5000 }).catch(() => null);
        if (!uploadHealth?.ok) throw new Error('上传服务不可用（/__proxy/upload/health）');

        updateItem(item.id, { status: 'uploading', progress: 10, message: '正在提交上传...' });

        const newId = generateNumericIdFromUuid();
        materialId = newId;
        updateItem(item.id, { materialId: newId });

        const formData = new FormData();
        formData.append('file', f);
        formData.append('materialId', String(newId));

        const uploadRes = await fetchWithTimeout('/__proxy/upload/tasks', {
          method: 'POST',
          body: formData,
          timeoutMs: 120_000,
        });

        if (!uploadRes.ok) {
          const errText = await uploadRes.text();
          throw new Error(`上传失败: HTTP ${uploadRes.status} - ${errText}`);
        }

        const uploadResult = await uploadRes.json();
        const taskId = String(uploadResult?.taskId || '').trim();
        const objectName = String(uploadResult?.objectName || '').trim();
        if (!taskId) throw new Error('后端未返回 taskId（任务未确认创建）');
        if (!objectName) {
          throw new Error('上传成功但未获得 objectName（未写入 MinIO）。后端队列只能处理 MinIO 文件，请检查存储后端配置。');
        }

        const fileName = String(uploadResult?.fileName || f.name || '').trim() || f.name;
        const title = fileName.replace(/\.[^.]+$/, '');
        dispatch({
          type: 'ADD_MATERIAL',
          payload: {
            id: newId,
            title,
            type: (fileName.split('.').pop() || 'FILE').toUpperCase(),
            size: `${(f.size / 1024 / 1024).toFixed(1)} MB`,
            sizeBytes: f.size,
            uploadTime: '刚刚',
            uploadTimestamp: Date.now(),
            status: 'processing',
            mineruStatus: 'pending',
            aiStatus: 'pending',
            tags: [],
            metadata: {
              relativePath: item.path,
              objectName,
              fileName,
              provider: uploadResult?.provider,
              mimeType: uploadResult?.mimeType,
              processingStage: 'mineru',
              processingMsg: '等待后端队列处理',
              processingProgress: '0',
              processingUpdatedAt: new Date().toISOString(),
            },
            uploader: '当前用户',
          },
        });

        updateItem(item.id, {
          status: 'task-created',
          progress: 25,
          taskId,
          objectName,
          message: `任务已创建（${taskId}）`,
        });
        batchRemoveFile(item.id);
        toast.success('任务已创建');
      } catch (error) {
        const raw = error instanceof Error ? error.message : String(error);
        const lowered = raw.toLowerCase();
        const classify = () => {
          if (raw.includes('UNSUPPORTED_FORMAT') || raw.includes('不支持的文件格式')) return `不支持的文件格式：${raw}`;
          if (raw.includes('Request aborted') || lowered.includes('request aborted') || lowered.includes('aborted')) return `请求中断（Request aborted）：${raw}`;
          if (raw.includes('请求超时') || lowered.includes('timed out') || lowered.includes('timeout')) return `请求超时：${raw}`;
          if (lowered.includes('network') || lowered.includes('failed to fetch')) return `网络错误：${raw}`;
          return `上传失败：${raw}`;
        };

        const msg = classify();

        const canReconcile = materialId != null
          && (msg.startsWith('请求中断') || msg.startsWith('请求超时') || msg.startsWith('网络错误'));
        if (canReconcile) {
          try {
            const [taskResp, matResp] = await Promise.all([
              fetchWithTimeout('/__proxy/db/tasks', { timeoutMs: 5000 }).catch(() => null),
              fetchWithTimeout(`/__proxy/db/materials/${encodeURIComponent(String(materialId))}`, { timeoutMs: 5000 }).catch(() => null),
            ]);
            if (taskResp?.ok && matResp?.ok) {
              const tasks = await taskResp.json().catch(() => null);
              const material = await matResp.json().catch(() => null);
              const related = Array.isArray(tasks)
                ? tasks.find((t) => String(t?.materialId) === String(materialId))
                : null;
              const objectName = String(material?.metadata?.objectName || '').trim();
              if (related && objectName) {
                const mIdNum = Number(String(materialId));
                const payload = {
                  id: mIdNum,
                  title: String(material?.title || '').trim() || String(material?.fileName || '').replace(/\.[^/.]+$/, ''),
                  type: String(material?.type || 'FILE'),
                  size: `${((Number(material?.fileSize) || 0) / 1024 / 1024).toFixed(1)} MB`,
                  sizeBytes: Number(material?.fileSize) || 0,
                  uploadTime: '刚刚',
                  uploadTimestamp: Date.now(),
                  status: material?.status || 'processing',
                  mineruStatus: material?.mineruStatus || 'pending',
                  aiStatus: material?.aiStatus || 'pending',
                  tags: Array.isArray(material?.tags) ? material.tags : [],
                  metadata: material?.metadata || { objectName },
                  uploader: '当前用户',
                };
                const exists = state.materials.some((m) => m.id === mIdNum);
                if (exists) {
                  dispatch({ type: 'UPDATE_MATERIAL', payload: { id: mIdNum, updates: payload } });
                } else {
                  dispatch({ type: 'ADD_MATERIAL', payload });
                }
                const taskId = String(related?.id || '').trim();
                updateItem(item.id, {
                  status: 'task-created',
                  progress: 25,
                  taskId,
                  objectName,
                  message: `上传已创建任务（响应中断后自动对账）`,
                });
                batchRemoveFile(item.id);
                return;
              }
            }
          } catch {
          }
        }

        updateItem(item.id, { status: 'error', message: msg });
      }
    };

    setWorking(true);
    processOne(nextPending, file).finally(() => {
      setWorking(false);
    });
  }, [dispatch, items, nextPending, paused, running, working]);

  return null;
}

export function BatchUploadModal() {
  const { state, dispatch } = useAppStore();
  const bp = state.batchProcessing;
  const items = bp.items;

  const isProcessing = bp.running && !bp.paused;
  const [diagRunning, setDiagRunning] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!bp.uiOpen) return;
    const hasMinerURunning = items.some((it) => it.status === 'mineru');
    if (!hasMinerURunning) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [bp.uiOpen, items]);

  const runDiagnostics = async () => {
    if (diagRunning) return;
    setDiagRunning(true);
    try {
      const [uploadHealth, dbHealth] = await Promise.all([
        fetchWithTimeout('/__proxy/upload/health', { timeoutMs: 5000 })
          .then((r) => (r.ok ? r.json().catch(() => ({})) : Promise.reject(new Error(`HTTP ${r.status}`))))
          .then(() => ({ ok: true as const, message: 'upload-server 正常' }))
          .catch((e) => ({ ok: false as const, message: `upload-server 异常：${e instanceof Error ? e.message : String(e)}` })),
        fetchWithTimeout('/__proxy/db/health', { timeoutMs: 5000 })
          .then((r) => (r.ok ? r.json().catch(() => ({})) : Promise.reject(new Error(`HTTP ${r.status}`))))
          .then(() => ({ ok: true as const, message: 'db-server 正常' }))
          .catch((e) => ({ ok: false as const, message: `db-server 异常：${e instanceof Error ? e.message : String(e)}` })),
      ]);

      const mineruEngine = state.mineruConfig.engine;
      const mineruCheck =
        mineruEngine === 'local'
          ? await checkLocalMinerUHealth(String(state.mineruConfig.localEndpoint || ''))
          : { ok: Boolean(String(state.mineruConfig.apiKey || '').trim()), message: String(state.mineruConfig.apiKey ? 'MinerU API Key 已配置' : 'MinerU API Key 未配置') };

      const enabledAiProviders = state.aiConfig.providers?.filter((p) => p.enabled) ?? [];
      const aiOk = enabledAiProviders.length > 0
        || Boolean(String(state.aiConfig.apiEndpoint || '').trim() && String(state.aiConfig.model || '').trim());
      const aiMsg = aiOk
        ? `AI 配置已填写（${enabledAiProviders.length > 0 ? `${enabledAiProviders.length} 个提供商已启用` : '旧格式'}）`
        : 'AI 配置缺失（请在系统设置中配置至少一个 AI 提供商）';

      const lines = [
        uploadHealth.ok ? `✅ ${uploadHealth.message}` : `❌ ${uploadHealth.message}`,
        dbHealth.ok ? `✅ ${dbHealth.message}` : `❌ ${dbHealth.message}`,
        mineruCheck.ok ? `✅ MinerU：${mineruCheck.message}` : `❌ MinerU：${mineruCheck.message}`,
        aiOk ? `✅ ${aiMsg}` : `❌ ${aiMsg}`,
      ];

      toast('连通性检测结果', {
        description: lines.join('\n'),
        duration: 12000,
      });
    } finally {
      setDiagRunning(false);
    }
  };

  const handleClose = () => {
    dispatch({ type: 'BATCH_SET_UI_OPEN', payload: { uiOpen: false } });
  };

  const handleStart = () => {
    dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: false } });
    dispatch({ type: 'BATCH_SET_RUNNING', payload: { running: true } });
  };

  const handlePause = () => {
    dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: true } });
  };

  const handleSkip = (id: string) => {
    dispatch({ type: 'BATCH_UPDATE_ITEM', payload: { id, updates: { status: 'skipped', message: '已手动跳过' } } });
  };

  const handleRetry = (id: string) => {
    const item = items.find((i) => i.id === id);
    if (!item) return;
    const taskId = String(item.taskId || '').trim();
    if (taskId) {
      window.open(`/cms/tasks/${encodeURIComponent(taskId)}`, '_blank');
      toast('已打开任务详情页', { description: '如需重跑解析/AI，请在任务详情页执行对应操作。' });
      return;
    }
    dispatch({ type: 'BATCH_UPDATE_ITEM', payload: { id, updates: { status: 'pending', progress: 0, message: '等待重试...', mineruStartedAt: undefined } } });
    dispatch({ type: 'BATCH_SET_RUNNING', payload: { running: true } });
    dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: false } });
  };

  const handleRemove = (id: string) => {
    batchRemoveFile(id);
    dispatch({ type: 'BATCH_REMOVE_ITEM', payload: { id } });
  };

  const handleClearAll = () => {
    for (const it of bp.items) batchRemoveFile(it.id);
    dispatch({ type: 'BATCH_CLEAR' });
  };

  if (!bp.uiOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-gray-50/50">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">批量上传与处理</h3>
            <p className="text-sm text-gray-500 mt-1">队列 {items.length} 个文件，离开页面也可在右下角继续查看进度</p>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex items-center justify-between p-4 border-b border-gray-100">
          <div className="flex items-center gap-4 flex-wrap">
            {!isProcessing ? (
              <button
                onClick={handleStart}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                <Play size={16} />
                {bp.paused ? '继续处理' : '开始处理'}
              </button>
            ) : (
              <button
                onClick={handlePause}
                className="flex items-center gap-2 px-4 py-2 bg-yellow-500 text-white rounded-lg hover:bg-yellow-600"
              >
                <Pause size={16} /> 暂停处理
              </button>
            )}

  
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={runDiagnostics}
              disabled={diagRunning}
              className="px-3 py-1.5 text-sm text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              {diagRunning ? '检测中…' : '连通性检测'}
            </button>
            <button
              onClick={handleClearAll}
              className="flex items-center gap-2 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded-lg"
            >
              <Trash2 size={16} /> 清空列表
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-50">
          {items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-400 space-y-3 py-12">
              <Folder size={48} className="text-gray-300" />
              <p>暂无文件，请先在工作台中上传文件或文件夹</p>
            </div>
          ) : (
            items.map((item) => {
              const isRunningPhase =
                item.status === 'uploading'
                || item.status === 'task-created'
                || item.status === 'tracking'
                || item.status === 'mineru'
                || item.status === 'ai';
              const mineruLimitSec =
                state.mineruConfig.engine === 'local'
                  ? Number(state.mineruConfig.localTimeout || 0)
                  : Number(state.mineruConfig.timeout || 0);
              const elapsedMs = item.status === 'mineru'
                ? Math.max(0, now - (item.mineruStartedAt || item.updatedAt))
                : 0;
              const taskId = String(item.taskId || '').trim();

              return (
                <div key={item.id} className="bg-white rounded-lg border border-gray-200 p-3 shadow-sm flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 overflow-hidden">
                    <FileIcon size={20} className="text-gray-400 flex-shrink-0" />
                    <div className="truncate">
                      <p className="text-sm font-medium text-gray-900 truncate" title={item.path}>
                        {item.path}
                      </p>
                      <p className="text-xs text-gray-500">{formatBytes(item.fileSize)} · 最后更新 {formatAgo(item.updatedAt)}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {item.status === 'pending' && (
                      <button onClick={() => handleSkip(item.id)} className="p-1.5 text-gray-400 hover:text-gray-600 bg-gray-50 rounded" title="跳过">
                        <SkipForward size={14} />
                      </button>
                    )}
                    {(item.status === 'error' || item.status === 'skipped') && (
                      <button onClick={() => handleRetry(item.id)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded" title="重试上传">
                        <RotateCcw size={14} />
                      </button>
                    )}
                    {(item.status === 'failed' || item.status === 'canceled' || item.status === 'review-pending') && taskId && (
                      <button onClick={() => handleRetry(item.id)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded" title="打开任务">
                        <ExternalLink size={14} />
                      </button>
                    )}
                    {(item.status === 'pending' || item.status === 'error' || item.status === 'skipped') && (
                      <button onClick={() => handleRemove(item.id)} className="p-1.5 text-red-500 hover:bg-red-50 rounded" title="移除">
                        <X size={14} />
                      </button>
                    )}
                  </div>
                </div>

                {isRunningPhase ? (
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-xs text-gray-600">
                      <Loader size={14} className="animate-spin" />
                      <span>
                        {item.status === 'uploading'
                          ? '正在提交上传...'
                          : item.status === 'task-created'
                            ? '任务已创建，等待处理...'
                            : item.status === 'tracking'
                              ? `处理中：${String(item.taskStage || item.taskState || '').trim() || 'processing'}`
                              : item.status === 'ai'
                                ? 'AI 分析中...'
                                : 'MinerU 解析中...'}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500">
                      {item.status === 'mineru'
                        ? `已耗时: ${formatClock(elapsedMs)}${mineruLimitSec > 0 ? ` / 限额: ${formatClock(mineruLimitSec * 1000)}` : ''}`
                        : taskId
                          ? `任务：${taskId}`
                          : ''}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all duration-300 ${
                          item.status === 'error' || item.status === 'failed' || item.status === 'canceled'
                            ? 'bg-red-500'
                            : item.status === 'completed'
                              ? 'bg-green-500'
                              : item.status === 'review-pending'
                                ? 'bg-yellow-500'
                              : item.status === 'skipped'
                                ? 'bg-gray-400'
                                : 'bg-blue-500'
                        }`}
                        style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }}
                      />
                    </div>
                    <div className="w-24 text-right">
                      {item.status === 'error' || item.status === 'failed' || item.status === 'canceled' ? (
                        <span className="flex items-center justify-end gap-1 text-xs text-red-600">
                          <AlertCircle size={12} /> 出错
                        </span>
                      ) : item.status === 'review-pending' ? (
                        <span className="flex items-center justify-end gap-1 text-xs text-yellow-700">
                          <AlertCircle size={12} /> 待复核
                        </span>
                      ) : item.status === 'completed' ? (
                        <span className="flex items-center justify-end gap-1 text-xs text-green-600">
                          <CheckCircle2 size={12} /> 完成
                        </span>
                      ) : (
                        <span className="text-xs text-gray-500">{clampPct(item.progress).toFixed(0)}%</span>
                      )}
                    </div>
                  </div>
                )}

                {item.message && (
                  <p className={`text-xs truncate ${item.status === 'error' ? 'text-red-500' : 'text-gray-500'}`} title={item.message}>
                    {item.message}
                  </p>
                )}
              </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

export function BatchProgressFab() {
  const { state, dispatch } = useAppStore();
  const bp = state.batchProcessing;

  // 前端队列统计
  const terminal = new Set(['completed', 'review-pending', 'failed', 'canceled', 'error', 'skipped']);
  const activeCount = bp.items.filter((i) => !terminal.has(i.status)).length;
  const errorCount = bp.items.filter((i) => i.status === 'error' || i.status === 'failed' || i.status === 'canceled').length;
  const totalCount = bp.items.length;
  const activeItem = bp.items.find((i) =>
    i.status === 'uploading'
    || i.status === 'task-created'
    || i.status === 'tracking'
    || i.status === 'mineru'
    || i.status === 'ai',
  );
  const isStale = activeItem
    ? Date.now() - activeItem.updatedAt >
      getStallThresholdMs(
        activeItem.status,
        state.aiConfig.timeout as number,
        state.mineruConfig.timeout as number,
        state.mineruConfig.engine,
        state.mineruConfig.localTimeout as number,
      )
    : false;

  // 无任何队列时隐藏
  if (totalCount === 0) return null;

  const label = bp.running
    ? `处理中 ${activeCount}/${totalCount}`
    : bp.paused
      ? `已暂停 ${activeCount}/${totalCount}`
      : `队列 ${activeCount}/${totalCount}`;
  const dotColor = isStale
    ? 'bg-red-500'
    : bp.running && !bp.paused
      ? 'bg-blue-600'
      : bp.paused
        ? 'bg-yellow-500'
        : 'bg-gray-400';

  return (
    <button
      onClick={() => dispatch({ type: 'BATCH_SET_UI_OPEN', payload: { uiOpen: true } })}
      className="fixed right-5 bottom-5 z-40 flex items-center gap-2 px-4 py-2 rounded-full shadow-lg border border-gray-200 bg-white hover:bg-gray-50 text-sm text-gray-800"
      title="打开批处理进度"
    >
      <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
      <span className="font-medium">{label}</span>
      {errorCount > 0 && (
        <span className="ml-1 px-2 py-0.5 rounded-full bg-red-50 text-red-600 text-xs">
          {errorCount} 失败
        </span>
      )}
    </button>
  );
}
