import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
 
type UploadProgress = { done: number; total: number; failed: number };
 
export function useFileUpload() {
  const { state, dispatch } = useAppStore();
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const hideTimerRef = useRef<number | null>(null);
  const lastServerQueueSyncAtRef = useRef(0);
 
  const validateFile = useCallback((file: File) => {
    const maxSize = (state.mineruConfig.maxFileSize || 0) > 0 ? state.mineruConfig.maxFileSize : 200 * 1024 * 1024;
    if (file.size > maxSize) {
      return { valid: false as const, error: `文件 "${file.name}" 超过上传限制 (最大 ${Math.round(maxSize / (1024 * 1024))}MB)` };
    }
    return { valid: true as const };
  }, [state.mineruConfig.maxFileSize]);
 
  const uploadWithProgress = useCallback((file: File, materialId: number, onProgress: (pct: number) => void) => {
    return new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/__proxy/upload/upload');
      xhr.responseType = 'json';
      xhr.upload.onprogress = (evt) => {
        if (!evt.lengthComputable) return;
        const pct = Math.max(0, Math.min(100, Math.round((evt.loaded / Math.max(1, evt.total)) * 100)));
        onProgress(pct);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
        else reject(new Error((xhr.response && (xhr.response as any).error) || xhr.responseText || `HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error('网络错误'));
      const formData = new FormData();
      formData.append('file', file);
      formData.append('materialId', String(materialId));
      xhr.send(formData);
    });
  }, []);
 
  const upload = useCallback(async (files: File[]) => {
    if (uploading) return;
    const list = Array.from(files || []);
    if (list.length === 0) return;
 
    const invalidFiles = list.filter((f) => !validateFile(f).valid);
    if (invalidFiles.length > 0) toast.error(`发现 ${invalidFiles.length} 个不符合规范的文件被过滤`);
    const validFiles = list.filter((f) => validateFile(f).valid);
    if (validFiles.length === 0) return;
 
    setUploading(true);
    if (hideTimerRef.current) {
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
    setProgress({ done: 0, total: validFiles.length, failed: 0 });
 
    let idCounter = 0;
    const items = validFiles.map((file) => {
      const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      idCounter = (idCounter + 1) % 1000;
      const materialId = Date.now() * 1000 + idCounter;
      const jobId = `job-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      return { file, filePath, materialId, jobId };
    });
 
    for (const it of items) {
      dispatch({
        type: 'ADD_MATERIAL',
        payload: {
          id: it.materialId,
          title: it.file.name.replace(/\.[^.]+$/, ''),
          type: it.file.name.split('.').pop()?.toUpperCase() ?? 'FILE',
          size: `${(it.file.size / 1024 / 1024).toFixed(1)} MB`,
          sizeBytes: it.file.size,
          uploadTime: '上传中...',
          uploadTimestamp: Date.now(),
          status: 'processing',
          mineruStatus: 'pending',
          aiStatus: 'pending',
          tags: [],
          metadata: {
            relativePath: it.filePath,
            processingStage: 'upload',
            processingMsg: '待上传',
            processingProgress: '0',
            processingUpdatedAt: new Date().toISOString(),
          },
          uploader: '当前用户',
        },
      });
    }
 
    try {
      const addRes = await fetch('/__proxy/upload/batch/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jobs: items.map((it) => ({
            id: it.jobId,
            fileName: it.file.name,
            fileSize: it.file.size,
            path: it.filePath,
            mimeType: it.file.type || 'application/octet-stream',
            materialId: it.materialId,
            status: 'uploading',
          })),
        }),
        signal: AbortSignal.timeout(15_000),
      });
      if (!addRes.ok) {
        const errData = await addRes.json().catch(() => ({ error: `HTTP ${addRes.status}` }));
        throw new Error((errData as { error?: string }).error || `HTTP ${addRes.status}`);
      }
      try {
        const statusRes = await fetch('/__proxy/upload/batch/status', { signal: AbortSignal.timeout(5000) });
        if (statusRes.ok) {
          const data = await statusRes.json();
          dispatch({ type: 'SERVER_BATCH_SYNC', payload: data });
        }
      } catch {}
    } catch (err) {
      toast.error(`提交队列失败：${err instanceof Error ? err.message : String(err)}`);
      setUploading(false);
      return;
    }
 
    const uploadOne = async (it: { file: File; filePath: string; materialId: number; jobId: string }) => {
      let lastEmitAt = 0;
      const emit = async (pct: number) => {
        const now = Date.now();
        if (now - lastEmitAt < 400 && pct !== 100) return;
        lastEmitAt = now;
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: it.materialId,
            updates: {
              metadata: {
                relativePath: it.filePath,
                processingStage: 'upload',
                processingMsg: `上传中 ${pct}%`,
                processingProgress: String(pct),
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });
        fetch(`/__proxy/upload/batch/job/${encodeURIComponent(it.jobId)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'uploading', progress: pct, message: pct === 0 ? '待上传' : `上传中 ${pct}%` }),
        }).catch(() => {});
      };
 
      try {
        await emit(0);
        const uploadResult = await uploadWithProgress(it.file, it.materialId, (pct) => { void emit(pct); });
        const objectName = String(uploadResult?.objectName || '').trim();
        if (!objectName) throw new Error('上传成功但未获得 objectName（未写入 MinIO）');
 
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: it.materialId,
            updates: {
              uploadTime: '刚刚',
              metadata: {
                relativePath: it.filePath,
                fileUrl: uploadResult.url,
                objectName,
                fileName: uploadResult.fileName,
                provider: uploadResult.provider,
                mimeType: uploadResult.mimeType,
                ...(uploadResult.pages != null ? { pages: String(uploadResult.pages) } : {}),
                ...(uploadResult.format ? { format: uploadResult.format } : {}),
                processingStage: 'mineru',
                processingMsg: '等待后端队列处理',
                processingProgress: '0',
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });
 
        await fetch(`/__proxy/upload/batch/job/${encodeURIComponent(it.jobId)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            status: 'pending',
            objectName,
            mimeType: uploadResult.mimeType || it.file.type || 'application/octet-stream',
            materialId: it.materialId,
            fileSize: it.file.size,
            path: it.filePath,
            progress: 0,
            message: '等待处理',
            error: '',
          }),
        }).catch(() => {});
 
        const now = Date.now();
        if (now - lastServerQueueSyncAtRef.current > 1000) {
          lastServerQueueSyncAtRef.current = now;
          try {
            const statusRes = await fetch('/__proxy/upload/batch/status', { signal: AbortSignal.timeout(5000) });
            if (statusRes.ok) {
              const data = await statusRes.json();
              dispatch({ type: 'SERVER_BATCH_SYNC', payload: data });
            }
          } catch {}
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: it.materialId,
            updates: {
              status: 'failed',
              mineruStatus: 'failed',
              aiStatus: 'failed',
              uploadTime: '上传失败',
              metadata: {
                processingStage: '',
                processingMsg: `上传失败：${msg}`,
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });
        fetch(`/__proxy/upload/batch/job/${encodeURIComponent(it.jobId)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'error', error: msg, message: `上传失败：${msg}` }),
        }).catch(() => {});
        setProgress((prev) => (prev ? { ...prev, failed: prev.failed + 1 } : prev));
      } finally {
        setProgress((prev) => {
          if (!prev) return prev;
          const next = { ...prev, done: prev.done + 1 };
          if (next.done >= next.total) {
            hideTimerRef.current = window.setTimeout(() => {
              setProgress(null);
              hideTimerRef.current = null;
            }, 2000);
          }
          return next;
        });
      }
    };
 
    const concurrency = 3;
    let idx = 0;
    const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
      while (idx < items.length) {
        const current = items[idx];
        idx += 1;
        await uploadOne(current);
      }
    });
    await Promise.all(runners);
 
    setUploading(false);
  }, [dispatch, uploadWithProgress, uploading, validateFile]);
 
  return { upload, uploading, progress };
}

