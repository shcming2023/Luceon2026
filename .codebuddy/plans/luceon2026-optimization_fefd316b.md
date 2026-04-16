---
name: luceon2026-optimization
overview: 整合两个优化方向：① AI 多策略容错调用（多提供商顺序 fallback，Ollama 兜底）；② AssetDetailPage 界面改进（原文件名显示、文件溯源修正、MinerU 产物树形结构 + 整包下载）。
todos:
  - id: type-and-data
    content: 扩展 types.ts 新增 AiProvider 接口和 AiConfig.providers 字段；更新 mockData.ts 初始化多提供商默认配置
    status: completed
  - id: backend-ai-fallback
    content: 重构 upload-server.mjs：提取 callAiProvider/analyzeWithFallback 函数，重构 /parse/analyze 支持多策略 fallback，新增 /parsed-zip 整包打包下载接口
    status: completed
    dependencies:
      - type-and-data
  - id: settings-ui
    content: 改造 SettingsPage.tsx 的 AI 配置 Tab，实现多提供商卡片列表管理UI（启用/禁用/优先级调整/新增/删除）
    status: completed
    dependencies:
      - type-and-data
  - id: detail-page-ui
    content: 改造 AssetDetailPage.tsx：B1原文件名+识别名称信息条；B2文件溯源折叠路径；B3解析产物目录树展示+下载全部按钮；handleAiAnalyze 改用 providers 新格式
    status: completed
    dependencies:
      - backend-ai-fallback
  - id: batch-upload-compat
    content: 更新 BatchUploadModal.tsx：调用 /parse/analyze 时改传 aiProviders 数组新格式，移除旧单字段判断逻辑
    status: completed
    dependencies:
      - backend-ai-fallback
---

## 用户需求

本次修订计划整合两轮讨论中的所有需求，分为两个独立但可并行实施的模块。

## 需求 A：AI 多策略容错调用

**背景**：批量上传时触发 AI API 限流（HTTP 429），单策略直接失败，中断整个分析流程。

**功能描述**：

- `AiConfig` 支持配置多个 AI 提供商（Moonshot、Kimi、OpenAI、本地 Ollama 等），每个提供商有独立的端点、密钥、模型名、超时和优先级
- 后端 `/parse/analyze` 接口按提供商优先级顺序依次尝试，第一个成功立即返回，不再调用后续提供商
- 单个提供商内可配置重试次数（仅限网络超时等可重试错误，429/401 不重试直接跳下一个）
- 全部提供商失败后，聚合所有失败原因一并报错
- **向后兼容**：保留旧的 `aiApiEndpoint / aiApiKey / aiModel` 单字段格式，请求中未传 `aiProviders` 时自动兼容
- 系统设置页面（`SettingsPage.tsx`）中 AI 配置 Tab 改为多提供商管理界面，支持新增/删除/启用/禁用/排序各提供商，Ollama 作为本地兜底默认禁用
- 响应中携带 `_meta.providerName` 供调试

## 需求 B：资产详情页三处界面改进

### B1 - AI 元数据分析面板

- 在分析结果区域顶部新增两行只读信息条：
- **原文件名**：显示 `material.metadata?.fileName`（用户上传的原始文件名，非 MinIO normalize 后的路径）
- **识别名称**：显示 `material.title`（AI 识别回填的资料标题）
- 两者并排显示，视觉上有明显区分

### B2 - 文件溯源 > 原始文件上传

- 主要展示行：显示原始文件名（`material.metadata?.fileName`），使用正常字体，不使用 `font-mono`
- 次要展示（折叠）：MinIO 完整对象路径（`objectName`）点击展开，使用 `font-mono` 灰色小字
- 保留现有刷新链接、预览、下载按钮不变

### B3 - 文件溯源 > MinerU 解析产物

- 文件列表从扁平结构改为目录树结构展示：根目录文件（`full.md`、`full.json` 等）直接显示，`images/`、`layout/` 等子目录作为可展开的文件夹节点，展示子文件列表
- 解析产物区域顶部增加"下载全部"按钮，触发后端新接口，将 `parsed/{materialId}/` 目录下所有文件打包成 ZIP 文件下载
- 后端新增 `POST /parsed-zip` 接口（`upload-server.mjs`），将 MinIO 指定前缀下所有文件打包返回

## 核心功能点汇总

