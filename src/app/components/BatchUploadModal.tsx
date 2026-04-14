import { useEffect, useMemo, useRef, useState } from 'react';
import {
  X,
  Play,
  Pause,
  SkipForward,
  RotateCcw,
  AlertCircle,
  CheckCircle2,
  Loader,
  File as FileIcon,
  Folder,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { runMinerUPipeline } from '../../utils/mineruApi';
import { checkLocalMinerUHealth } from '../../utils/mineruLocalApi';
import type { BatchQueueItem } from '../../store/types';

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
  const idCounterRef = useRef(0);
  const lastWarnRef = useRef(new Map<string, number>());

  const items = state.batchProcessing.items;
  const running = state.batchProcessing.running;
  const paused = state.batchProcessing.paused;
  const autoMinerU = state.batchProcessing.autoMinerU;
  const autoAI = state.batchProcessing.autoAI;

  const nextPending = useMemo(
    () => items.find((i) => i.status === 'pending'),
    [items],
  );

  const activeItem = useMemo(
    () => items.find((i) => i.status === 'uploading' || i.status === 'mineru' || i.status === 'ai'),
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

      toast.warning(`长时间无进度：${activeItem.fileName}`, {
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
    if (!running || paused || working) return;

    if (!nextPending) {
      if (wasRunningRef.current) toast.success('批量处理队列已完成');
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
      let stage: 'upload' | 'mineru' | 'ai' = 'upload';
      try {
        const uploadHealth = await fetchWithTimeout('/__proxy/upload/health', { timeoutMs: 5000 }).catch(() => null);
        if (!uploadHealth?.ok) throw new Error('上传服务不可用（/__proxy/upload/health）');

        updateItem(item.id, { status: 'uploading', progress: 10, message: '正在上传文件...' });

        idCounterRef.current = (idCounterRef.current + 1) % 1000;
        const newId = Date.now() * 1000 + idCounterRef.current;
        materialId = newId;
        updateItem(item.id, { materialId: newId });

        const updateMaterialProgress = (stage: 'upload' | 'mineru' | 'ai' | '', msg: string, progress?: number) => {
          dispatch({
            type: 'UPDATE_MATERIAL',
            payload: {
              id: newId,
              updates: {
                metadata: {
                  relativePath: item.path,
                  processingStage: stage,
                  processingMsg: msg,
                  ...(progress != null ? { processingProgress: String(Math.round(progress)) } : {}),
                  processingUpdatedAt: new Date().toISOString(),
                },
              },
            },
          });
        };

        dispatch({
          type: 'ADD_MATERIAL',
          payload: {
            id: newId,
            title: f.name.replace(/\.[^.]+$/, ''),
            type: f.name.split('.').pop()?.toUpperCase() ?? 'FILE',
            size: `${(f.size / 1024 / 1024).toFixed(1)} MB`,
            sizeBytes: f.size,
            uploadTime: '上传中...',
            uploadTimestamp: Date.now(),
            status: 'processing',
            mineruStatus: 'pending',
            aiStatus: 'pending',
            tags: [],
            metadata: {
              relativePath: item.path,
              processingStage: 'upload',
              processingMsg: '正在上传文件...',
              processingProgress: '10',
              processingUpdatedAt: new Date().toISOString(),
            },
            uploader: '当前用户',
          },
        });

        const formData = new FormData();
        formData.append('file', f);
        formData.append('materialId', String(newId));

        const uploadRes = await fetchWithTimeout('/__proxy/upload/upload', {
          method: 'POST',
          body: formData,
          timeoutMs: 120_000,
        });

        if (!uploadRes.ok) {
          const errText = await uploadRes.text();
          throw new Error(`上传失败: HTTP ${uploadRes.status} - ${errText}`);
        }

        const uploadResult = await uploadRes.json();
        updateItem(item.id, { progress: 30, message: '上传完成' });
        updateMaterialProgress(autoMinerU ? 'mineru' : '', '上传完成', 30);

        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: newId,
            updates: {
              status: 'pending',
              uploadTime: '刚刚',
              metadata: {
                relativePath: item.path,
                fileUrl: uploadResult.url,
                objectName: uploadResult.objectName || '',
                fileName: uploadResult.fileName,
                provider: uploadResult.provider,
                mimeType: uploadResult.mimeType,
                ...(uploadResult.pages != null ? { pages: String(uploadResult.pages) } : {}),
                ...(uploadResult.format ? { format: uploadResult.format } : {}),
                processingStage: autoMinerU ? 'mineru' : '',
                processingMsg: '上传完成',
                processingProgress: '30',
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });

        if (!autoMinerU) {
          updateItem(item.id, { status: 'completed', progress: 100, message: '已完成（跳过 MinerU）' });
          updateMaterialProgress('', '', 100);
          batchRemoveFile(item.id);
          return;
        }

        stage = 'mineru';
        if (state.mineruConfig.engine === 'local') {
          const health = await checkLocalMinerUHealth(String(state.mineruConfig.localEndpoint || ''));
          if (!health.ok) throw new Error(`本地 MinerU 不可用：${health.message}`);
        } else {
          if (!String(state.mineruConfig.apiKey || '').trim()) throw new Error('MinerU API Key 未配置');
        }

        updateItem(item.id, { status: 'mineru', progress: 40, message: '正在解析文件（MinerU）...' });
        updateItem(item.id, { mineruStartedAt: Date.now() });
        updateMaterialProgress('mineru', '正在解析文件（MinerU）...', 40);
        dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: newId, mineruStatus: 'processing' } });

        let mineruTaskId = '';
        const onMinerUTaskId = (taskId: string) => {
          mineruTaskId = taskId;
          const msg = `MinerU 任务已提交（任务ID: ${taskId}）`;
          updateItem(item.id, { message: msg });
          updateMaterialProgress('mineru', msg, 40);
          dispatch({
            type: 'UPDATE_MATERIAL',
            payload: {
              id: newId,
              updates: {
                metadata: {
                  mineruTaskId: taskId,
                  processingStage: 'mineru',
                  processingMsg: msg,
                  processingUpdatedAt: new Date().toISOString(),
                },
              },
            },
          });
        };

        const onMinerUProgress = (progress: number, msg: string) => {
          const normalized = normalizePctInput(progress);
          const pct = 40 + (normalized / 100) * 30;
          const displayMsg = mineruTaskId ? `${msg}（任务ID: ${mineruTaskId}）` : msg;
          updateItem(item.id, { progress: pct, message: displayMsg });
          updateMaterialProgress('mineru', displayMsg, pct);
        };

        let mineruResult: Awaited<ReturnType<typeof runMinerUPipeline>>;
        try {
          mineruResult = await runMinerUPipeline(f, state.mineruConfig, onMinerUProgress, newId, onMinerUTaskId);
        } catch (e) {
          const raw = e instanceof Error ? e.message : String(e);
          const lower = raw.toLowerCase();
          const isTimeout = raw.includes('请求超时') || raw.includes('signal timed out') || lower.includes('timeout');
          const canFallbackToCloud =
            state.mineruConfig.engine === 'local' &&
            Boolean(String(state.mineruConfig.apiKey || '').trim());

          if (isTimeout && canFallbackToCloud) {
            toast.warning('本地 MinerU 超时，改用官方 API 重试…');
            updateItem(item.id, { status: 'mineru', progress: 42, message: '本地超时，改用官方 API 重试…' });
            mineruResult = await runMinerUPipeline(
              f,
              { ...state.mineruConfig, engine: 'cloud' },
              onMinerUProgress,
              newId,
              onMinerUTaskId,
            );
          } else {
            throw e;
          }
        }

        let finalMarkdownContent = mineruResult.markdown || '';

        if (mineruResult.zipUrl) {
          dispatch({ type: 'UPDATE_MATERIAL_MINERU_ZIP_URL', payload: { id: newId, mineruZipUrl: mineruResult.zipUrl } });
          try {
            const downloadRes = await fetchWithTimeout('/__proxy/upload/parse/download', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ zipUrl: mineruResult.zipUrl, materialId: newId }),
              timeoutMs: 60_000,
            });
            if (downloadRes.ok) {
              const downloadData = await downloadRes.json();
              if (downloadData.markdownObjectName || downloadData.markdownUrl) {
                mineruResult.markdownObjectName = downloadData.markdownObjectName;
                mineruResult.markdownUrl = downloadData.markdownUrl;
                if (!downloadData.markdownContent && downloadData.markdownUrl) {
                  const mdRes = await fetchWithTimeout(downloadData.markdownUrl, { timeoutMs: 60_000 });
                  if (mdRes.ok) finalMarkdownContent = await mdRes.text();
                } else if (downloadData.markdownContent) {
                  finalMarkdownContent = downloadData.markdownContent;
                }
              }
            }
          } catch {
          }
        }

        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: newId,
            updates: {
              mineruStatus: 'completed',
              metadata: {
                relativePath: item.path,
                markdownObjectName: mineruResult.markdownObjectName,
                markdownUrl: mineruResult.markdownUrl,
                processingStage: 'ai',
                processingMsg: 'MinerU 解析完成',
                processingProgress: '70',
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });
        dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: newId, mineruStatus: 'completed', mineruCompletedAt: Date.now() } });
        updateItem(item.id, { progress: 70, message: 'MinerU 解析完成' });
        updateMaterialProgress('ai', 'MinerU 解析完成', 70);

        if (!autoAI || (!mineruResult.markdownObjectName && !mineruResult.markdownUrl && !finalMarkdownContent)) {
          updateItem(item.id, { status: 'completed', progress: 100, message: '已完成（跳过 AI）' });
          updateMaterialProgress('', '', 100);
          batchRemoveFile(item.id);
          return;
        }

        stage = 'ai';
        const { apiEndpoint, apiKey, model } = state.aiConfig;
        if (!apiEndpoint?.trim() || !apiKey?.trim() || !model?.trim()) {
          throw new Error('未配置 AI 服务');
        }

        updateItem(item.id, { status: 'ai', progress: 80, message: '正在进行 AI 分析...' });
        updateMaterialProgress('ai', '正在进行 AI 分析...', 80);
        dispatch({ type: 'UPDATE_MATERIAL_AI_STATUS', payload: { id: newId, aiStatus: 'analyzing' } });

        const aiRes = await fetchWithTimeout('/__proxy/upload/parse/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            markdownObjectName: mineruResult.markdownObjectName,
            markdownUrl: mineruResult.markdownUrl,
            markdownContent: finalMarkdownContent || undefined,
            materialId: newId,
            aiApiEndpoint: apiEndpoint.replace(/\/$/, ''),
            aiApiKey: apiKey,
            aiModel: model,
            prompts: state.aiConfig.prompts,
          }),
          timeoutMs: Math.max(60_000, (state.aiConfig.timeout || 300) * 1000 + 30_000),
        });

        if (!aiRes.ok) {
          const errData = await aiRes.json().catch(() => ({ error: `HTTP ${aiRes.status}` }));
          const errorType = String((errData as { errorType?: string } | null)?.errorType || '');
          if (aiRes.status === 429 || errorType === 'INSUFFICIENT_BALANCE' || errorType === 'RATE_LIMIT') {
            dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: true } });
            dispatch({ type: 'BATCH_SET_OPTIONS', payload: { autoAI: false } });
          }
          throw new Error((errData as { error?: string } | null)?.error || `HTTP ${aiRes.status}`);
        }

        const aiData = await aiRes.json();
        dispatch({
          type: 'UPDATE_MATERIAL_AI_STATUS',
          payload: {
            id: newId,
            aiStatus: 'analyzed',
            status: 'completed',
            ...(aiData.title ? { title: aiData.title } : {}),
            tags: aiData.tags?.length ? aiData.tags : [],
            metadata: {
              relativePath: item.path,
              subject: aiData.subject || '',
              grade: aiData.grade || '',
              type: aiData.materialType || '',
              language: aiData.language || '',
              country: aiData.country || '',
              summary: aiData.summary || '',
              aiConfidence: String(aiData.confidence ?? ''),
              aiAnalyzedAt: aiData.analyzedAt || new Date().toISOString(),
              processingStage: '',
              processingMsg: '',
              processingProgress: '100',
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        });

        updateItem(item.id, { status: 'completed', progress: 100, message: '全部完成' });
        updateMaterialProgress('', '', 100);
        batchRemoveFile(item.id);
      } catch (error) {
        const raw = error instanceof Error ? error.message : String(error);
        const stageLabel =
          stage === 'upload' ? '上传阶段'
          : stage === 'mineru' ? 'MinerU 阶段'
          : 'AI 阶段';
        const hint =
          stage === 'upload'
            ? '请确认 upload-server 正在运行，并可访问 /__proxy/upload/health'
            : stage === 'mineru' && state.mineruConfig.engine === 'local'
              ? '请检查本地 MinerU 地址（系统设置）与服务是否在线'
              : stage === 'mineru'
                ? '请检查 MinerU API Key 与网络连通性'
                : '请检查 AI 配置与 upload-server /parse/analyze 日志';

        const msg = raw.includes('请求超时') || raw.includes('timed out') || raw.includes('Timeout')
          ? `${stageLabel}${raw}。${hint}`
          : `${stageLabel}失败：${raw}`;

        updateItem(item.id, { status: 'error', message: msg });
        if (materialId) {
          dispatch({
            type: 'UPDATE_MATERIAL',
            payload: {
              id: materialId,
              updates: {
                status: 'failed',
                uploadTime: '处理失败',
                metadata: {
                  processingMsg: msg,
                  processingUpdatedAt: new Date().toISOString(),
                },
              },
            },
          });
          if (stage === 'ai') {
            dispatch({ type: 'UPDATE_MATERIAL_AI_STATUS', payload: { id: materialId, aiStatus: 'failed' } });
          } else {
            dispatch({ type: 'UPDATE_MATERIAL_AI_STATUS', payload: { id: materialId, aiStatus: 'failed' } });
            dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: materialId, mineruStatus: 'failed', mineruCompletedAt: Date.now() } });
          }
        }
      }
    };

    setWorking(true);
    processOne(nextPending, file).finally(() => {
      setWorking(false);
    });
  }, [autoAI, autoMinerU, dispatch, nextPending, paused, running, state.aiConfig, state.mineruConfig, working]);

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

      const aiOk = Boolean(String(state.aiConfig.apiEndpoint || '').trim() && String(state.aiConfig.apiKey || '').trim() && String(state.aiConfig.model || '').trim());
      const aiMsg = aiOk ? 'AI 配置已填写' : 'AI 配置缺失（apiEndpoint / apiKey / model）';

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

            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={bp.autoMinerU}
                onChange={(e) => dispatch({ type: 'BATCH_SET_OPTIONS', payload: { autoMinerU: e.target.checked } })}
                className="rounded text-blue-600"
              />
              自动 MinerU 解析
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={bp.autoAI}
                onChange={(e) => dispatch({ type: 'BATCH_SET_OPTIONS', payload: { autoAI: e.target.checked } })}
                className="rounded text-blue-600"
                disabled={!bp.autoMinerU}
              />
              自动 AI 分析
            </label>
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
              <p>暂无文件，请先在「原始资料」中选择文件或文件夹</p>
            </div>
          ) : (
            items.map((item) => {
              const isMinerURunning = item.status === 'mineru';
              const mineruLimitSec =
                state.mineruConfig.engine === 'local'
                  ? Number(state.mineruConfig.localTimeout || 0)
                  : Number(state.mineruConfig.timeout || 0);
              const elapsedMs = isMinerURunning
                ? Math.max(0, now - (item.mineruStartedAt || item.updatedAt))
                : 0;

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
                      <button onClick={() => handleRetry(item.id)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded" title="重试">
                        <RotateCcw size={14} />
                      </button>
                    )}
                    {(item.status === 'pending' || item.status === 'error' || item.status === 'skipped') && (
                      <button onClick={() => handleRemove(item.id)} className="p-1.5 text-red-500 hover:bg-red-50 rounded" title="移除">
                        <X size={14} />
                      </button>
                    )}
                  </div>
                </div>

                {isMinerURunning ? (
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-xs text-gray-600">
                      <Loader size={14} className="animate-spin" />
                      <span>正在执行深度解析...</span>
                    </div>
                    <div className="text-xs text-gray-500">
                      已耗时: {formatClock(elapsedMs)}{mineruLimitSec > 0 ? ` / 限额: ${formatClock(mineruLimitSec * 1000)}` : ''}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all duration-300 ${
                          item.status === 'error'
                            ? 'bg-red-500'
                            : item.status === 'completed'
                              ? 'bg-green-500'
                              : item.status === 'skipped'
                                ? 'bg-gray-400'
                                : 'bg-blue-500'
                        }`}
                        style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }}
                      />
                    </div>
                    <div className="w-24 text-right">
                      {item.status === 'error' ? (
                        <span className="flex items-center justify-end gap-1 text-xs text-red-600">
                          <AlertCircle size={12} /> 出错
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

  const activeCount = bp.items.filter((i) => !['completed', 'skipped', 'error'].includes(i.status)).length;
  const errorCount = bp.items.filter((i) => i.status === 'error').length;
  const totalCount = bp.items.length;
  const activeItem = bp.items.find((i) => i.status === 'uploading' || i.status === 'mineru' || i.status === 'ai');
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

  if (totalCount === 0) return null;

  const label = bp.running
    ? `处理中 ${activeCount}/${totalCount}`
    : bp.paused
      ? `已暂停 ${activeCount}/${totalCount}`
      : `队列 ${activeCount}/${totalCount}`;

  return (
    <button
      onClick={() => dispatch({ type: 'BATCH_SET_UI_OPEN', payload: { uiOpen: true } })}
      className="fixed right-5 bottom-5 z-40 flex items-center gap-2 px-4 py-2 rounded-full shadow-lg border border-gray-200 bg-white hover:bg-gray-50 text-sm text-gray-800"
      title="打开批处理进度"
    >
      <span
        className={`inline-block w-2 h-2 rounded-full ${
          isStale
            ? 'bg-red-500'
            : bp.running && !bp.paused
              ? 'bg-blue-600'
              : bp.paused
                ? 'bg-yellow-500'
                : 'bg-gray-400'
        }`}
      />
      <span className="font-medium">{label}</span>
      {errorCount > 0 && (
        <span className="ml-1 px-2 py-0.5 rounded-full bg-red-50 text-red-600 text-xs">
          {errorCount} 失败
        </span>
      )}
    </button>
  );
}
