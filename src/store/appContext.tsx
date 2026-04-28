/**
 * 全局应用 Context
 *
 * 数据持久化策略（三级降级）：
 *   1. 主存储：db-server（通过 /__proxy/db/ REST API，JSON 文件持久化）
 *   2. 次存储：localStorage（降级兜底，同步写）
 *   3. 内存 fallback：mockData 初始数据（全量 API 失败时）
 *
 * 启动流程：
 *   a. 先用 localStorage 快速渲染（避免白屏）
 *   b. 异步请求 db-server API
 *   c. 若 API 数据存在，以 db-server 数据覆盖 localStorage 并更新 state
 *   d. 若 db-server 库为空，则将当前内存数据 bulk-restore 写入 db-server
 *
 * 写操作：
 *   - reducer 照常更新内存 state
 *   - useEffect 监听各 state 切片 → 同步写 localStorage（防丢失）
 *   - useEffect 监听各 state 切片 → 异步 fire-and-forget 写 db-server（不阻塞 UI）
 */

import React, {
  createContext, useContext, useReducer, useEffect, useRef, useState,
} from 'react';
import { toast } from 'sonner';
import type { AppState, AppAction, AiConfig, MinerUConfig, MinioConfig, Material, AssetDetail, ProcessTask, Task, Product, FlexibleTag, AiRule, AiRuleSettings } from './types';
import { appReducer } from './appReducer';
import {
  initialMaterials,
  initialProcessTasks,
  initialTasks,
  initialProducts,
  initialBatchProcessing,
  initialFlexibleTags,
  initialAiRules,
  initialAiRuleSettings,
  initialAiConfig,
  initialMinerUConfig,
  initialMinioConfig,
  initialAssetDetails,
} from './mockData';

// ─── API 基础路径 ──────────────────────────────────────────────
const DB_BASE = '/__proxy/db';

// ─── localStorage keys ────────────────────────────────────────
const LS = {
  AI:             'app_ai_config',
  MINERU:         'app_mineru_config',
  MINIO:          'app_minio_config',
  MATERIALS:      'app_materials',
  PROCESS_TASKS:  'app_process_tasks',
  TASKS:          'app_tasks',
  PRODUCTS:       'app_products',
  BATCH_PROCESSING: 'app_batch_processing',
  ASSET_DETAILS:  'app_asset_details',
  FLEXIBLE_TAGS:  'app_flexible_tags',
  AI_RULES:       'app_ai_rules',
  AI_RULE_SETTINGS: 'app_ai_rule_settings',
};

// ─── localStorage 工具 ────────────────────────────────────────

function loadFromStorage<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (Array.isArray(fallback)) {
      if (Array.isArray(parsed)) return parsed as T;
      if (parsed && typeof parsed === 'object') return Object.values(parsed) as T;
      return fallback;
    }
    if (fallback && typeof fallback === 'object' && !Array.isArray(fallback)
        && parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return { ...fallback, ...parsed } as T;
    }
    return parsed as T;
  } catch {
    return fallback;
  }
}

function saveToStorage<T>(key: string, value: T) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

function sanitizeMaterialMetadataForPersistence(metadata: Material['metadata']): Material['metadata'] {
  const next = { ...metadata };
  if (next.provider === 'minio' && next.objectName) delete next.fileUrl;
  if (next.markdownObjectName) delete next.markdownUrl;
  return next;
}

function sanitizeMaterialForPersistence(material: Material): Material {
  return {
    ...material,
    metadata: sanitizeMaterialMetadataForPersistence(material.metadata),
    ...(material.previewUrl?.startsWith('blob:') ? { previewUrl: '' } : {}),
  };
}

function shouldPersistMaterial(material: Material) {
  const meta = material.metadata || {};
  if ((meta as { localDraft?: unknown }).localDraft === true) return false;
  const objectName = typeof meta.objectName === 'string' ? meta.objectName.trim() : '';
  if (material.status === 'processing' && !objectName) return false;
  return true;
}

