---
name: 第三轮UAT修复计划
overview: 修复 UAT 诊断报告中的 3 个遗留问题：(1) BUG-06 资产详情页编辑元数据时 AI 状态被覆盖为 pending、(2) NEW-04 非 autoAI 上传的资料手动启动分析因 blob URL 失败、(3) NEW-05 AI 规则未持久化到 localStorage
todos:
  - id: fix-new04-blob-url
    content: 修复 NEW-04：SourceMaterialsPage.tsx 统一 tmpfiles 上传并增加 blob URL 运行时防护
    status: completed
  - id: fix-bug06-ai-status
    content: 修复 BUG-06：types.ts 补充 AssetDetail.aiStatus 字段 + AssetDetailPage.tsx 修正状态获取逻辑
    status: completed
  - id: fix-new05-persistence
    content: 修复 NEW-05：appContext.tsx 补充 aiRules/aiRuleSettings 的 localStorage 持久化
    status: completed
  - id: verify-all-fixes
    content: 验证三处修复：编译检查 + 逻辑走查确保无回归
    status: completed
    dependencies:
      - fix-new04-blob-url
      - fix-bug06-ai-status
      - fix-new05-persistence
---

## 产品概述

根据《EduAsset CMS - 第三轮 UAT 诊断报告》，本轮需修复第三轮 UAT 中残留的 3 个问题，涵盖资产详情页 AI 状态覆盖、原始资料库手动启动分析失败、AI 规则未持久化三个模块。

## 核心功能

1. **BUG-06 [P1] 修复**：解决资产详情页编辑元数据保存时 `aiStatus` 被错误重置为 `pending` 的问题
2. **NEW-04 [P0] 修复**：解决非 autoAI 模式上传资料后手动启动分析必然失败的 blob URL 问题
3. **NEW-05 [P2] 修复**：解决 AI 规则修改后刷新页面配置丢失的 localStorage 持久化缺失问题

## Tech Stack

- **前端框架**: React + TypeScript（现有项目技术栈）
- **状态管理**: useReducer + Context（现有 appContext.tsx）
- **样式**: Tailwind CSS + shadcn/ui 组件
- **持久化**: localStorage（与现有模式一致）

## 实现方案

### BUG-06 [P1] - AI 状态覆盖问题修复

**根因链路**: `handleSaveEdit` 使用 `UPDATE_MATERIAL_AI_STATUS` action -> 强制传入 `aiStatus: assetData?.aiStatus ?? 'pending'` -> `AssetDetail` 接口缺少 `aiStatus` 字段 -> `assetData` 动态构建未映射 `mat.aiStatus` -> 值始终 fallback 为 `'pending'`

**修复策略 (A+B 组合)**:

- 在 `types.ts` 的 `AssetDetail` 接口中补充 `aiStatus?: AiStatus` 可选字段
- 在 `AssetDetailPage.tsx` 的 `assetData` useMemo 构建中映射 `aiStatus: mat.aiStatus`
- 在 `handleSaveEdit` 中采用三级兜底: `state.materials.find(m => m.id === assetId)?.aiStatus ?? assetData?.aiStatus ?? 'pending'`

**涉及文件**: `src/store/types.ts`, `src/app/pages/AssetDetailPage.tsx`

### NEW-04 [P0] - 手动启动分析失败 (blob URL) 修复

**根因链路**: `autoAI=false` 时跳过 tmpfiles 上传 -> previewUrl 保持为本地 blob URL -> handleStartAnalysis 直接传给 MinerU API -> 外部服务无法访问 blob URL

**修复策略 (统一上传 + 运行时检测双保险)**:

- 将 tmpfiles.org 上传逻辑从 autoAI 条件分支中提取出来，无论 autoAI 是否开启都执行上传获取公开 URL，确保 previewUrl 始终可被外部访问
- 在 `triggerAnalysisForMaterial` / `handleStartAnalysis` / `handleBatchStartAnalysis` 入口增加 `blob:` URL 检测防御，发现时给出明确错误提示引导用户重新上传

**涉及文件**: `src/app/pages/SourceMaterialsPage.tsx`

### NEW-05 [P2] - AI 规则未持久化修复

**根因**: 三处遗漏 -- STORAGE_KEY 常量缺失、initialState 未从 localStorage 加载、useEffect 持久化监听缺失

**修复策略**:

- 新增 `STORAGE_KEY_AI_RULES = 'app_ai_rules'` 和 `STORAGE_KEY_AI_RULE_SETTINGS = 'app_ai_rule_settings'` 常量
- initialState 中改用 `loadFromStorage<AiRule[]>(...)` 和 `loadFromStorage<AiRuleSettings>(...)` 加载
- 补充两个 useEffect 分别监听 `state.aiRules` 和 `state.aiRuleSettings` 并调用 `saveToStorage`

**涉及文件**: `src/store/appContext.tsx`

## 目录结构

```
project-root/src/
├── store/
│   ├── types.ts                    # [MODIFY] AssetDetail 接口补充 aiStatus 字段
│   ├── appContext.tsx              # [MODIFY] 补充 aiRules/aiRuleSettings 持久化逻辑
│   └── appReducer.ts              # 无需修改 (已有正确处理)
└── app/pages/
    ├── AssetDetailPage.tsx         # [MODIFY] 修复 aiStatus 映射与保存逻辑
    └── SourceMaterialsPage.tsx    # [MODIFY] 统一上传流程 + blob URL 防护
```

## 关键注意事项

- **blast radius control**: 每个修复仅改动目标文件的相关行，不涉及无关逻辑重构
- **向后兼容**: `AssetDetail` 接口的 `aiStatus` 设为可选字段 (`?`)，不影响其他使用处
- **性能**: NEW-04 的统一上传方案会增加所有上传的 tmpfiles 网络请求时间，但这是保证后续手动分析可用的必要代价；对于大文件场景可考虑在 UI 上增加提示说明

## Agent Extensions

### SubAgent

- **code-explorer**
- Purpose: 在实施过程中验证每个修复点的代码上下文准确性
- Expected outcome: 确认每处修改的精确行号和周围代码结构，避免引入回归