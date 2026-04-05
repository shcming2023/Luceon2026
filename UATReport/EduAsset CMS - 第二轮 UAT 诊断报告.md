# EduAsset CMS - 第二轮 UAT 诊断报告

## 1. 测试摘要 (Summary)

本次测试针对 EduAsset CMS 前端项目的第一轮 UAT 修复版本进行了全面的静态代码分析与逻辑验证。测试重点关注了第一轮报告中提出的 15 个缺陷（Bug）的修复状态，特别是 P0 和 P1 级别的核心问题。

**测试结论：**
- **整体修复率**：约 60% (9/15) 的问题已得到有效修复。
- **核心问题状态**：P0 级别问题已全部修复；但 P1 级别中仍存在 2 个关键问题未完全修复，且引入了 1 个新的严重类型错误（BUG-06 衍生）。
- **新发现问题**：在验证过程中，发现了 3 个新的功能缺失或逻辑缺陷。

---

## 2. 第一轮缺陷修复状态验证 (Defects Verification)

### 2.1 P0 级别问题（严重阻碍流程）- 全部修复

| ID | 模块 | 问题描述 | 修复状态 | 验证说明 |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-01** | 全局状态 | 刷新页面后所有数据丢失 | ✅ **已修复** | 已在 `appContext.tsx` 中引入 `localStorage` 持久化机制，并通过 `useEffect` 监听状态变化自动保存。 |
| **BUG-02** | 资产详情页 | 页面标题和多处字段显示 "undefined" 字符串 | ✅ **已修复** | 已在 `AssetDetailPage.tsx` 中增加了对 `assetData` 的空值防护，并使用 `String(val \|\| '')` 避免了 undefined 字符串的渲染。 |
| **BUG-03** | 资产详情页 | PDF 预览区域显示 404，路径错误 | ✅ **已修复** | 上传时已正确生成 `localPreviewUrl`，并在详情页通过 `assetData.metadata.previewUrl` 正确绑定到 `iframe` 的 `src` 属性。 |

### 2.2 P1 级别问题（核心功能异常）- 部分修复

| ID | 模块 | 问题描述 | 修复状态 | 验证说明 |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-04** | 原始资料库 | MinerU 解析无超时处理，可能导致无限等待 | ✅ **已修复** | 已引入 `withTimeout` 和 `AbortController`，并在轮询逻辑中增加了基于 `mineruConfig.timeout` 的超时中断机制。 |
| **BUG-05** | 原始资料库 | AI 识别结果语言不一致（中英文混杂） | ✅ **已修复** | 已在 Kimi API 的 Prompt 中明确增加了严格的中文约束（如：必须使用中文学科名、禁止返回英文或拼音等）。 |
| **BUG-06** | 资产详情页 | AI 识别结果错误时无法手动编辑修正 | ❌ **未修复 (引入新Bug)** | 弹窗 UI 已实现，但保存逻辑 `handleSaveEdit` 触发的 `UPDATE_MATERIAL_AI_STATUS` 缺少必填的 `aiStatus` 字段，导致 reducer 将其覆盖为 `undefined`，引发状态异常。 |
| **BUG-07** | 原始资料库 | 批量删除功能未实现，点击无反应 | ✅ **已修复** | 已实现 `openDeleteDialog` 和 `DELETE_MATERIAL` reducer，支持单条和批量删除。 |
| **BUG-08** | 原始资料库 | 单条资料的"启动分析"按钮仅有 toast 提示 | ❌ **未修复** | 按钮的 `onClick` 事件仍然只有 `toast.info('已为「...」启动AI分析')`，未绑定真实的 AI 触发逻辑。 |
| **BUG-09** | 原始资料库 | `parseAiJson` 缺乏异常处理，AI 返回非 JSON 时页面崩溃 | ✅ **已修复** | 已在 `parseAiJson` 中增加了 `try-catch` 块，并在解析失败时返回空对象，避免了页面崩溃。 |

### 2.3 P2 级别问题（体验与边缘场景）- 部分修复

