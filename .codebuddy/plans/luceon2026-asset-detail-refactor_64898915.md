---
name: luceon2026-asset-detail-refactor
overview: 重构 AssetDetailPage 右侧列：删除冗余的元数据概览卡片，新增 PDF 内嵌预览和 Markdown 渲染预览，同时调整元数据区域布局（分类在上，标签在分类卡片下方）。
todos:
  - id: refactor-state-and-lineage
    content: 重构 FileLineageCard 将 originalUrl 状态提升至 AssetDetailPage，新增 onOriginalUrlReady/onMdLoaded 回调 props
    status: completed
  - id: add-preview-components
    content: 新增 renderMarkdown 轻量渲染函数、PDFPreviewPanel 组件和 MarkdownRenderPanel 组件
    status: completed
    dependencies:
      - refactor-state-and-lineage
  - id: restructure-right-column
    content: 删除右侧冗余元数据概览卡片，将标签移入 AI 元数据分析面板底部，右侧列插入 PDF 和 Markdown 预览面板
    status: completed
    dependencies:
      - add-preview-components
---

## 用户需求

对 `AssetDetailPage`（资产详情页）进行布局重构和功能增强：

### 问题

当前页面右侧"元数据概览"卡片与左侧"AI 元数据分析面板"展示的是完全相同的字段（语言/年级/学科/国家/类型/置信度/摘要），属于冗余重复，造成空间浪费。

### 改造目标

**删除冗余**

- 移除右侧列中独立的"元数据概览"只读卡片（第 1432-1453 行）

**新增 PDF 页面内预览**

- 在右侧列增加 PDF 嵌入式预览组件，利用 `<iframe>` 直接内嵌原始文件的 presigned URL
- 仅当原始文件类型为 PDF 时显示；显示加载中/加载失败状态

**新增 Markdown 页面内预览**

- 在右侧列增加 Markdown 渲染预览组件，将解析后的 `full.md` 内容渲染为格式化 HTML（标题、表格、代码块、列表等），而非当前的纯文本 `<pre>`
- 有内容时自动显示；支持滚动查看全文；不依赖外部库，使用轻量内联渲染方案

**元数据区域布局调整**

- "AI 元数据分析面板"保持现有分类字段功能不变（语言/年级/学科/国家/类型/摘要）
- 将现有独立的"标签"卡片从右侧列移入"AI 元数据分析面板"底部，置于保存按钮上方，体现"分类 + 标签均为 AI 识别输出"的逻辑归属

**右侧列最终结构**

1. 文件溯源卡片（保持现有功能）
2. PDF 内嵌预览（有 PDF 时显示）
3. Markdown 渲染预览（解析完成后显示）
4. 相关资产（现有功能保留）

## 技术栈

- 已有项目：React 18 + TypeScript + Tailwind CSS 4 + Vite
- 无 `react-markdown` / `remark-gfm` / `@tailwindcss/typography` 依赖，**不引入新依赖**
- Markdown 渲染使用**轻量内联实现**（正则替换 + dangerouslySetInnerHTML，仅处理常见语法），避免 pnpm install 步骤

## 实现方案

### 1. presigned URL 状态提升

当前 `originalUrl`（presigned URL）封装在 `FileLineageCard` 内部 state，PDF 预览组件需要复用同一个 URL。

**方案**：在 `AssetDetailPage` 层新增 `originalUrl` state，通过 props 传给 `FileLineageCard`（同时用回调 `onOriginalUrlReady` 回传 URL）。`FileLineageCard` 内部不再自己 fetch presign，改为由父组件统一管理。

这样：

- `AssetDetailPage` 的 `originalUrl` state 可供右侧列 PDF 预览使用
- `FileLineageCard` 的"刷新链接/预览/下载"按钮仍正常工作（URL 由 props 注入）
- 单一数据源，避免重复 fetch

### 2. Markdown 预览内容来源

- 优先使用 `mineruMarkdown`（`AssetDetailPage` 内 state，解析后自动获得）
- 若 `mineruMarkdown` 为空但已有 `markdownObjectName`，`FileLineageCard` 内部已支持加载 `mdPreview`——通过在 `FileLineageCard` 新增 `onMdLoaded` 回调将内容提升到父组件，供右侧列渲染预览

### 3. Markdown 轻量渲染函数

项目已使用 Tailwind，无 typography 插件。实现一个 `renderMarkdown(md: string): string` 纯函数，用正则依次处理：

- ATX 标题（`# ## ###`）→ `<h1>/<h2>/<h3>`
- 粗体/斜体（`**` / `*`）→ `<strong>/<em>`
- 行内代码（`` ` `` ）→ `<code>`
- 代码块（ ```` `）→ `<pre><code>`
- 表格（GFM `|---|`）→ `<table>`
- 有序/无序列表 → `<ul>/<ol>`
- 换行 → `<br>`

输出通过 `dangerouslySetInnerHTML` 渲染（内容来源为系统内部 MinIO，可信任）。加内联 Tailwind class 实现基础排版。

### 4. 标签移入 AI 元数据分析面板

将原右侧独立标签卡片的所有 JSX 和逻辑（state、handlers 均已在 `AssetDetailPage` 中）整体迁移到 AI 元数据分析面板的保存按钮上方，添加分隔线区分"分类字段"与"标签"区域。

## 实现注意事项

- `FileLineageCard` 的 `originalUrl` 相关逻辑（刷新按钮、预览按钮、下载按钮）改为接收 `originalUrl` prop 及 `onRefreshUrl` 回调，内部不再持有该 state
- PDF `<iframe>` 使用 `key={originalUrl}` 避免 URL 变化时不刷新；添加 `onLoad/onError` 处理
- Markdown 预览区高度限定 `max-h-[600px] overflow-auto`，防止超长内容撑开页面
- 保持 `FileLineageCard` 内部的 `.md` 文件列表预览（`mdPreview`/`handlePreviewMd`）功能不变，仅新增右侧列的独立 Markdown 渲染预览面板；两者互不干扰
- `displayMeta` 变量及 `META_DISPLAY_ORDER` 常量在删除右侧元数据卡片后可一并清理
- 删除 `Archive` 图标导入（如未使用）防止 TS lint 告警

## 目录结构

```
src/app/pages/
└── AssetDetailPage.tsx  # [MODIFY] 唯一修改目标
```

### 关键改动点（行号参考）

| 改动 | 位置 | 操作 |
| --- | --- | --- |
| `FileLineageCard` Props 扩展 | 第 106-109 行 | 新增 `originalUrl/onOriginalUrlReady/onRefreshUrl/onMdLoaded` |
| `FileLineageCard` 内部 state 重构 | 第 120-129 行 | 删除 `originalUrl/refreshing` state，改用 props |
| 新增 `renderMarkdown` 工具函数 | 文件顶部区域 | 纯函数，无副作用 |
| 新增 `PDFPreviewPanel` 组件 | 文件顶部区域 | 接收 `url` prop |
| 新增 `MarkdownRenderPanel` 组件 | 文件顶部区域 | 接收 `content` prop |
| `AssetDetailPage` 新增 state | 第 556 行后 | `originalUrl/setOriginalUrl` |
| 删除右侧元数据概览卡片 | 第 1432-1453 行 | 整段删除 |
| 标签 JSX 迁移至 AI 面板底部 | 第 1395-1423 行 | 插入标签区域，右侧删除标签卡片 |
| 右侧列新增 PDF/MD 预览 | 第 1428 行后 | 插入两个新面板 |
| 清理 `displayMeta` 相关代码 | 第 1064-1070 行 | 删除 |