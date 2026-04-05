/**
 * 全局应用 Context
 * 提供 AppProvider 组件和 useAppStore hook，
 * 封装 useReducer 状态管理，向整个应用树注入全局状态与 dispatch
 */

import React, { createContext, useContext, useReducer, useEffect } from 'react';
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

// ==================== localStorage 持久化 ====================

const STORAGE_KEY_AI = 'app_ai_config';
const STORAGE_KEY_MINERU = 'app_mineru_config';
const STORAGE_KEY_MATERIALS = 'app_materials';
const STORAGE_KEY_PROCESS_TASKS = 'app_process_tasks';
const STORAGE_KEY_TASKS = 'app_tasks';
const STORAGE_KEY_PRODUCTS = 'app_products';
const STORAGE_KEY_ASSET_DETAILS = 'app_asset_details';
const STORAGE_KEY_FLEXIBLE_TAGS = 'app_flexible_tags';
const STORAGE_KEY_AI_RULES = 'app_ai_rules';
const STORAGE_KEY_AI_RULE_SETTINGS = 'app_ai_rule_settings';

function loadFromStorage<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;

    const parsed = JSON.parse(raw);

    if (Array.isArray(fallback)) {
      if (Array.isArray(parsed)) {
        return parsed as T;
      }

      if (parsed && typeof parsed === 'object') {
        return Object.values(parsed) as T;
      }

      return fallback;
    }

    if (fallback && typeof fallback === 'object' && parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return { ...fallback, ...parsed } as T;
    }

    return parsed as T;
  } catch {
    return fallback;
  }
}

function saveToStorage<T>(key: string, value: T) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 忽略 storage 写入失败
  }
}

function mergeConfigWithFallback<T extends Record<string, any>>(fallback: T, value: unknown): T {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fallback;
  }

  const result: Record<string, any> = { ...fallback };

  for (const key of Object.keys(fallback)) {
    const fallbackValue = fallback[key];
    const currentValue = (value as Record<string, any>)[key];

    if (typeof fallbackValue === 'string') {
      result[key] = typeof currentValue === 'string' && currentValue.trim() !== '' ? currentValue : fallbackValue;
      continue;
    }

    if (fallbackValue && typeof fallbackValue === 'object' && !Array.isArray(fallbackValue)) {
      result[key] = mergeConfigWithFallback(fallbackValue, currentValue);
      continue;
    }

    result[key] = currentValue ?? fallbackValue;
  }

  return result as T;
}

function loadConfigFromStorage<T extends Record<string, any>>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return mergeConfigWithFallback(fallback, parsed);
  } catch {
    return fallback;
  }
}

// ==================== 初始状态 ====================

/** 全局初始状态，由各模块的 Mock 数据组成（API 配置优先从 localStorage 读取，业务数据也优先从 localStorage 恢复） */
const initialState: AppState = {
  materials: loadFromStorage<Material[]>(STORAGE_KEY_MATERIALS, initialMaterials),
  processTasks: loadFromStorage(STORAGE_KEY_PROCESS_TASKS, initialProcessTasks),
  tasks: loadFromStorage(STORAGE_KEY_TASKS, initialTasks),
  products: loadFromStorage(STORAGE_KEY_PRODUCTS, initialProducts),
  flexibleTags: loadFromStorage(STORAGE_KEY_FLEXIBLE_TAGS, initialFlexibleTags),
  aiRules: loadFromStorage(STORAGE_KEY_AI_RULES, initialAiRules),
  aiRuleSettings: loadFromStorage(STORAGE_KEY_AI_RULE_SETTINGS, initialAiRuleSettings),
  aiConfig: loadConfigFromStorage<AiConfig>(STORAGE_KEY_AI, initialAiConfig),
  mineruConfig: loadConfigFromStorage<MinerUConfig>(STORAGE_KEY_MINERU, initialMinerUConfig),
  assetDetails: loadFromStorage(STORAGE_KEY_ASSET_DETAILS, initialAssetDetails),
};

// ==================== Context 定义 ====================

interface AppContextValue {
  /** 当前全局状态 */
  state: AppState;
  /** 触发状态变更的 dispatch 函数 */
  dispatch: React.Dispatch<AppAction>;
}

const AppContext = createContext<AppContextValue | null>(null);

// ==================== Provider 组件 ====================

/**
 * 全局状态 Provider
 * 包裹应用根节点，向下提供 state 和 dispatch
 * @param children - 子组件树
 */
export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  // 每当 aiConfig / mineruConfig 变化时持久化到 localStorage
  useEffect(() => {
    saveToStorage(STORAGE_KEY_AI, state.aiConfig);
  }, [state.aiConfig]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_MINERU, state.mineruConfig);
  }, [state.mineruConfig]);

  // 核心业务数据持久化（页面刷新后不丢失）
  useEffect(() => {
    saveToStorage(STORAGE_KEY_MATERIALS, state.materials);
  }, [state.materials]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_PROCESS_TASKS, state.processTasks);
  }, [state.processTasks]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_TASKS, state.tasks);
  }, [state.tasks]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_PRODUCTS, state.products);
  }, [state.products]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_ASSET_DETAILS, state.assetDetails);
  }, [state.assetDetails]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_FLEXIBLE_TAGS, state.flexibleTags);
  }, [state.flexibleTags]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_AI_RULES, state.aiRules);
  }, [state.aiRules]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_AI_RULE_SETTINGS, state.aiRuleSettings);
  }, [state.aiRuleSettings]);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

// ==================== Hook ====================

/**
 * 获取全局应用状态和 dispatch 的 Hook
 * 必须在 AppProvider 内部使用
 * @returns { state, dispatch }
 * @throws 若在 AppProvider 外部调用则抛出错误
 */
export function useAppStore(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error('useAppStore must be used within AppProvider');
  }
  return ctx;
}