function sanitizeAssetDetailForPersistence(detail: AssetDetail): AssetDetail {
  const metadata = { ...detail.metadata };
  if (metadata.provider === 'minio' && typeof metadata.objectName === 'string' && metadata.objectName) {
    delete metadata.fileUrl;
  }
  if (typeof metadata.markdownObjectName === 'string' && metadata.markdownObjectName) {
    delete metadata.markdownUrl;
  }
  if (typeof metadata.previewUrl === 'string' && metadata.previewUrl.startsWith('blob:')) {
    delete metadata.previewUrl;
  }
  return {
    ...detail,
    metadata,
  };
}

function sanitizeAssetDetailsForPersistence(details: Record<number, AssetDetail>) {
  return Object.fromEntries(
    Object.entries(details).map(([id, detail]) => [id, sanitizeAssetDetailForPersistence(detail)]),
  ) as Record<number, AssetDetail>;
}

function sanitizeAiConfigForLocalStorage(config: AiConfig): AiConfig {
  const providers = Array.isArray(config.providers)
    ? config.providers.map((p) => ({ ...p, apiKey: '' }))
    : config.providers;
  return {
    ...config,
    apiKey: '',
    ...(providers ? { providers } : {}),
  };
}

function sanitizeMinerUConfigForLocalStorage(config: MinerUConfig): MinerUConfig {
  return {
    ...config,
    apiKey: '',
  };
}

function sanitizeMinioConfigForLocalStorage(config: MinioConfig): MinioConfig {
  return {
    ...config,
    accessKey: '',
    secretKey: '',
  };
}

function extractSecretsPayload(configs: { aiConfig: AiConfig; mineruConfig: MinerUConfig; minioConfig: MinioConfig }) {
  const payload: Record<string, string | null> = {};

  if (typeof configs.aiConfig.apiKey === 'string') {
    const v = configs.aiConfig.apiKey.trim();
    payload.aiConfig_apiKey = v ? v : null;
  }
  if (Array.isArray(configs.aiConfig.providers)) {
    for (const p of configs.aiConfig.providers) {
      if (!p || typeof p !== 'object') continue;
      const id = String((p as { id?: unknown }).id || '').trim();
      if (!id) continue;
      const apiKey = String((p as { apiKey?: unknown }).apiKey || '').trim();
      payload[`aiProvider_${id}_apiKey`] = apiKey ? apiKey : null;
    }
  }

  if (typeof configs.mineruConfig.apiKey === 'string') {
    const v = configs.mineruConfig.apiKey.trim();
    payload.mineru_apiKey = v ? v : null;
  }
  if (typeof configs.minioConfig.accessKey === 'string') {
    const v = configs.minioConfig.accessKey.trim();
    payload.minio_accessKey = v ? v : null;
  }
  if (typeof configs.minioConfig.secretKey === 'string') {
    const v = configs.minioConfig.secretKey.trim();
    payload.minio_secretKey = v ? v : null;
  }

  return payload;
}

function applySecretsToConfigs(input: { aiConfig: AiConfig; mineruConfig: MinerUConfig; minioConfig: MinioConfig }, secrets: Record<string, unknown>) {
  const aiConfig = { ...input.aiConfig };
  const mineruConfig = { ...input.mineruConfig };
  const minioConfig = { ...input.minioConfig };

  if (typeof secrets.aiConfig_apiKey === 'string') aiConfig.apiKey = secrets.aiConfig_apiKey;
  if (Array.isArray(aiConfig.providers)) {
    aiConfig.providers = aiConfig.providers.map((p) => {
      const id = String(p.id || '').trim();
      const key = `aiProvider_${id}_apiKey`;
      if (id && typeof secrets[key] === 'string') return { ...p, apiKey: secrets[key] as string };
      return p;
    });
  }

  if (typeof secrets.mineru_apiKey === 'string') mineruConfig.apiKey = secrets.mineru_apiKey;
  if (typeof secrets.minio_accessKey === 'string') minioConfig.accessKey = secrets.minio_accessKey;
  if (typeof secrets.minio_secretKey === 'string') minioConfig.secretKey = secrets.minio_secretKey;

  return { aiConfig, mineruConfig, minioConfig };
}

