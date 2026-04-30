/**
 * useFileUpload Hook（P1 Patch: 列表退场重构）
 *
 * 负责文件校验、队列注册和自动启动 BatchProcessingController。
 * 上传后仅 toast 提示，不弹出列表 UI。
 * 进度信息不再由前端本地队列提供，一律到 /cms/tasks 查看。
 */

import { useCallback } from 'react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { batchRegisterFiles } from '../components/BatchUploadModal';

/**
 * 文件上传 Hook。
 * 校验文件后加入本地队列并自动启动 BatchProcessingController。
 * @returns upload 函数与 uploading 状态
 */
export function useFileUpload() {
  const { state, dispatch } = useAppStore();
  const bp = state.batchProcessing;

  /**
   * 校验单个文件是否可上传。
   * @param file - 待校验的 File 对象
   * @returns 校验结果
   */
  const validateFile = useCallback((file: File) => {
    const maxSize = (state.mineruConfig.maxFileSize || 0) > 0 ? state.mineruConfig.maxFileSize : 200 * 1024 * 1024;
    if (file.size > maxSize) {
      return { valid: false as const, error: `文件 "${file.name}" 超过上传限制 (最大 ${Math.round(maxSize / (1024 * 1024))}MB)` };
    }
    if (file.name === '.DS_Store') {
      return { valid: false as const, error: `系统文件已忽略: ${file.name}` };
    }
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const supportedExts = new Set(['pdf', 'doc', 'docx', 'ppt', 'pptx', 'jpg', 'jpeg', 'png', 'md']);
    if (!supportedExts.has(ext)) {
      return { valid: false as const, error: `不支持的文件格式: ${file.name}` };
    }
    return { valid: true as const };
  }, [state.mineruConfig.maxFileSize]);

  /**
   * 将文件列表加入上传队列并自动启动提交。
   * 仅 toast 提示文件数量，不弹出可见列表。
   * @param files - 待上传的 File 数组
   */
  const upload = useCallback(async (files: File[]) => {
    const list = Array.from(files || []);
    if (list.length === 0) return;

    try {
      const healthRes = await fetch('/__proxy/upload/ops/dependency-health');
      if (healthRes.ok) {
        const health = await healthRes.json();
        if (health.blocking) {
          const blockingDep = Object.keys(health.dependencies).find(k => 
            health.dependencies[k].ok === false && 
            health.dependencies[k].requiredFor?.includes('parse') && 
            !health.dependencies[k].skipped
          );
          
          let message = '核心依赖不健康，无法执行解析';
          if (blockingDep === 'mineru') message = 'MinerU API 未启动，请先启动本地解析服务';
          else if (blockingDep === 'minio') message = 'MinIO 存储未就绪，无法上传文件';
          
          toast.error(message, { description: '请根据顶部诊断提示启动相关服务' });
          return;
        }
      }
    } catch (e) {
      // 忽略前端 fetch 本身的错误，让后续流程继续（后端也有兜底）
    }

    const invalidFiles = list.filter((f) => !validateFile(f).valid);
    const validFiles = list.filter((f) => validateFile(f).valid);
    if (validFiles.length === 0) {
      if (invalidFiles.length > 0) {
        toast.error(`${invalidFiles.length} 个文件不符合上传规范，已过滤`);
      }
      return;
    }

    if (invalidFiles.length > 0) toast.error(`发现 ${invalidFiles.length} 个不符合规范的文件被过滤`);

    const now = Date.now();
    const items = validFiles.map((file, idx) => {
      const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      // 增强 ID 唯一性：时间戳 + 索引 + 随机串
      const id = `q-${now}-${idx}-${Math.random().toString(36).slice(2, 10)}`;
      return { id, file, filePath };
    });

    // 必须先注册内存句柄，再派发状态
    batchRegisterFiles(items.map((it) => ({ id: it.id, file: it.file })));

    // P1 Patch: 不打开 UI，openUi 固定为 false
    dispatch({
      type: 'BATCH_ADD_FILES',
      payload: {
        items: items.map((it) => ({
          id: it.id,
          fileName: it.file.name,
          fileSize: it.file.size,
          path: it.filePath,
        })),
        openUi: false,
      },
    });

    // Toast 提示：正在提交
    toast.info(`正在提交 ${validFiles.length} 个文件…`, {
      description: '任务状态请在「任务管理」页面查看',
      duration: 3000,
    });

    // 确保队列处于运行状态
    dispatch({ type: 'BATCH_SET_PAUSED', payload: { paused: false } });
    dispatch({ type: 'BATCH_SET_RUNNING', payload: { running: true } });
  }, [dispatch, validateFile]);

  const uploading = bp.running && !bp.paused;

  return { upload, uploading };
}
