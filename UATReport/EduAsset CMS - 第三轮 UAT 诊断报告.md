# EduAsset CMS - 第三轮 UAT 诊断报告

## 1. 测试摘要 (Summary)

本次测试针对 EduAsset CMS 前端项目的第三轮 UAT 修复版本（Commit: `6832e6f`）进行了全面的静态代码分析与逻辑验证。测试重点关注了第二轮报告中遗留的 2 个 P1 级别问题、2 个 P2 级别问题，以及新发现的 3 个问题。

**测试结论：**
- **整体修复率**：第二轮遗留及新发现的 7 个问题中，有 5 个已得到有效修复，2 个问题（BUG-06、NEW-01 衍生）由于逻辑漏洞或类型定义问题，仍未完全修复。
- **核心问题状态**：
  - 批量删除、成品库删除、元数据删除等 CRUD 逻辑已全部打通。
  - Dashboard 图表数据已成功与 Store 联动。
  - **BUG-06（AI 状态覆盖问题）**：修复方案无效，仍会导致已分析资产的状态被重置为 `pending`。
  - **NEW-01 衍生问题（手动启动分析失败）**：当用户上传时选择“不自动启动 AI 分析”时，由于缺少 `tmpfiles` 上传步骤，后续手动点击“启动分析”时会因 MinerU 无法访问本地 `blob://` URL 而必然失败。

---

## 2. 第二轮遗留问题修复状态验证 (Defects Verification)

### 2.1 P1 级别问题（核心功能异常）

| ID | 模块 | 问题描述 | 修复状态 | 验证说明 |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-06** | 资产详情页 | AI 识别结果错误时无法手动编辑修正（保存时缺少 `aiStatus` 导致状态被覆盖） | ❌ **未修复** | 开发团队采用了 `assetData?.aiStatus ?? 'pending'` 的修复方案。但由于 `AssetDetail` 接口和 `Material` 构建的 `assetData` 对象中均未包含 `aiStatus` 字段，该值始终为 `undefined`。这导致每次保存编辑时，已分析资产的 `aiStatus` 都会被错误地重置为 `pending`。 |
| **BUG-08** | 原始资料库 | 单条资料的"启动分析"按钮仅有 toast 提示 | ⚠️ **部分修复** | 按钮已绑定真实的 `triggerAnalysisForMaterial` 逻辑。但如果资料上传时未选择自动分析，其 `previewUrl` 为本地 `blob://` 格式，MinerU 无法访问该格式，导致手动启动分析必然失败（详见 3.1 节）。 |

### 2.2 P2 级别问题（体验与边缘场景）

| ID | 模块 | 问题描述 | 修复状态 | 验证说明 |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-14** | 成品库 | 缺乏删除功能 | ✅ **已修复** | 已在 `ProductsPage.tsx` 中实现了删除确认弹窗，并成功触发 `DELETE_PRODUCT` reducer。 |
| **BUG-15** | 元数据管理 | 删除字段/标签/规则时仅有 toast 提示，未真实删除 | ✅ **已修复** | 标签和规则的删除按钮已分别绑定 `DELETE_FLEXIBLE_TAG` 和 `DELETE_AI_RULE` reducer，实现了真实的删除逻辑。 |

---

## 3. 第二轮新发现问题修复状态验证 (New Defects Verification)

| ID | 模块 | 问题描述 | 修复状态 | 验证说明 |
| :--- | :--- | :--- | :--- | :--- |
| **NEW-01** | 原始资料库 | 批量启动 AI 分析功能未实现 | ⚠️ **部分修复** | 已实现 `handleBatchStartAnalysis` 逻辑。但与 BUG-08 相同，受限于 `blob://` URL 问题，部分资料的批量分析会失败。 |
| **NEW-02** | 工作台 | Dashboard 图表数据仍为硬编码 | ✅ **已修复** | `weeklyData` 和 `monthlyTrend` 已通过 `useMemo` 从 `state.materials` 动态计算，实现了与 Store 的联动。 |
| **NEW-03** | 全局多页面 | 大量占位功能未清理 | ✅ **已修复** | 移除了大部分影响体验的“功能开发中”提示，保留的提示也优化了文案（如“已加入下载队列”），提升了 UAT 阶段的体验。 |

---

## 4. 深入诊断与新发现问题 (Deep Diagnosis & New Issues)

### 4.1 [NEW-04] 手动启动 AI 分析必然失败 (P0 级逻辑漏洞)
- **模块**：原始资料库 (`SourceMaterialsPage.tsx`)
- **问题描述**：
  当用户在上传资料时，如果取消勾选“自动启动 AI 分析”（`autoAI=false`），代码会直接 `return`，跳过将文件上传至 `tmpfiles.org` 获取公开 URL 的步骤。此时，该资料的 `previewUrl` 仅为本地的 `blob://...` 格式。
  后续用户在列表中手动点击“启动分析”时，`triggerAnalysisForMaterial` 函数会将这个 `blob://` URL 直接传给 MinerU API。由于 MinerU 服务器无法访问用户本地浏览器的 Blob URL，任务创建必然失败。
- **代码位置**：`SourceMaterialsPage.tsx` 第 801 行（跳过上传）及 1165 行（直接使用 fileUrl）。

### 4.2 [NEW-05] AI 规则未持久化到 localStorage (P2)
- **模块**：全局状态 (`appContext.tsx`)
- **问题描述**：
  虽然 `aiRules` 和 `aiRuleSettings` 已在 Store 中定义并可被修改，但在 `appContext.tsx` 的 `useEffect` 持久化列表中，遗漏了对这两个状态的 `saveToStorage` 调用。这导致用户在“元数据管理”页面修改的 AI 规则，在刷新页面后会全部丢失，恢复为初始状态。
- **代码位置**：`appContext.tsx` 第 90-115 行。

---

## 5. 修复建议与下一步行动 (Recommendations)

针对第三轮 UAT 发现的残留问题和新漏洞，建议开发团队采取以下具体修复措施：

1. **彻底修复 BUG-06（AI 状态覆盖问题）**：
   - **方案 A**：在 `AssetDetailPage.tsx` 的 `handleSaveEdit` 中，通过 `state.materials.find(m => m.id === assetId)?.aiStatus` 获取真实的 AI 状态，而不是依赖不完整的 `assetData`。
   - **方案 B**：在 `types.ts` 的 `AssetDetail` 接口中补充 `aiStatus` 字段，并在 `assetData` 的构建逻辑中正确映射该字段。

2. **修复 NEW-04（手动启动分析失败）**：
   - 在 `triggerAnalysisForMaterial` 函数中增加对 `fileUrl` 的校验。如果发现 `fileUrl.startsWith('blob:')`，必须先执行 `tmpfiles.org` 的上传逻辑，获取公开 URL，更新 Store 中的 `previewUrl`，然后再调用 MinerU API。
   - 或者，在上传文件时（无论 `autoAI` 是否为 true），统一将文件上传至 `tmpfiles.org` 以获取公开 URL。

3. **修复 NEW-05（AI 规则未持久化）**：
   - 在 `appContext.tsx` 中补充对 `state.aiRules` 和 `state.aiRuleSettings` 的 `useEffect` 监听，并调用 `saveToStorage` 进行持久化。