function mergeConfigWithFallback<T extends Record<string, unknown>>(fallback: T, value: unknown): T {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fallback;
  const result: Record<string, unknown> = { ...fallback };
  for (const key of Object.keys(fallback)) {
    const fb = fallback[key];
    const cur = (value as Record<string, unknown>)[key];
    if (typeof fb === 'string') {
      result[key] = typeof cur === 'string' && cur.trim() !== '' ? cur : fb;
    } else if (fb && typeof fb === 'object' && !Array.isArray(fb)) {
      result[key] = mergeConfigWithFallback(fb as Record<string, unknown>, cur);
    } else {
      result[key] = cur ?? fb;
    }
  }
  return result as T;
}

function loadConfigFromStorage<T extends Record<string, unknown>>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return mergeConfigWithFallback(fallback, JSON.parse(raw));
  } catch {
    return fallback;
  }
}

// ─── db-server API 工具（带失败提示与重试）───────────────────────

// 连续失败计数器，避免频繁弹窗骚扰用户
let dbFailCount = 0;
const DB_FAIL_TOAST_THRESHOLD = 3; // 连续失败 N 次后才弹窗提示
let dbFailToastShown = false;

function handleDbWriteError(operation: string, err: unknown) {
  dbFailCount++;
  const msg = err instanceof Error ? err.message : String(err);
  console.warn(`[db-sync] ${operation} failed (count=${dbFailCount}):`, msg);
  
  const isTimeout = msg.toLowerCase().includes('timeout') || msg.toLowerCase().includes('abort');
  if (isTimeout) return; // 单次/多次 timeout 仅控制台记录，不强提示

  if (dbFailCount >= 5 && !dbFailToastShown) {
    dbFailToastShown = true;
    toast.warning(`数据同步服务连接异常（${operation} ${msg}），已保存到本地缓存。`, { duration: 5000 });
    setTimeout(() => { dbFailToastShown = false; }, 60000);
  }
}

function handleDbWriteSuccess() {
  if (dbFailCount > 0) {
    dbFailCount = 0;
    if (dbFailToastShown) {
      dbFailToastShown = false;
      toast.success('数据同步服务已恢复连接。', { duration: 3000 });
    }
  }
}

async function dbGet<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${DB_BASE}${path}`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) return null;
    return await res.json() as T;
  } catch {
    return null;
  }
}

async function dbPut(path: string, body: unknown): Promise<void> {
  try {
    const res = await fetch(`${DB_BASE}${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    handleDbWriteSuccess();
  } catch (err) { handleDbWriteError(`PUT ${path}`, err); }
}