- 多 AI 提供商按优先级顺序 fallback 调用
- 单提供商成功即返回，不调用后续
- 全部失败才报错
- Ollama 本地兜底（无需 apiKey）
- 设置页面支持多提供商可视化管理
- 原始文件名正常显示（非 MinIO normalize 路径）
- MinerU 解析产物目录树展示
- 解析产物整包 ZIP 下载

## 技术栈

基于现有项目技术栈，无新增依赖：

- **前端**：React + TypeScript + Tailwind CSS（现有）
- **后端**：Node.js + Express（现有 `upload-server.mjs`）
- **ZIP 打包**：`JSZip`（项目已引入，`upload-server.mjs` 中已使用）
- **状态管理**：已有的 `appContext` + `appReducer` 模式

---

## 实现方案

### 模块 A：多策略 AI 容错

#### 类型层扩展（`src/store/types.ts`）

在现有 `AiConfig` 基础上新增 `AiProvider` 接口，`AiConfig` 增加 `providers` 数组字段，同时保留 `apiEndpoint / apiKey / model / timeout` 作为兼容字段（旧配置迁移时使用）：

```typescript
export interface AiProvider {
  id: string;           // 唯一标识，如 'moonshot' | 'kimi' | 'openai' | 'ollama'
  name: string;         // 显示名称
  enabled: boolean;     // 是否启用
  apiEndpoint: string;
  apiKey: string;       // Ollama 可为空串
  model: string;
  timeout: number;      // 单次请求超时秒数
  priority: number;     // 数值越小优先级越高
}

// AiConfig 扩展：新增 providers，保留旧字段向后兼容
export interface AiConfig {
  [key: string]: unknown;
  // === 新增：多提供商 ===
  providers?: AiProvider[];
  // === 保留旧字段（兼容） ===
  apiEndpoint: string;
  apiKey: string;
  model: string;
  timeout: number;
  prompts: AiPromptConfig;
  maxFileSize: number;
  enabledFileTypes: string[];
}
```

#### 后端重构（`server/upload-server.mjs`）

新增两个纯函数：

1. `callAiProvider(provider, systemPrompt, userPrompt)` —— 调用单个提供商，返回解析后的 JSON 对象；Ollama 无需 Authorization header；内部超时用 `AbortSignal.timeout(timeoutMs)`
2. `analyzeWithFallback(providers, systemPrompt, userPrompt, opts)` —— 按 `priority` 排序后顺序尝试，成功立即返回 `{ result, providerId, providerName }`；失败分两类：429/401/403 不重试直接跳下一个；网络超时/ECONNREFUSED 可重试（受 `maxRetries` 限制，间隔 `retryDelay * attempt` ms）；全部失败抛出聚合错误

`/parse/analyze` 接口改造：

- 优先读取 `req.body.aiProviders`（新格式）
- 若不存在则从 `aiApiEndpoint / aiApiKey / aiModel` 构建单元素兼容 `providers` 数组
- 过滤 `enabled` 并按 `priority` 排序后传入 `analyzeWithFallback`
- 响应增加 `_meta: { providerId, providerName }` 调试字段

`BatchUploadModal.tsx` 调用侧：将 `aiApiEndpoint / aiApiKey / aiModel` 三字段替换为传递 `state.aiConfig.providers`（新格式），同时保留旧格式兼容路径

`AssetDetailPage.tsx` `handleAiAnalyze`：同上改造

#### 前端设置页（`SettingsPage.tsx`）

将 AI 配置 Tab 的"接口参数"区域从单行配置改为多提供商列表卡片形式：

- 每个 `AiProvider` 渲染一张卡片，含启用开关、名称、优先级序号、endpoint、API Key（密文）、模型名、测试连接按钮
- 支持新增提供商（弹出简单表单）和删除提供商
- 拖拽或上移/下移调整优先级（用上移/下移按钮实现，不引入拖拽库）
- 默认预置 Moonshot（启用）、Kimi（禁用）、OpenAI（禁用）、Ollama（禁用）四个提供商
- `mockData.ts` 中 `initialAiConfig` 同步更新默认值

---

### 模块 B：界面改进

#### B1 - AI 元数据分析面板原文件名显示

在 `AssetDetailPage.tsx` 的 AI 元数据分析面板（第 1024 行附近）标题行下方新增一个信息条：

```
原文件名：高中英语写作技巧练习册_高中版.pdf   →   识别名称：2025-2026高中英语写作技巧练习册
```