| ID | 模块 | 问题描述 | 修复状态 | 验证说明 |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-10** | 侧边栏导航 | 路由切换时，左侧导航栏的高亮状态未更新 | ✅ **已修复** | 已在 `Layout.tsx` 中通过 `useLocation` 和 `pathname.startsWith` 正确计算了 `isActive` 状态。 |
| **BUG-11** | 原始资料库 | 文件上传缺乏大小校验，大文件导致浏览器卡死 | ✅ **已修复** | 已在 `handleFileSelect` 中增加了 100MB 的文件大小硬性校验。 |
| **BUG-12** | 工作台 | Dashboard 统计数据为硬编码，未与 Store 联动 | ✅ **已修复** | 已通过 `useAppStore` 获取真实数据，并使用 `useMemo` 动态计算了各项统计指标。 |
| **BUG-13** | 系统设置 | MinerU 和 Kimi 的配置字段为只读，无法修改 | ✅ **已修复** | 已将 `input` 替换为受控组件，并绑定了 `onChange` 事件触发 `UPDATE_MINERU_CONFIG` 等 reducer。 |
| **BUG-14** | 成品库 | 缺乏删除功能 | ❌ **未修复** | `ProductsPage.tsx` 中仍未实现删除按钮和相关逻辑。 |
| **BUG-15** | 元数据管理 | 删除字段/标签/规则时仅有 toast 提示，未真实删除 | ❌ **未修复** | 删除按钮的 `onClick` 仍然只绑定了 `toast.error`，未触发真实的删除 reducer。 |

---

## 3. 新发现问题 (New Defects Found)

在本次验证过程中，除了上述遗留问题外，还发现了以下 3 个新的逻辑缺陷：

### 3.1 [NEW-01] 批量启动 AI 分析功能未实现 (P1)
- **模块**：原始资料库 (`SourceMaterialsPage.tsx`)
- **描述**：在多选资料后，底部操作栏的"批量AI分析"按钮仅绑定了 `toast.info`，未实现真实的批量处理逻辑。
- **代码位置**：`SourceMaterialsPage.tsx` 第 1311 行。

### 3.2 [NEW-02] Dashboard 图表数据仍为硬编码 (P2)
- **模块**：工作台 (`Dashboard.tsx`)
- **描述**：虽然顶部的统计卡片已与 Store 联动（修复了 BUG-12），但页面下方的图表数据（`weeklyData` 和 `monthlyTrend`）仍然是硬编码的静态数组。
- **代码位置**：`Dashboard.tsx` 第 25-40 行。

### 3.3 [NEW-03] 大量占位功能未清理 (P2)
- **模块**：全局多页面
- **描述**：系统中仍存在大量未实现的占位按钮（如：导出功能、新建处理任务、版本创建等），点击后仅弹出 `toast.info('xxx功能开发中')`。这在 UAT 阶段会严重影响用户体验。
- **涉及页面**：`ProcessWorkbenchPage`, `TaskCenterPage`, `AssetDetailPage`, `ProductsPage` 等。

---

## 4. 修复建议与下一步行动 (Recommendations)

针对未修复和新发现的问题，建议开发团队在下一轮迭代中优先处理以下事项：

1. **紧急修复 BUG-06 (TypeScript 类型与状态覆盖问题)**：
   - 在 `AssetDetailPage.tsx` 的 `handleSaveEdit` 中，必须向 `UPDATE_MATERIAL_AI_STATUS` 的 payload 中传入当前的 `aiStatus`（可从 `assetData` 中获取），以满足类型定义并防止 reducer 将其覆盖为 `undefined`。
2. **补全缺失的真实逻辑 (BUG-08, BUG-14, BUG-15, NEW-01)**：
   - 将所有仅绑定了 `toast` 的操作按钮替换为真实的业务逻辑。
   - 特别是"启动分析"（单条/批量）需要复用或重构 `handleUploadConfirm` 中的 AI 触发流程。
   - 在成品库和元数据管理页补充完整的 `DELETE` reducer 调用。
3. **清理硬编码与占位符 (NEW-02, NEW-03)**：
   - 动态计算 Dashboard 的图表数据。
   - 隐藏或移除当前版本不打算实现的"功能开发中"按钮，保持界面的整洁和功能的闭环。
