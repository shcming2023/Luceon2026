/**
 * 全局应用 Context
 *
 * 数据持久化策略（三级降级）：
 *   1. 主存储：SQLite（通过 /__proxy/db/ REST API）
 *   2. 次存储：localStorage（降级兜底，同步写）
 *   3. 内存 fallback：mockData 初始数据（全量 API 失败时）
 *
 * 启动流程：
 *   a. 先用 localStorage 快速渲染（避免白屏）
 *   b. 异步请求 SQLite API
 *   c. 若 API 数据存在，以 SQLite 数据覆盖 localStorage 并更新 state
 *   d. 若 SQLite 库为空，则将当前内存数据 bulk-restore 写入 SQLite
 *
 * 写操作：
 *   - reducer 照常更新内存 state
 *   - useEffect 监听各 state 切片 → 同步写 localStorage（防丢失）
 *   - useEffect 监听各 state 切片 → 异步 fire-and-forget 写 SQLite（不阻塞 UI）
 */

import React, {
  createContext, useContext, useReducer, useEffect, useRef, useState,
} from 'react';
import type { AppState, AppAction, AiConfig, MinerUConfig, Material } from './types';
import { appReducer } from './appReducer';
import {
  initialMaterials,
  initialProcessTasks,
  initialTasks,
  initialProducts,
  initialFlexibleTags,
  initialAiRules,
  initialAiRuleSettings,
  initialAiConfig,
  initialMinerUConfig,
  initialAssetDetails,
} from './mockData';

// ─── API 基础路径 ──────────────────────────────────────────────
const DB_BASE = '/__proxy/db';