- `material.metadata?.fileName` 用于显示原文件名，`material.title` 用于显示识别名称
- 两者之间用 `→` 视觉分隔，识别名称仅在 `aiStatus === 'analyzed'` 时显示

#### B2 - 文件溯源原始文件名正确显示

修改 `FileLineageCard` 中"层 1：原始文件"区域：

- 删除直接显示 `objectName` 的 `<p>` 标签
- 新增主显示：`material.metadata?.fileName`（原始文件名），正常字体
- MinIO 路径改为折叠展示：点击"展开路径"小链接后才显示 `font-mono` 灰色小字的 `objectName`
- state 新增一个 `showPath` boolean 局部状态控制展开/折叠

#### B3 - MinerU 解析产物目录树 + 整包下载

**前端改动**：

`FileLineageCard` 中新增工具函数 `groupByDirectory(files: MinioObject[])` 将扁平文件列表按路径中第一层子目录分组：

```
{
  '__root__': [full.md, full.json],   // 无子目录的文件
  'images':   [page_1_img_1.png, ...]
}
```

渲染逻辑：

- 根目录文件直接列出（现有样式保持不变）
- 子目录渲染为可展开的文件夹行，点击展开/折叠子文件列表
- 子目录展开状态用 `Map<string, boolean>` 管理
- 解析产物卡片头部新增"下载全部"按钮，点击调用 `POST /__proxy/upload/parsed-zip`，返回 blob 后触发浏览器下载

**后端新增接口**（`upload-server.mjs`）：

```javascript
// POST /parsed-zip
// Body: { materialId: string | number }
// Response: application/zip（流式）
```

- 调用已有的 `listAllObjects(parsedBucket, `parsed/${materialId}`)` 获取文件列表
- 循环调用 `getObjectBuffer` 读取每个文件内容
- 用 `JSZip` 按相对路径打包（去掉 `parsed/{materialId}/` 前缀）
- 生成 nodebuffer 后 `res.send()`，设置 `Content-Disposition: attachment; filename="parsed-{materialId}.zip"`
- 文件数量为 0 时返回 400

---

## 实现注意事项

1. **向后兼容**：`/parse/analyze` 接口旧调用方（传 `aiApiEndpoint/aiApiKey/aiModel`）不需要修改调用格式，后端自动识别并包装为单元素 providers 数组
2. **429 不重试**：在 `analyzeWithFallback` 中检测 HTTP 4xx 类错误，对 429/401/403 立即跳下一个提供商，避免无效重试消耗时间
3. **Ollama 无 Authorization**：`callAiProvider` 中 apiKey 为空串时不发送 `Authorization` header
4. **ZIP 下载大文件**：`/parsed-zip` 接口先并发读取所有文件（`Promise.all`），再打包，考虑并发数量控制（每批最多 10 个并发，防止 MinIO 连接过载）
5. **目录树性能**：`groupByDirectory` 是纯内存分组，解析产物文件通常不超过数十个，O(n) 时间复杂度无瓶颈
6. **设置页兼容初始化**：读取旧版 `aiConfig`（无 `providers` 字段）时，自动从 `apiEndpoint/apiKey/model` 构建默认 `providers` 数组，防止旧数据丢失

---

## 目录结构

```
src/
├── store/
│   ├── types.ts          [MODIFY] 新增 AiProvider 接口；AiConfig 增加 providers?: AiProvider[] 字段
│   └── mockData.ts       [MODIFY] initialAiConfig 新增 providers 默认数组（Moonshot启用，其余禁用）
├── app/
│   ├── pages/
│   │   ├── AssetDetailPage.tsx   [MODIFY]
│   │   │   - FileLineageCard：B2原文件名折叠路径；B3目录树+整包下载按钮
│   │   │   - AI元数据分析面板：B1原文件名/识别名称信息条
│   │   │   - handleAiAnalyze：传递 providers 新格式
│   │   └── SettingsPage.tsx      [MODIFY]
│   │       - AI配置Tab：改为多提供商卡片管理UI
│   └── components/
│       └── BatchUploadModal.tsx  [MODIFY] 传递 providers 新格式调用 /parse/analyze
server/
└── upload-server.mjs     [MODIFY]
    - 新增 callAiProvider() 函数
    - 新增 analyzeWithFallback() 函数
    - 重构 POST /parse/analyze 接口（向后兼容）
    - 新增 POST /parsed-zip 接口
```