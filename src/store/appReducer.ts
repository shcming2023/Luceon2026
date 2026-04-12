/**
 * 全局 Reducer
 * 处理所有 Action，返回新的 AppState
 * 所有状态变更均通过此函数统一管理
 */

import type { AppState, AppAction } from './types';

/**
 * 应用主 Reducer
 * @param state - 当前应用状态
 * @param action - 触发的 Action
 * @returns 新的应用状态（不可变更新）
 */
export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    // ==================== DB Hydration ====================

    /**
     * 从 db-server 全量覆盖内存状态（仅在应用启动时触发一次）
     * 只覆盖 payload 中非 undefined 的字段
     */
    case 'HYDRATE_FROM_DB': {
      const p = action.payload;
      return {
        ...state,
        ...(p.materials      !== undefined ? { materials:      p.materials }      : {}),
        ...(p.assetDetails   !== undefined ? { assetDetails:   p.assetDetails }   : {}),
        ...(p.processTasks   !== undefined ? { processTasks:   p.processTasks }   : {}),
        ...(p.tasks          !== undefined ? { tasks:          p.tasks }          : {}),
        ...(p.products       !== undefined ? { products:       p.products }       : {}),
        ...(p.batchProcessing !== undefined ? {
          batchProcessing: {
            ...p.batchProcessing,
            items: (p.batchProcessing.items ?? []).map((it) => ({
              ...it,
              updatedAt: (it as { updatedAt?: number }).updatedAt ?? it.createdAt ?? Date.now(),
            })),
          },
        } : {}),
        ...(p.flexibleTags   !== undefined ? { flexibleTags:   p.flexibleTags }   : {}),
        ...(p.aiRules        !== undefined ? { aiRules:        p.aiRules }        : {}),
        ...(p.aiRuleSettings !== undefined ? { aiRuleSettings: p.aiRuleSettings } : {}),
        ...(p.aiConfig       !== undefined ? { aiConfig:       p.aiConfig }       : {}),
        ...(p.mineruConfig   !== undefined ? { mineruConfig:   p.mineruConfig }   : {}),
        ...(p.minioConfig    !== undefined ? { minioConfig:    p.minioConfig }    : {}),
      };
    }

    // ==================== 批处理队列 ====================

    case 'BATCH_ADD_FILES': {
      const now = Date.now();
      const nextItems = action.payload.items.map((i) => ({
        id: i.id,
        fileName: i.fileName,
        fileSize: i.fileSize,
        path: i.path,
        status: 'pending' as const,
        progress: 0,
        createdAt: now,
        updatedAt: now,
      }));
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          items: [...state.batchProcessing.items, ...nextItems],
          ...(action.payload.openUi !== undefined ? { uiOpen: action.payload.openUi } : {}),
        },
      };
    }

    case 'BATCH_UPDATE_ITEM': {
      const { id, updates } = action.payload;
      const now = Date.now();
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          items: state.batchProcessing.items.map((item) =>
            item.id === id ? { ...item, ...updates, updatedAt: updates.updatedAt ?? now } : item,
          ),
        },
      };
    }

    case 'BATCH_REMOVE_ITEM': {
      const id = action.payload.id;
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          items: state.batchProcessing.items.filter((i) => i.id !== id),
        },
      };
    }

    case 'BATCH_CLEAR': {
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          items: [],
          running: false,
          paused: false,
        },
      };
    }

    case 'BATCH_SET_RUNNING': {
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          running: action.payload.running,
        },
      };
    }

    case 'BATCH_SET_PAUSED': {
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          paused: action.payload.paused,
        },
      };
    }

    case 'BATCH_SET_UI_OPEN': {
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          uiOpen: action.payload.uiOpen,
        },
      };
    }

    case 'BATCH_SET_OPTIONS': {
      return {
        ...state,
        batchProcessing: {
          ...state.batchProcessing,
          ...action.payload,
        },
      };
    }

    // ==================== 资料操作 ====================

    /**
     * 删除资料（支持批量）
     * 同时清理 materials 数组和 assetDetails 中的对应记录
     */
    case 'DELETE_MATERIAL': {
      const idSet = new Set(action.payload);
      const newAssetDetails = { ...state.assetDetails };
      action.payload.forEach((id) => delete newAssetDetails[id]);
      return {
        ...state,
        materials: state.materials.filter((m) => !idSet.has(m.id)),
        assetDetails: newAssetDetails,
        // 级联清理关联的处理任务（孤儿任务防护：无 materialId 的任务保留）
        processTasks: state.processTasks.filter(
          (t) => t.materialId === undefined || !idSet.has(t.materialId),
        ),
        products: state.products.filter(
          (product) =>
            !idSet.has(Number(String(product.source || '').replace(/^material:/, ''))) &&
            !(product.lineage || []).some((lineageId) => idSet.has(Number(lineageId))),
        ),
      };
    }

    /**
     * 新增原始资料
     * 将新资料插入列表头部（最新优先）
     */
    case 'ADD_MATERIAL': {
      const m = action.payload;
      const newAssetDetail = {
        id: m.id,
        title: m.title,
        status: m.status,
        assetId: `MAT-${m.id}`,
        tags: m.tags,
        metadata: {
          subject: m.metadata?.subject || '-',
          grade: m.metadata?.grade || '-',
          standard: m.metadata?.standard || '-',
          region: m.metadata?.region || '-',
          format: m.type || '-',
          size: m.size || '-',
          pages: '-',
          language: '中文',
          uploadTime: m.uploadTime || '刚刚',
          uploader: m.uploader || '管理员',
          summary: m.metadata?.summary || '',
          previewUrl: m.previewUrl || '',
        },
        relatedAssets: [],
      };
      return {
        ...state,
        materials: [m, ...state.materials],
        assetDetails: {
          ...state.assetDetails,
          [m.id]: newAssetDetail,
        },
      };
    }

    /**
     * 更新单个资料信息（支持部分更新）
     * 同步更新 assetDetails 中的对应记录（#2）
     */
    case 'UPDATE_MATERIAL': {
      const { id, updates } = action.payload;
      const newMaterials = state.materials.map((m) =>
        m.id === id
          ? {
              ...m,
              ...updates,
              ...(updates.metadata ? { metadata: { ...m.metadata, ...updates.metadata } } : {}),
            }
          : m,
      );

      // 同步更新 assetDetails（参照 UPDATE_MATERIAL_AI_STATUS 的联动写法）
      const existingDetail = state.assetDetails[id];
      const updatedDetails = existingDetail
        ? {
            ...state.assetDetails,
            [id]: {
              ...existingDetail,
              ...(updates.title !== undefined ? { title: updates.title } : {}),
              ...(updates.status !== undefined ? { status: updates.status } : {}),
              ...(updates.tags !== undefined ? { tags: updates.tags } : {}),
              ...(updates.metadata
                ? { metadata: { ...existingDetail.metadata, ...updates.metadata } as Record<string, string | number> }
                : {}),
            },
          }
        : state.assetDetails;

      return { ...state, materials: newMaterials, assetDetails: updatedDetails };
    }

    /**
     * 批量为资料添加标签
     * 对指定 ID 列表中的资料，合并去重后追加新标签
     * 同步更新 assetDetails[id].tags（#3）
     */
    case 'BATCH_ADD_TAGS': {
      const newAssetDetails = { ...state.assetDetails };
      const newMaterials = state.materials.map((m) => {
        if (!action.payload.ids.includes(m.id)) return m;
        const newTags = [...new Set([...m.tags, ...action.payload.tags])];
        if (newAssetDetails[m.id]) {
          newAssetDetails[m.id] = { ...newAssetDetails[m.id], tags: newTags };
        }
        return { ...m, tags: newTags };
      });
      return { ...state, materials: newMaterials, assetDetails: newAssetDetails };
    }

    /**
     * 更新单个资料的标签列表
     * 同步更新 assetDetails[id].tags（#3）
     */
    case 'UPDATE_MATERIAL_TAGS': {
      const { id, tags } = action.payload;
      const existingDetail = state.assetDetails[id];
      return {
        ...state,
        materials: state.materials.map((m) =>
          m.id === id ? { ...m, tags } : m,
        ),
        assetDetails: existingDetail
          ? { ...state.assetDetails, [id]: { ...existingDetail, tags } }
          : state.assetDetails,
      };
    }

    /**
     * 更新单个资料 AI 分析状态（可选同步更新主状态 + 回填 AI 识别结果）
     */
    case 'UPDATE_MATERIAL_AI_STATUS': {
      const { id, aiStatus, status, tags, metadata, title } = action.payload;
      const newMaterials = state.materials.map((m) =>
        m.id === id
          ? {
              ...m,
              aiStatus,
              ...(status ? { status } : {}),
              ...(tags ? { tags } : {}),
              ...(metadata ? { metadata: { ...m.metadata, ...metadata } } : {}),
              ...(title ? { title } : {}),
              ...(aiStatus === 'analyzed' || aiStatus === 'failed'
                ? { aiCompletedAt: Date.now() }
                : {}),
            }
          : m,
      );

      // 同步更新 assetDetails 中的对应记录
      const existingDetail = state.assetDetails[id];
      const updatedDetails = existingDetail
        ? {
            ...state.assetDetails,
            [id]: {
              ...existingDetail,
              ...(title ? { title } : {}),
              ...(status ? { status } : {}),
              ...(tags ? { tags } : {}),
              metadata: {
                ...existingDetail.metadata,
                ...(metadata?.subject ? { subject: metadata.subject } : {}),
                ...(metadata?.grade ? { grade: metadata.grade } : {}),
                ...(metadata?.standard ? { standard: metadata.standard } : {}),
                ...(metadata?.type ? { type: metadata.type } : {}),
                ...(metadata?.summary ? { summary: metadata.summary } : {}),
              },
            },
          }
        : state.assetDetails;

      return { ...state, materials: newMaterials, assetDetails: updatedDetails };
    }

    /**
     * 更新单个资料 MinerU 解析状态（可级联更新 AI 状态和主状态）
     */
    case 'UPDATE_MATERIAL_MINERU_STATUS':
      return {
        ...state,
        materials: state.materials.map((m) =>
          m.id === action.payload.id
            ? {
                ...m,
                mineruStatus: action.payload.mineruStatus,
                ...(action.payload.aiStatus ? { aiStatus: action.payload.aiStatus } : {}),
                ...(action.payload.status ? { status: action.payload.status } : {}),
                ...(action.payload.mineruCompletedAt ? { mineruCompletedAt: action.payload.mineruCompletedAt } : {}),
              }
            : m,
        ),
      };

    // ==================== 处理任务操作 ====================

    /**
     * 新增处理任务
     * 将新任务插入列表头部（最新优先）
     */
    case 'ADD_PROCESS_TASK': {
      const task = action.payload;
      return {
        ...state,
        processTasks: [task, ...state.processTasks],
      };
    }

    /**
     * 更新处理任务（可更新状态和关联的 materialId）
     * 如果任务状态变为 completed 且有关联的 materialId，则同时更新对应 material 的状态
     */
    case 'UPDATE_PROCESS_TASK': {
      const { id, status, materialId } = action.payload;
      
      // 更新 processTasks
      const updatedTasks = state.processTasks.map((t) =>
        t.id === id
          ? {
              ...t,
              status,
              ...(materialId !== undefined ? { materialId } : {}),
            }
          : t,
      );

      // 如果任务完成且有关联的 materialId，则更新对应的 material 状态
      let updatedMaterials = state.materials;
      if (status === 'completed' && materialId !== undefined) {
        updatedMaterials = state.materials.map((m) =>
          m.id === materialId ? { ...m, status: 'completed' } : m,
        );
        
        // 同时更新 assetDetails 中的状态
        const updatedDetails = state.assetDetails[materialId]
          ? {
              ...state.assetDetails,
              [materialId]: {
                ...state.assetDetails[materialId],
                status: 'completed' as const,
              },
            }
          : state.assetDetails;

        return {
          ...state,
          processTasks: updatedTasks,
          materials: updatedMaterials,
          assetDetails: updatedDetails,
        };
      }

      return {
        ...state,
        processTasks: updatedTasks,
        ...(updatedMaterials !== state.materials ? { materials: updatedMaterials } : {}),
      };
    }

    /**
     * 更新处理中心任务状态
     * reviewing → completed/failed，processing → pending，pending → processing
     */
    case 'UPDATE_PROCESS_TASK_STATUS':
      return {
        ...state,
        processTasks: state.processTasks.map((t) =>
          t.id === action.payload.id ? { ...t, status: action.payload.status } : t,
        ),
      };

    // ==================== 任务中心操作 ====================

    /**
     * 更新任务中心任务状态
     */
    case 'UPDATE_TASK_STATUS':
      return {
        ...state,
        tasks: state.tasks.map((t) =>
          t.id === action.payload.id ? { ...t, status: action.payload.status } : t,
        ),
      };

    // ==================== AI 规则操作 ====================

    /**
     * 切换 AI 规则的启用/禁用状态
     */
    case 'TOGGLE_AI_RULE':
      return {
        ...state,
        aiRules: state.aiRules.map((r) =>
          r.id === action.payload.id ? { ...r, enabled: !r.enabled } : r,
        ),
      };

    /**
     * 更新 AI 规则执行设置（部分更新）
     */
    case 'UPDATE_AI_RULE_SETTINGS':
      return {
        ...state,
        aiRuleSettings: { ...state.aiRuleSettings, ...action.payload },
      };

    /**
     * 更新 AI 识别配置（部分更新）
     */
    case 'UPDATE_AI_CONFIG':
      return {
        ...state,
        aiConfig: { ...state.aiConfig, ...action.payload },
      };

    /**
     * 更新 MinerU API 配置（部分更新）
     */
    case 'UPDATE_MINERU_CONFIG':
      return {
        ...state,
        mineruConfig: { ...state.mineruConfig, ...action.payload },
      };

    /**
     * 更新 MinIO 存储配置（部分更新）
     */
    case 'UPDATE_MINIO_CONFIG':
      return {
        ...state,
        minioConfig: { ...state.minioConfig, ...action.payload },
      };

    // ==================== 资产详情操作 ====================

    /**
     * 更新资产标签列表
     * 若 assetDetails[id] 不存在则直接返回原 state，不创建残缺对象（#10）
     */
    case 'UPDATE_ASSET_TAGS': {
      const { id, tags } = action.payload;
      if (!state.assetDetails[id]) return state;
      return {
        ...state,
        assetDetails: {
          ...state.assetDetails,
          [id]: {
            ...state.assetDetails[id],
            tags,
          },
        },
      };
    }

    /**
     * 更新资料预览 URL（本地 blob URL 或远程公开 URL）
     */
    case 'UPDATE_MATERIAL_PREVIEW_URL':
      return {
        ...state,
        materials: state.materials.map((m) =>
          m.id === action.payload.id ? { ...m, previewUrl: action.payload.previewUrl } : m,
        ),
        assetDetails: state.assetDetails[action.payload.id]
          ? {
              ...state.assetDetails,
              [action.payload.id]: {
                ...state.assetDetails[action.payload.id],
                metadata: {
                  ...state.assetDetails[action.payload.id].metadata,
                  previewUrl: action.payload.previewUrl,
                },
              },
            }
          : state.assetDetails,
      };

    /**
     * 更新资料 MinerU 解析后的 ZIP 下载链接
     */
    case 'UPDATE_MATERIAL_MINERU_ZIP_URL':
      return {
        ...state,
        materials: state.materials.map((m) =>
          m.id === action.payload.id ? { ...m, mineruZipUrl: action.payload.mineruZipUrl } : m,
        ),
      };

    // ==================== 删除操作 ====================

    /**
     * 新增成品
     */
    case 'ADD_PRODUCT':
      return {
        ...state,
        products: [action.payload, ...state.products],
      };

    /**
     * 删除成品（支持批量）
     */
    case 'DELETE_PRODUCT': {
      const idSet = new Set(action.payload);
      return { ...state, products: state.products.filter((p) => !idSet.has(p.id)) };
    }

    /**
     * 删除灵活标签（支持批量）
     */
    case 'DELETE_FLEXIBLE_TAG': {
      const idSet = new Set(action.payload);
      return { ...state, flexibleTags: state.flexibleTags.filter((t) => !idSet.has(t.id)) };
    }

    /**
     * 删除 AI 标注规则（支持批量）
     */
    case 'DELETE_AI_RULE': {
      const idSet = new Set(action.payload);
      return { ...state, aiRules: state.aiRules.filter((r) => !idSet.has(r.id)) };
    }

    default:
      return state;
  }
}
