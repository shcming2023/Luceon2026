# EduAsset CMS - UAT 深度诊断报告

**测试版本**: 最新提交 (`5106770`)
**测试环境**: Ubuntu 22.04 / Node.js 22.13.0 / Chrome 浏览器
**测试方式**: 源码深度审查 + 运行时黑盒测试

---

## 核心问题诊断（用户已知问题）

### 1. MinerU 解析超时后任务卡死
**现象**: 超时后弹出技术性错误提示（"The operation was aborted"），且任务永久停留在 `failed` 状态，无法重试。
**根因分析**:
- **错误识别缺失**: `withTimeout` 抛出 `AbortError` 后，`catch` 块未通过 `error.name === 'AbortError'` 进行区分，导致直接将底层错误抛给用户。
- **状态流转断裂**: 超时后虽然将状态置为 `failed`，但 UI 层（`SourceMaterialsPage`）未针对 `failed` 状态提供"重新分析"或"重试"的入口按钮，导致流程彻底卡死。

### 2. AI 识别语言不一致且筛选失效
**现象**: AI 有时输出英文（如 `Math`），导致左侧筛选面板无法筛选出该资料。
**根因分析**:
- **提示词约束不足**: `prompt` 中仅要求 `tags` 返回中文，未对 `subject`、`grade`、`materialType` 强制要求中文输出。
- **值域未对齐**: 筛选面板使用的是严格的枚举值（如 `['数学', '语文', '英语']`），而 AI 提示词中未提供这些枚举选项供 AI 参考。当 AI 自由发挥输出 "数理化" 或 "Math" 时，精确匹配的筛选逻辑必然失效。

### 3. AI 识别结果无法编辑
**现象**: 点击"编辑"按钮只弹出 "编辑功能开发中" 的提示。
**根因分析**:
- **Store 层缺失**: `appReducer.ts` 中完全没有实现 `UPDATE_MATERIAL_METADATA` 这一 Action，底层数据无法被修改。
- **UI 层缺失**: `AssetDetailPage` 和 `SourceMaterialsPage` 的编辑按钮仅绑定了 `toast.info` 占位符，未实现真实的表单弹窗和数据回填逻辑。

---

## 新发现的严重缺陷 (High Priority)

### 4. 致命的数据持久化缺陷 (Data Loss)
**现象**: 用户刷新页面后，所有上传的资料、处理任务、成品库数据全部丢失。
**根因分析**: `appContext.tsx` 的 `localStorage` 持久化白名单中，仅包含了 `aiConfig` 和 `mineruConfig`，核心业务数据（`materials`, `processTasks`, `tasks`, `products`）均未被持久化。

### 5. 批量与单条删除功能未实现 (Fake UI)
**现象**: 在原始资料库中点击"删除"或"批量删除"，仅弹出成功提示，数据依然存在。
**根因分析**: `SourceMaterialsPage` 中的删除按钮仅调用了 `toast.success`，完全没有调用 `dispatch({ type: 'DELETE_MATERIAL' })`。

### 6. 资产详情页 AI 摘要显示异常 (UI Bug)
**现象**: 当 AI 尚未完成分析时，详情页的 AI 摘要区域显示字符串 `"undefined"`。
**根因分析**: 代码中使用了 `String(assetData.metadata.summary) || '暂无摘要'`。当 `summary` 为 `undefined` 时，`String(undefined)` 会返回真值字符串 `"undefined"`，导致 fallback 逻辑失效。

### 7. 资产详情页文件预览失效 (UI Bug)
**现象**: 详情页的 PDF 预览 iframe 尝试加载 `/undefined` 路径导致 404。
**根因分析**: 同上，`String(assetData.metadata.previewUrl)` 在 `undefined` 时返回了字符串 `"undefined"`，被直接赋值给了 iframe 的 `src` 属性。

---

## 中低优先级缺陷 (Medium/Low Priority)

### 8. 导航栏高亮逻辑缺陷
**现象**: 进入资产详情页 (`/asset/1`) 时，顶部导航栏的"原始资料库"失去高亮状态。
**根因分析**: `Layout.tsx` 中的高亮判断逻辑为严格相等 (`location.pathname === item.href`)，未处理子路由前缀匹配。

### 9. 文件上传大小限制不一致
**现象**: UI 提示支持最大 200MB，但实际使用的 `tmpfiles.org` 免费版限制为 100MB。
**根因分析**: 前端 `handleSubmit` 中未做任何文件大小的前置校验，直接将文件发给图床，超限后才由图床返回错误。

### 10. 仪表盘数据硬编码 (Fake Data)
**现象**: Dashboard 的"活动趋势"图表和"今日概览"数据永远不变。
**根因分析**: `weeklyData` 和 `monthlyData` 在组件外部被硬编码为常量，未与 Store 中的真实任务状态联动。

### 11. 系统设置页部分字段只读
**现象**: MinerU 的超时时间（Timeout）和 API Endpoint 无法修改。
**根因分析**: `SettingsPage` 中这两个字段被渲染为普通的 `<div>` 文本，未提供 `<input>` 交互元素。

### 12. 缺少 JSON 解析容错
**现象**: 若 AI 返回了非标准 JSON（如缺少闭合括号），页面会直接崩溃。
**根因分析**: `parseAiJson` 函数中的 `JSON.parse(codeBlock)` 外层缺少 `try-catch` 保护。

---

## 修复建议路线图

1. **P0 (立即修复)**: 
   - 修复 `appContext.tsx`，将 `materials` 等核心数据加入 `localStorage` 持久化列表。
   - 修复 `AssetDetailPage` 中的 `String(undefined)` 渲染 Bug。
2. **P1 (核心流程)**: 
   - 完善 AI 提示词，注入 `FILTER_DIMENSIONS` 的枚举值，并强制要求中文输出。
   - 在 `appReducer` 中补充 `UPDATE_MATERIAL_METADATA` 和 `DELETE_MATERIAL` Action，并对接 UI。
   - 捕获 `AbortError`，并在 UI 上补充"重试"按钮。
3. **P2 (体验优化)**: 
   - 修复导航栏高亮逻辑。
   - 增加文件上传的 100MB 前置校验。
   - 将 Dashboard 的图表数据与 Store 真实联动。
