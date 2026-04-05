---
name: eduasset-cms-revision-plan
overview: 基于 UAT 深度诊断报告和生产化审查报告，系统性修复 EduAsset CMS 项目的 11 个问题（2个 P0 数据丢失/核心 Bug + 4个 P1 核心功能缺失 + 5个 P2 体验优化），将生产就绪度从 60% 提升至 85%+。
design:
  architecture:
    framework: react
    component: shadcn
  styleKeywords:
    - Enterprise CMS
    - Dark Sidebar + Light Content
    - Card-based Layout
    - Blue Primary Color (#3b82f6)
    - Rounded-xl Design
  fontSystem:
    fontFamily: PingFang SC
    heading:
      size: 20px
      weight: 600
    subheading:
      size: 16px
      weight: 500
    body:
      size: 14px
      weight: 400
  colorSystem:
    primary:
      - "#3B82F6"
      - "#2563EB"
      - "#1D4ED8"
    background:
      - "#F8FAFC"
      - "#FFFFFF"
      - "#F1F5F9"
      - "#0F172A"
    text:
      - "#0F172A"
      - "#334155"
      - "#64748B"
      - "#94A3B8"
    functional:
      - "#10B981"
      - "#EF4444"
      - "#F59E0B"
      - "#8B5CF6"
todos:
  - id: p0-persistence
    content: 修复 P0 数据持久化缺陷：扩展 appContext.tsx 的 localStorage 白名单，将 materials/processTasks/tasks/products/assetDetails/flexibleTags 全部纳入写时持久化，确保页面刷新后数据不丢失
    status: completed
  - id: p0-string-undefined-bug
    content: "修复 P0 AssetDetailPage String(undefined) Bug：将 AI 摘要区和 PDF 预览 iframe 的条件判断从 `String(x) || fallback` 改为 `x ? String(x) : fallback`，消除 \"undefined\" 字符串渲染问题"
    status: completed
  - id: p1-delete-feature
    content: 实现 P1 删除功能：在 types.ts 新增 DELETE_MATERIAL AppAction，在 appReducer.ts 实现 reducer 逻辑（同时清理 materials 数组和 assetDetails），在 SourceMaterialsPage 替换两处 Fake UI 为真实 dispatch + 确认对话框
    status: completed
  - id: p1-ai-improvement
    content: 完善 P1 AI 识别链路：强化 AI 提示词（注入 FILTER_DIMENSIONS 枚举值强制中文输出）、为 parseAiJson 添加 try-catch 容错返回空对象而非崩溃、修正文件大小限制文案和前置校验
    status: completed
  - id: p1-retry-and-edit
    content: 实现 P1 错误恢复与编辑功能：区分 MinerU/AI 超时的 AbortError 显示友好提示、为 failed 状态资料卡添加"重新分析"按钮、实现资产详情页编辑元数据的 Dialog 弹窗及 UPDATE_MATERIAL_METADATA action
    status: completed
    dependencies:
      - p1-delete-feature
      - p1-ai-improvement
  - id: p2-polish
    content: 完成 P2 体验优化：修复 Layout 导航高亮前缀匹配、Dashboard 今日概览/趋势图数据动态关联 Store、SettingsPage MinerU 配置项改为可编辑 input 控件
    status: completed
---

## 产品概述

基于 UAT 深度诊断报告和生产化开发审查报告，系统性修复 EduAsset CMS（教育资产管理平台）的 11 个已知问题，将项目生产就绪度从约 60% 提升至 85%+。涵盖数据持久化、核心功能缺失、Bug修复和体验优化四个维度。

## 核心功能

- **P0 数据安全**: 将核心业务数据（materials/processTasks/tasks/products/assetDetails/flexibleTags）纳入 localStorage 持久化，解决页面刷新后全部丢失的致命缺陷
- **P0 Bug 修复**: 修复资产详情页 `String(undefined)` 渲染 "undefined" 字符串的问题（AI摘要区域、PDF预览 iframe）
- **P1 删除功能落地**: 在 Store 层新增 `DELETE_MATERIAL` action，替换原始资料库中批量删除和单条删除的 Fake UI（toast 占位）
- **P1 AI 识别优化**: 完善大模型提示词，注入 `FILTER_DIMENSIONS` 枚举值并强制中文输出，解决筛选失效问题；为 JSON 解析添加 try-catch 容错
- **P1 错误恢复**: 区分 MinerU 超时 AbortError 与普通错误，在 failed 状态资料卡片上添加"重新分析"重试入口按钮
- **P1 编辑功能实现**: 实现 `UPDATE_MATERIAL_METADATA` action 和资产详情页编辑弹窗（元数据表单+保存）
- **P2 导航高亮**: 侧边栏导航改用前缀匹配，子路由（如 `/asset/1`）正确高亮父级菜单
- **P2 上传校验**: 添加 100MB 文件大小前置校验，修正 UI 提示文案
- **P2 Dashboard 动态化**: 将活动趋势图、今日概览数字与 Store 真实数据联动
- **P2 设置页可编辑**: MinerU 的超时时间、API Endpoint 改为可交互 input，支持 dispatch 更新

## Tech Stack

- 前端框架: React 18 + TypeScript + Vite 6 + Tailwind CSS 4
- 状态管理: React Context + useReducer (现有架构)
- UI 组件库: shadcn/ui (Radix UI 原语组件)
- 图表: Recharts
- 持久化: localStorage (浏览器端)
- 通知系统: sonner

## 技术架构

### 系统架构（修改范围）

采用分层架构，本次修订涉及三层：

```
┌─────────────────────────────────┐
│        Pages Layer            │  ← AssetDetailPage, SourceMaterialsPage,
│   (6 个页面文件需修改)         │     Dashboard, SettingsPage, Layout
├─────────────────────────────────┤
│       Store Layer              │  ← appContext.tsx (持久化扩展),
│  (3 个文件需修改)               │     appReducer.ts (新 Action),
│                              │     types.ts (类型扩展)
└─────────────────────────────────┘
```

### 数据流变更

**当前（刷新丢失）**:
用户上传 → dispatch(ADD_MATERIAL) → 内存状态 → ❌ 刷新全丢

**修订后（持久化保留）**:
用户上传 → dispatch(ADD_MATERIAL) → 内存状态 → ✅ useEffect 同步写入 localStorage → 刷新自动恢复

### 关键设计决策

1. **持久化策略**: 采用"写时持久化"(Write-through)，每个 state 变更通过独立 useEffect 监听并序列化到 localStorage，与现有 aiConfig/mineruConfig 模式完全一致
2. **删除操作**: 新增 `DELETE_MATERIAL` action 同时清理 materials 数组和 assetDetails 对象中的对应记录
3. **编辑弹窗**: 复用 shadcn/ui Dialog 组件构建元数据编辑表单（title/subject/grade/materialType/standard/summary），提交时调用 `UPDATE_MATERIAL_AI_STATUS` action（复用已有 action，payload 已包含 metadata 字段更新能力）
4. **JSON 容错**: parseAiJson 返回空对象而非崩溃，配合 toast 警告
5. **AbortError 分离**: catch 块内通过 `error instanceof DOMException && error.name === 'AbortError'` 识别超时错误，显示友好提示

### 性能考虑

- localStorage 写入使用 JSON.stringify 序列化，对于正常量级的教育资料（几十到几百条）无性能瓶颈
- useEffect 依赖数组精确控制，避免不必要的序列化
- Dashboard 派生数据已使用 useMemo，图表数据联动不会引入额外计算开销

## 目录结构（修改范围）

```
/workspace/dev/Ui/src/
├── store/
│   ├── appContext.tsx          # [MODIFY] 扩展 localStorage 持久化白名单
│   ├── appReducer.ts           # [MODIFY] 新增 DELETE_MATERIAL、UPDATE_MATERIAL_METADATA action
│   └── types.ts                # [MODIFY] AppAction 联合类型扩展
└── app/
    ├── pages/
    │   ├── SourceMaterialsPage.tsx    # [MODIFY] 删除功能对接Store、AI提示词完善、JSON容错、重试按钮、文件大小校验
    │   ├── AssetDetailPage.tsx       # [MODIFY] String(undefined) Bug修复、编辑弹窗实现
    │   ├── Dashboard.tsx            # [MODIFY] 图表/概览数据动态化
    │   └── SettingsPage.tsx         # [MODIFY] MinerU配置字段可编辑
    └── components/
        └── Layout.tsx             # [MODIFY] 导航高亮改为前缀匹配
```

## Key Code Structures

### 新增 AppAction 类型（types.ts 扩展）

```typescript
// 新增到 AppAction 联合类型
| { type: 'DELETE_MATERIAL'; payload: number[] }  // 支持批量删除
| { type: 'UPDATE_MATERIAL_METADATA'; payload: { id: number; metadata: Record<string, string>; title?: string } }
```

### 持久化 key 定义（appContext.tsx 扩展）

```typescript
// 新增 STORAGE_KEY 常量
const STORAGE_KEY_MATERIALS = 'app_materials';
const STORAGE_KEY_PROCESS_TASKS = 'app_process_tasks';
const STORAGE_KEY_TASKS = 'app_tasks';
const STORAGE_KEY_PRODUCTS = 'app_products';
const STORAGE_KEY_ASSET_DETAILS = 'app_asset_details';
const STORAGE_KEY_FLEXIBLE_TAGS = 'app_flexible_tags';
```

## 设计风格

延续现有项目的现代企业级 CMS 风格：深色侧边栏 + 浅色内容区、蓝色主色调（#3b82f6）、卡片式布局、圆角设计风格（rounded-xl）。所有修改保持与现有 UI 高度一致，不引入新的视觉语言。

## 页面规划（仅涉及修改的页面区块）

### 页面1: 原始资料库页面 - 功能增强

**重点修改区块**:

- **删除操作区**: 将批量操作栏和列表行操作的删除按钮从 Fake UI（toast占位）替换为真实 dispatch 调用，删除前弹出确认对话框
- **资料卡片底部**: 为 mineruStatus='failed' 或 aiStatus='failed' 的卡片添加橙色"重新分析"按钮（Sparkles图标），点击后重新触发 MinerU+AI 流程
- **上传提示文案**: "单文件最大 200MB" 改为 "单文件最大 100MB"
- **上传校验**: handleSubmit 中增加 `data.file.size > 100 * 1024 * 1024` 时拦截并 toast.error

### 页面2: 资产详情页 - Bug修复 + 新增编辑

**重点修改区块**:

- **文件预览区**: 条件判断从 `String(assetData.metadata.previewUrl)` 改为 `assetData.metadata.summary ? String(...) : '...'`，消除 undefined 显示
- **AI摘要区**: 同上，summary 为空时显示 fallback 文案
- **顶部操作栏**: "编辑"按钮从 `toast.info('编辑功能开发中')` 改为打开编辑弹窗
- **编辑弹窗（NEW）**: 使用 shadcn Dialog，内含 title/subject/grade/materialType/standard/summary 六个字段表单，回填当前 assetData 数据，保存时 dispatch UPDATE_MATERIAL_AI_STATUS

### 页面3: 工作台 Dashboard - 数据动态化

**重点修改区块**:

- **统计卡片 #3 "Cleancode数量"**: 硬编码 `"2,154"` 改为从 store 动态计算（如 completed 材料数 * 某系数）
- **今日概览区**: 四个硬编码数字（47/31/8/156）改为从 materials/processTasks/products 的 uploadTimestamp/status 统计当日数据
- **平台活动趋势图**: weeklyData 改为基于 materials 按 uploadTimestamp 聚合生成（或至少让趋势方向正确）

### 页面4: 系统设置页 - 配置可编辑

**重点修改区块**:

- **MinerU 信息展示区**: "只读信息块"（第155-176行）改造：
- 超时时间: `<span>` → `<input type="number">`，绑定 state.mineruConfig.timeout，onChange dispatch UPDATE_MINERU_CONFIG
- API Endpoint: 同理改为 `<input type="text">`，绑定 apiEndpoint
- 模型版本: 下拉选择（pipeline/vlm）
- 语言/OCR/表格/公式: 改为 Switch 开关控件

### 页面5: 全局布局 - 导航高亮

**重点修改区块**:

- **侧边栏导航菜单**: 第45行 `location.pathname === item.href` 改为前缀匹配逻辑：

```
const isActive = item.href === '/'
? location.pathname === '/'
: location.pathname.startsWith(item.href);
```

## SubAgent

- **code-explorer**
- Purpose: 在实施阶段对修改目标文件进行精确定位和上下文确认，确保每处修改点的代码行号、周边逻辑准确无误
- Expected outcome: 所有 11 个问题的修改位置得到二次验证，降低回归风险

## Skill

- 无特殊 skill 需求，本任务为纯前端代码修订