// ─── localStorage keys ────────────────────────────────────────
const LS = {
  AI:             'app_ai_config',
  MINERU:         'app_mineru_config',
  MATERIALS:      'app_materials',
  PROCESS_TASKS:  'app_process_tasks',
  TASKS:          'app_tasks',
  PRODUCTS:       'app_products',
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

// ─── SQLite API 工具 ───────────────────────────────────────────

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
    await fetch(`${DB_BASE}${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
  } catch { /* fire-and-forget，忽略网络错误 */ }
}

async function dbPatch(path: string, body: unknown): Promise<void> {
  try {
    await fetch(`${DB_BASE}${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
  } catch { /* fire-and-forget */ }
}

async function dbPost(path: string, body: unknown): Promise<void> {
  try {
    await fetch(`${DB_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
  } catch { /* fire-and-forget */ }
}

async function dbDelete(path: string, body: unknown): Promise<void> {
  try {
    await fetch(`${DB_BASE}${path}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
  } catch { /* fire-and-forget */ }
}

// ─── 初始状态（先用 localStorage，等 SQLite 加载完再覆盖）─────

const initialState: AppState = {
  materials:        loadFromStorage<Material[]>(LS.MATERIALS, initialMaterials),
  processTasks:     loadFromStorage(LS.PROCESS_TASKS, initialProcessTasks),
  tasks:            loadFromStorage(LS.TASKS, initialTasks),
  products:         loadFromStorage(LS.PRODUCTS, initialProducts),
  flexibleTags:     loadFromStorage(LS.FLEXIBLE_TAGS, initialFlexibleTags),
  aiRules:          loadFromStorage(LS.AI_RULES, initialAiRules),
  aiRuleSettings:   loadFromStorage(LS.AI_RULE_SETTINGS, initialAiRuleSettings),
  aiConfig:         loadConfigFromStorage<AiConfig>(LS.AI, initialAiConfig),
  mineruConfig:     loadConfigFromStorage<MinerUConfig>(LS.MINERU, initialMinerUConfig),
  assetDetails:     loadFromStorage(LS.ASSET_DETAILS, initialAssetDetails),
};

// ─── Context ──────────────────────────────────────────────────

interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  /** SQLite 加载是否完成（可用于显示加载指示） */
  dbReady: boolean;
}

const AppContext = createContext<AppContextValue | null>(null);

// ─── Provider ─────────────────────────────────────────────────

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  const [dbReady, setDbReady] = useState(false);

  // 防止 SQLite 写操作在 hydration 完成之前触发（初次加载时跳过写操作）
  const hydratedRef = useRef(false);

  // ── 启动：从 SQLite 加载数据（一次性，挂载后执行）──────────
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
        ] = await Promise.all([
          dbGet<Material[]>('/materials'),
          dbGet<Record<number, object>>('/asset-details'),
          dbGet<object[]>('/process-tasks'),
          dbGet<object[]>('/tasks'),
          dbGet<object[]>('/products'),
          dbGet<object[]>('/flexible-tags'),
          dbGet<object[]>('/ai-rules'),
          dbGet<Record<string, unknown>>('/settings'),
        ]);

        if (cancelled) return;

        const hasMaterials = Array.isArray(materials) && materials.length > 0;

        if (hasMaterials) {
          // SQLite 有数据：直接派发一个特殊 action 覆盖内存 state
          dispatch({
            type: 'HYDRATE_FROM_DB',
            payload: {
              materials:      materials ?? undefined,
              assetDetails:   assetDetails ?? undefined,
              processTasks:   processTasks ?? undefined,
              tasks:          tasks ?? undefined,
              products:       products ?? undefined,
              flexibleTags:   flexibleTags ?? undefined,
              aiRules:        aiRules ?? undefined,
              aiRuleSettings: settings?.aiRuleSettings ?? undefined,
              aiConfig:       settings?.aiConfig ? mergeConfigWithFallback(initialAiConfig, settings.aiConfig) : undefined,
              mineruConfig:   settings?.mineruConfig ? mergeConfigWithFallback(initialMinerUConfig, settings.mineruConfig) : undefined,
            },
          });
          console.log(`[appContext] Hydrated from SQLite (${materials?.length} materials)`);
        } else {
          // SQLite 为空：将当前内存数据 bulk-restore 写入 SQLite（种子数据）
          console.log('[appContext] SQLite empty, seeding from current state...');
          await dbPost('/bulk-restore', {
            materials:      state.materials,
            assetDetails:   state.assetDetails,
            processTasks:   state.processTasks,
            tasks:          state.tasks,
            products:       state.products,
            flexibleTags:   state.flexibleTags,
            aiRules:        state.aiRules,
            aiRuleSettings: state.aiRuleSettings,
            aiConfig:       state.aiConfig,
            mineruConfig:   state.mineruConfig,
          });
          console.log('[appContext] Seeded SQLite with initial data');
        }
      } catch (err) {
        console.warn('[appContext] SQLite hydration failed, using localStorage fallback:', err);
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
  const prevAssetIds      = useRef<Set<number>>(new Set(Object.keys(state.assetDetails).map(Number)));
  const prevProcessIds    = useRef<Set<number>>(new Set(state.processTasks.map((t) => t.id)));
  const prevProductIds    = useRef<Set<number>>(new Set(state.products.map((p) => p.id)));
  const prevTagIds        = useRef<Set<number>>(new Set(state.flexibleTags.map((t) => t.id)));
  const prevAiRuleIds     = useRef<Set<number>>(new Set(state.aiRules.map((r) => r.id)));

  // ── 持久化：localStorage（同步）+ SQLite（异步）────────────

  useEffect(() => {
    saveToStorage(LS.MATERIALS, state.materials);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.materials.map((m) => m.id));

    // 检测删除：上一轮有、这一轮没有的 id 需要从 SQLite 删除
    const deletedIds = [...prevMaterialIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      dbDelete('/materials', { ids: deletedIds });
    }
    prevMaterialIds.current = currentIds;

    // upsert 当前所有 materials
    for (const m of state.materials) {
      dbPost('/materials', m);
    }
  }, [state.materials]);

  useEffect(() => {
    saveToStorage(LS.ASSET_DETAILS, state.assetDetails);
    if (!hydratedRef.current) return;

    const currentIds = new Set(Object.keys(state.assetDetails).map(Number));

    // 检测删除
    const deletedIds = [...prevAssetIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      // asset_details 表的批量删除由 /materials DELETE 联动，但以防万一也显式删
      for (const id of deletedIds) {
        // db-server 中 /materials DELETE 已联动删除 asset_details，这里 fire-and-forget 保险起见
        dbDelete('/materials', { ids: [id] });
      }
    }
    prevAssetIds.current = currentIds;

    for (const [id, detail] of Object.entries(state.assetDetails)) {
      dbPut(`/asset-details/${id}`, detail);
    }
  }, [state.assetDetails]);

  useEffect(() => {
    saveToStorage(LS.PROCESS_TASKS, state.processTasks);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.processTasks.map((t) => t.id));
    prevProcessIds.current = currentIds;

    for (const t of state.processTasks) {
      dbPost('/process-tasks', t);
    }
  }, [state.processTasks]);

  useEffect(() => {
    saveToStorage(LS.TASKS, state.tasks);
    if (!hydratedRef.current) return;
    // tasks 较少变更，仅 upsert（无批量删除操作）
    for (const t of state.tasks) {
      dbPost('/tasks', t);
    }
  }, [state.tasks]);

  useEffect(() => {
    saveToStorage(LS.PRODUCTS, state.products);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.products.map((p) => p.id));
    const deletedIds = [...prevProductIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      dbDelete('/products', { ids: deletedIds });
    }
    prevProductIds.current = currentIds;

    for (const p of state.products) {
      dbPost('/products', p);
    }
  }, [state.products]);

  useEffect(() => {
    saveToStorage(LS.FLEXIBLE_TAGS, state.flexibleTags);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.flexibleTags.map((t) => t.id));
    const deletedIds = [...prevTagIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      dbDelete('/flexible-tags', { ids: deletedIds });
    }
    prevTagIds.current = currentIds;

    for (const tag of state.flexibleTags) {
      dbPost('/flexible-tags', tag);
    }
  }, [state.flexibleTags]);

  useEffect(() => {
    saveToStorage(LS.AI_RULES, state.aiRules);
    if (!hydratedRef.current) return;

    const currentIds = new Set(state.aiRules.map((r) => r.id));
    const deletedIds = [...prevAiRuleIds.current].filter((id) => !currentIds.has(id));
    if (deletedIds.length > 0) {
      dbDelete('/ai-rules', { ids: deletedIds });
    }
    prevAiRuleIds.current = currentIds;

    for (const rule of state.aiRules) {
      dbPost('/ai-rules', rule);
    }
  }, [state.aiRules]);

  useEffect(() => {
    saveToStorage(LS.AI_RULE_SETTINGS, state.aiRuleSettings);
    if (!hydratedRef.current) return;
    dbPut('/settings/aiRuleSettings', state.aiRuleSettings);
  }, [state.aiRuleSettings]);

  useEffect(() => {
    saveToStorage(LS.AI, state.aiConfig);
    if (!hydratedRef.current) return;
    dbPut('/settings/aiConfig', state.aiConfig);
  }, [state.aiConfig]);

  useEffect(() => {
    saveToStorage(LS.MINERU, state.mineruConfig);
    if (!hydratedRef.current) return;
    dbPut('/settings/mineruConfig', state.mineruConfig);
  }, [state.mineruConfig]);

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