async function dbPatch(path: string, body: unknown): Promise<void> {
  try {
    const res = await fetch(`${DB_BASE}${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    handleDbWriteSuccess();
  } catch (err) { handleDbWriteError(`PATCH ${path}`, err); }
}

async function dbPost(path: string, body: unknown): Promise<void> {
  try {
    const res = await fetch(`${DB_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    handleDbWriteSuccess();
  } catch (err) { handleDbWriteError(`POST ${path}`, err); }
}

async function dbDelete(path: string, body: unknown): Promise<void> {
  try {
    const res = await fetch(`${DB_BASE}${path}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    handleDbWriteSuccess();
  } catch (err) { handleDbWriteError(`DELETE ${path}`, err); }
}

// ─── 初始状态（先用 localStorage，等 SQLite 加载完再覆盖）─────

const initialState: AppState = {
  materials:        loadFromStorage<Material[]>(LS.MATERIALS, initialMaterials),
  processTasks:     loadFromStorage(LS.PROCESS_TASKS, initialProcessTasks),
  tasks:            loadFromStorage(LS.TASKS, initialTasks),
  products:         loadFromStorage(LS.PRODUCTS, initialProducts),
  batchProcessing:  initialBatchProcessing, // P1 Patch: 不再从 localStorage 恢复，始终空队列
  flexibleTags:     loadFromStorage(LS.FLEXIBLE_TAGS, initialFlexibleTags),
  aiRules:          loadFromStorage(LS.AI_RULES, initialAiRules),
  aiRuleSettings:   loadFromStorage(LS.AI_RULE_SETTINGS, initialAiRuleSettings),
  aiConfig:         loadConfigFromStorage<AiConfig>(LS.AI, initialAiConfig),
  mineruConfig:     loadConfigFromStorage<MinerUConfig>(LS.MINERU, initialMinerUConfig),
  minioConfig:      loadConfigFromStorage<MinioConfig>(LS.MINIO, initialMinioConfig),
  assetDetails:     loadFromStorage(LS.ASSET_DETAILS, initialAssetDetails),
  _dataSource:      'localStorage',
};

// ─── Context ──────────────────────────────────────────────────

interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  /** db-server 加载是否完成（可用于显示加载指示） */
  dbReady: boolean;
}

const AppContext = createContext<AppContextValue | null>(null);

// ─── Provider ─────────────────────────────────────────────────

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  const [dbReady, setDbReady] = useState(false);

  // 防止 db-server 写操作在 hydration 完成之前触发（初次加载时跳过写操作）
  const hydratedRef = useRef(false);

  // ── 启动：从 db-server 加载数据（一次性，挂载后执行）──────────
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        // 并行请求所有数据
        const [
          materials,
          assetDetails,
          processTasks,
          tasks,
          products,
          flexibleTags,
          aiRules,
          settings,
          secrets,
        ] = await Promise.all([
          dbGet<Material[]>('/materials'),
          dbGet<Record<number, AssetDetail>>('/asset-details'),
          dbGet<ProcessTask[]>('/process-tasks'),
          dbGet<Task[]>('/tasks'),
          dbGet<Product[]>('/products'),
          dbGet<FlexibleTag[]>('/flexible-tags'),
          dbGet<AiRule[]>('/ai-rules'),
          dbGet<Record<string, unknown>>('/settings'),
          dbGet<{ secrets: Record<string, unknown> }>('/secrets').catch(() => ({ secrets: {} })),
        ]);

        if (cancelled) return;

        // 判断 DB 是否已完成过初始化（通过 settings.initialized 标记）
        const isDbInitialized = settings?.initialized === true;
        const hasMaterials = Array.isArray(materials) && materials.length > 0;

        if (hasMaterials || isDbInitialized) {
          const mergedAi = settings?.aiConfig ? mergeConfigWithFallback(initialAiConfig, settings.aiConfig) : initialAiConfig;
          const mergedMineru = settings?.mineruConfig ? mergeConfigWithFallback(initialMinerUConfig, settings.mineruConfig) : initialMinerUConfig;
          const mergedMinio = settings?.minioConfig ? mergeConfigWithFallback(initialMinioConfig, settings.minioConfig) : initialMinioConfig;
          const withSecrets = applySecretsToConfigs({ aiConfig: mergedAi, mineruConfig: mergedMineru, minioConfig: mergedMinio }, secrets?.secrets ?? {});
          // DB 已初始化（有数据 or 有初始化标记）：直接用 DB 数据覆盖内存 state
          dispatch({
            type: 'HYDRATE_FROM_DB',
            payload: {
              materials:      Array.isArray(materials) ? materials : [],
              assetDetails:   assetDetails ?? undefined,
              processTasks:   processTasks ?? undefined,
              tasks:          tasks ?? undefined,
              products:       products ?? undefined,
              // P1 Patch: batchProcessing 不再从 DB 恢复（由 reducer HYDRATE_FROM_DB 强制清空）
              flexibleTags:   flexibleTags ?? undefined,
              aiRules:        aiRules ?? undefined,
              aiRuleSettings: (settings?.aiRuleSettings as AiRuleSettings | undefined) ?? undefined,
              aiConfig:       withSecrets.aiConfig,
              mineruConfig:   withSecrets.mineruConfig,
              minioConfig:    withSecrets.minioConfig,
              _dataSource:    'db-server',
            },
          });
          console.log(`[appContext] Hydrated from DB (${materials?.length ?? 0} materials, initialized=${isDbInitialized})`);
        } else {
          // DB 从未初始化过（全新部署）：将当前内存数据 seed 写入 DB，并打标记
          console.log('[appContext] DB not initialized, checking idempotency...');

          // 幂等性保护：双重检查当前 DB 中 materials 是否已有数据
          try {
            const materialsResp = await fetch(`${DB_BASE}/materials`);
            if (materialsResp.ok) {
              const existingMaterials = await materialsResp.json();
              if (Array.isArray(existingMaterials) && existingMaterials.length > 0) {
                console.log('[appContext] DB already has data, marking initialized directly');
                await fetch(`${DB_BASE}/settings/initialized`, {
                  method: 'PUT',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(true),
                });
                return;
              }
            }
          } catch (e) {
            console.warn('[appContext] Idempotency check failed:', e);
          }

          console.log('[appContext] Seeding from current state...');
          await dbPost('/bulk-restore', {
            materials:      state.materials.map(sanitizeMaterialForPersistence),
            assetDetails:   sanitizeAssetDetailsForPersistence(state.assetDetails),
            processTasks:   state.processTasks,
            tasks:          state.tasks,
            products:       state.products,
            // P1 Patch: batchProcessing 不再 seed 到 DB
            flexibleTags:   state.flexibleTags,
            aiRules:        state.aiRules,
            aiRuleSettings: state.aiRuleSettings,
            aiConfig:       sanitizeAiConfigForLocalStorage(state.aiConfig),
            mineruConfig:   sanitizeMinerUConfigForLocalStorage(state.mineruConfig),
            minioConfig:    sanitizeMinioConfigForLocalStorage(state.minioConfig),
          });
          await dbPut('/secrets', extractSecretsPayload({ aiConfig: state.aiConfig, mineruConfig: state.mineruConfig, minioConfig: state.minioConfig }));
          // 打初始化标记，后续刷新不再 seed
          // 必须使用 await fetch 等待确认，不能 fire-and-forget
          await fetch(`${DB_BASE}/settings/initialized`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(true),
          });
          console.log('[appContext] DB seeded and marked as initialized');
        }
      } catch (err) {
        console.warn('[appContext] db-server hydration failed, using localStorage fallback:', err);
      } finally {
        if (!cancelled) {
          hydratedRef.current = true;
          setDbReady(true);
        }
      }
    })();

    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // 仅挂载时执行一次

  // ── 用 useRef 追踪上一次的 id 集合，用于检测删除 ───────────
  const prevMaterialIds   = useRef<Set<number>>(new Set(state.materials.map((m) => m.id)));
  // 保存上一轮完整 materials，用于删除时提取 metadata（删除后 state.materials 已不含被删项）
  const prevMaterialsRef  = useRef<typeof state.materials>(state.materials);
  const prevAssetIds      = useRef<Set<number>>(new Set(Object.keys(state.assetDetails).map(Number)));
  const prevProcessIds    = useRef<Set<number>>(new Set(state.processTasks.map((t) => t.id)));
  const prevTaskIds       = useRef<Set<string>>(new Set(state.tasks.map((t) => t.id)));
  const prevProductIds    = useRef<Set<number>>(new Set(state.products.map((p) => p.id)));
  const prevTagIds        = useRef<Set<number>>(new Set(state.flexibleTags.map((t) => t.id)));
  const prevAiRuleIds     = useRef<Set<number>>(new Set(state.aiRules.map((r) => r.id)));

  // ── 差量指纹：仅对内容变化的记录执行 upsert（#7）──────────
  const materialFingerprintsRef  = useRef<Map<number, string>>(new Map());
  const assetDetailFingerprintsRef = useRef<Map<string, string>>(new Map());
  const processTaskFingerprintsRef = useRef<Map<number, string>>(new Map());
  const taskFingerprintsRef        = useRef<Map<string, string>>(new Map());
  const productFingerprintsRef     = useRef<Map<number, string>>(new Map());
  const tagFingerprintsRef         = useRef<Map<number, string>>(new Map());
  const aiRuleFingerprintsRef      = useRef<Map<number, string>>(new Map());

  // ── 持久化：localStorage（同步）+ db-server（异步）────────────

  useEffect(() => {
    const materialsForPersistence = state.materials.filter(shouldPersistMaterial).map(sanitizeMaterialForPersistence);
    saveToStorage(LS.MATERIALS, materialsForPersistence);

    const currentIds = new Set(state.materials.map((m) => m.id));

    if (hydratedRef.current) {
      // 差量 upsert：仅对内容指纹变化的记录执行 POST（#7）
      for (const m of state.materials) {
        if (!shouldPersistMaterial(m)) continue;
        const sanitized = sanitizeMaterialForPersistence(m);
        const fingerprint = JSON.stringify(sanitized);
        if (materialFingerprintsRef.current.get(m.id) !== fingerprint) {
          materialFingerprintsRef.current.set(m.id, fingerprint);
          dbPost('/materials', sanitized);
        }
      }
    }

    // 无论是否 hydrated，始终更新 prevMaterialIds / prevMaterialsRef，确保后续删除比对正确
    prevMaterialIds.current = currentIds;
    prevMaterialsRef.current = state.materials;
  }, [state.materials]);

  useEffect(() => {
    const assetDetailsForPersistence = sanitizeAssetDetailsForPersistence(state.assetDetails);
    saveToStorage(LS.ASSET_DETAILS, assetDetailsForPersistence);

    const currentIds = new Set(Object.keys(state.assetDetails).map(Number));

    if (hydratedRef.current) {
      // 差量 upsert：仅对内容指纹变化的记录执行 PUT（#7）
      for (const [id, detail] of Object.entries(assetDetailsForPersistence)) {
        const fingerprint = JSON.stringify(detail);
        if (assetDetailFingerprintsRef.current.get(id) !== fingerprint) {
          assetDetailFingerprintsRef.current.set(id, fingerprint);
          dbPut(`/asset-details/${id}`, detail);
        }
      }
    }

    prevAssetIds.current = currentIds;
  }, [state.assetDetails]);

  useEffect(() => {
    saveToStorage(LS.PROCESS_TASKS, state.processTasks);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.processTasks.map((t) => t.id));
    const deletedIds = [...prevProcessIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      dbDelete('/process-tasks', { ids: deletedIds });
    }
    for (const t of state.processTasks) {
      const fp = JSON.stringify(t);
      if (processTaskFingerprintsRef.current.get(t.id) !== fp) {
        processTaskFingerprintsRef.current.set(t.id, fp);
        dbPost('/process-tasks', t);
      }
    }
    prevProcessIds.current = currentIds;
  }, [state.processTasks]);

  useEffect(() => {
    saveToStorage(LS.TASKS, state.tasks);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.tasks.map((t) => t.id));
    // 检测删除：上一轮有、这一轮没有的 id 需要从 db-server 删除
    const deletedIds = [...prevTaskIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      dbDelete('/tasks', { ids: deletedIds });
    }
    // 差量 upsert：仅对内容指纹变化的记录执行 POST
    for (const t of state.tasks) {
      const fp = JSON.stringify(t);
      if (taskFingerprintsRef.current.get(t.id) !== fp) {
        taskFingerprintsRef.current.set(t.id, fp);
        dbPost('/tasks', t);
      }
    }
    prevTaskIds.current = currentIds;
  }, [state.tasks]);

  useEffect(() => {
    saveToStorage(LS.PRODUCTS, state.products);

    const currentIds = new Set(state.products.map((p) => p.id));

    if (hydratedRef.current) {
      const deletedIds = [...prevProductIds.current].filter((id) => !currentIds.has(id));
      if (deletedIds.length > 0) {
        dbDelete('/products', { ids: deletedIds });
      }
      for (const p of state.products) {
        const fp = JSON.stringify(p);
        if (productFingerprintsRef.current.get(p.id) !== fp) {
          productFingerprintsRef.current.set(p.id, fp);
          dbPost('/products', p);
        }
      }
    }

    prevProductIds.current = currentIds;
  }, [state.products]);

  useEffect(() => {
    saveToStorage(LS.FLEXIBLE_TAGS, state.flexibleTags);

    const currentIds = new Set(state.flexibleTags.map((t) => t.id));

    if (hydratedRef.current) {
      const deletedIds = [...prevTagIds.current].filter((id) => !currentIds.has(id));
      if (deletedIds.length > 0) {
        dbDelete('/flexible-tags', { ids: deletedIds });
      }
      for (const tag of state.flexibleTags) {
        const fp = JSON.stringify(tag);
        if (tagFingerprintsRef.current.get(tag.id) !== fp) {
          tagFingerprintsRef.current.set(tag.id, fp);
          dbPost('/flexible-tags', tag);
        }
      }
    }

    prevTagIds.current = currentIds;
  }, [state.flexibleTags]);

  useEffect(() => {
    saveToStorage(LS.AI_RULES, state.aiRules);

    const currentIds = new Set(state.aiRules.map((r) => r.id));

    if (hydratedRef.current) {
      const deletedIds = [...prevAiRuleIds.current].filter((id) => !currentIds.has(id));
      if (deletedIds.length > 0) {
        dbDelete('/ai-rules', { ids: deletedIds });
      }
      for (const rule of state.aiRules) {
        const fp = JSON.stringify(rule);
        if (aiRuleFingerprintsRef.current.get(rule.id) !== fp) {
          aiRuleFingerprintsRef.current.set(rule.id, fp);
          dbPost('/ai-rules', rule);
        }
      }
    }

    prevAiRuleIds.current = currentIds;
  }, [state.aiRules]);

  useEffect(() => {
    saveToStorage(LS.AI_RULE_SETTINGS, state.aiRuleSettings);
    if (!hydratedRef.current) return;
    dbPut('/settings/aiRuleSettings', state.aiRuleSettings);
  }, [state.aiRuleSettings]);

  useEffect(() => {
    saveToStorage(LS.AI, sanitizeAiConfigForLocalStorage(state.aiConfig));
    if (!hydratedRef.current) return;
    dbPut('/settings/aiConfig', sanitizeAiConfigForLocalStorage(state.aiConfig));
    dbPut('/secrets', extractSecretsPayload({ aiConfig: state.aiConfig, mineruConfig: state.mineruConfig, minioConfig: state.minioConfig }));
  }, [state.aiConfig]);

  useEffect(() => {
    saveToStorage(LS.MINERU, sanitizeMinerUConfigForLocalStorage(state.mineruConfig));
    if (!hydratedRef.current) return;
    dbPut('/settings/mineruConfig', sanitizeMinerUConfigForLocalStorage(state.mineruConfig));
    dbPut('/secrets', extractSecretsPayload({ aiConfig: state.aiConfig, mineruConfig: state.mineruConfig, minioConfig: state.minioConfig }));
  }, [state.mineruConfig]);

  useEffect(() => {
    saveToStorage(LS.MINIO, sanitizeMinioConfigForLocalStorage(state.minioConfig));
    if (!hydratedRef.current) return;
    dbPut('/settings/minioConfig', sanitizeMinioConfigForLocalStorage(state.minioConfig));
    dbPut('/secrets', extractSecretsPayload({ aiConfig: state.aiConfig, mineruConfig: state.mineruConfig, minioConfig: state.minioConfig }));
  }, [state.minioConfig]);

  // P1 Patch: batchProcessing 不再持久化到 localStorage 和 db-server。
  // 它是纯内存的短生命周期队列，刷新即清空。

  // ── 后端批处理队列状态轮询（已移除）──────────────────
  useEffect(() => {
    // 空效果，用于保持依赖关系
  }, []);

  return (
    <AppContext.Provider value={{ state, dispatch, dbReady }}>
      {children}
    </AppContext.Provider>
  );
}

// ─── Hook ──────────────────────────────────────────────────────

export function useAppStore(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppStore must be used within AppProvider');
  return ctx;
}

// ─── 导出工具函数（供外部使用）────────────────────────────────

export { dbDelete, dbPatch, dbPost, dbPut, dbGet };
