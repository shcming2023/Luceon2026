---
name: mineru-e2e-test-fix
overview: 利用 test/ 目录下的 3 个 PDF 文件，自动化端到端测试 MinerU + AI 解析流程，找出并修复所有阻断问题（API Key 验证、请求体结构、轮询状态字段、Markdown 提取、AI 阶段调用等）
todos:
  - id: fix-settings-endpoint
    content: 修复 SettingsPage.tsx：MINERU_PROXY_ENDPOINT 补 /create 后缀，修复响应解析逻辑
    status: completed
  - id: fix-source-upload
    content: 修复 SourceMaterialsPage.tsx：createBody 补 model_version，拆分 AbortController，catch 用 currentStage 变量
    status: completed
  - id: create-test-script
    content: 新建 server/test-mineru.mjs 端到端测试脚本并用 test/companion_materials_final.pdf 运行验证
    status: completed
    dependencies:
      - fix-source-upload
---

## 用户需求

已知 MinerU 解析流程存在多个经诊断确认的 bug，用户提供了 3 个测试 PDF（`test/`），要求 AI 自行测试、精确修复所有问题，不再依赖人工点击浏览器触发。

## 产品概述

教育资产管理平台的原始资料页具备 MinerU 解析 + Kimi AI 分析两阶段流水线；目前需修复 SettingsPage 测试端点错误、上传请求体缺少 `model_version`、`AbortController` 超时逻辑混乱、轮询无 abort 信号、以及提供可在终端独立运行的端到端测试脚本。

## 核心功能

- **修复 SettingsPage 测试端点**：`MINERU_PROXY_ENDPOINT` 缺少 `/create` 后缀，导致 405
- **补全上传请求体**：`SourceMaterialsPage` 的 `createBody` 缺少 `model_version` 字段
- **修复超时控制**：创建任务与轮询各自拥有独立的 `AbortController`，轮询超时改为 `AbortSignal.timeout` 或独立 controller
- **轮询加 abort 信号**：每次轮询 fetch 传入 signal，避免页面关闭后后台悬空请求
- **新建端到端测试脚本**：`server/test-mineru.mjs`，读取 `test/` 目录的 PDF，通过本地 proxy 发起完整 MinerU 流程并打印结果，AI 可直接在终端运行验证

## 技术栈

现有项目：React + TypeScript + Vite，Vite proxy 转发 MinerU。测试脚本使用 Node.js ESM（与 `server/upload-server.mjs` 保持一致）。

## 实现方案

### 修复策略

所有修复均为最小范围改动，严格对齐已验证的接口事实：

- Proxy curl 测试已确认 `/api/v4/extract/task/create` 路径 100% 可达
- MinerU 返回 `{traceId, msgCode, msg, data, success, total}`
- `data.task_id` 是任务 ID 字段（已在 `extractTaskId` 中覆盖）

### Bug 修复明细

#### Bug 1：`SettingsPage` 测试端点缺 `/create`

```ts
// 修复前
const MINERU_PROXY_ENDPOINT = '/__proxy/mineru/api/v4/extract/task';
// 修复后
const MINERU_PROXY_ENDPOINT = '/__proxy/mineru/api/v4/extract/task/create';
```

测试连接应与真实上传走同一端点。同时响应解析也需对齐真实结构：`data.task_id`（嵌套在 `data` 下）。

#### Bug 2：上传请求体缺少 `model_version`

SettingsPage 测试连接携带 `model_version`，但 `SourceMaterialsPage` 的 `createBody` 遗漏该字段。补充：

```ts
model_version: mineruConfig.modelVersion || initialMinerUConfig.modelVersion,
```

#### Bug 3：`AbortController` 超时逻辑混用

当前 `mineruController` 同时用于创建任务的 `signal` 和整体超时计时，但 `createTaskResp.text()` 读取已在 signal 之外（signal 只覆盖 fetch 网络层），且轮询的 while 循环用独立时间判断，两个超时逻辑并存且互相干扰。

**修复方案**：将 `mineruController` 的 signal 仅用于 `createTask` fetch，轮询引入独立的 `pollController`（在 while 循环开始前创建，pollTimeoutMs 后 abort）并将其 signal 传给每次轮询 fetch：

```ts
// createTask 用独立 controller（30s 固定超时）
const createController = withTimeout(30_000);
// 轮询用独立 controller（mineruConfig.timeout 整体超时）
const pollController = withTimeout(pollTimeoutMs);
```

#### Bug 4：阶段判断依赖字符串脆弱性

`catch` 块中 `isMineruStage = !message.includes('AI')` 判断方式脆弱；引入显式阶段标记变量 `let currentStage: 'mineru' | 'ai' = 'mineru'`，在进入 AI 阶段前赋值，catch 块依赖该变量判断。

### 端到端测试脚本 `server/test-mineru.mjs`

独立 Node.js 脚本，不依赖浏览器：

1. 读取 `test/companion_materials_final.pdf`（最小，345KB，适合快速验证）
2. 转 base64
3. 直接 POST 到 `https://mineru.net/api/v4/extract/task/create`（需传 `MINERU_API_KEY` 环境变量）
4. 轮询 task 状态，每 3s 一次，最多 120s
5. 打印每步响应的完整 JSON，最终输出 markdown 前 500 字符

脚本使用 `MINERU_API_KEY` 环境变量传 key，`MINERU_TEST_FILE` 传文件路径（可选）。

## 实现注意事项

- `SettingsPage` 测试连接的响应解析需对齐真实结构：`(json.data as {task_id?:string})?.task_id || json.task_id || json.id`
- `vite.config.ts` 已加入 proxy 日志中间件，本次不再修改
- 测试脚本使用 `node:fs`、`node:path` 内置模块，无需额外依赖

## 目录结构

```
/workspace/dev/Ui/
├── src/app/pages/
│   ├── SettingsPage.tsx       [MODIFY] 修复第 19 行 MINERU_PROXY_ENDPOINT 缺 /create；
│   │                                   修复第 83 行响应解析对齐嵌套 data.task_id 结构
│   └── SourceMaterialsPage.tsx [MODIFY] createBody 补 model_version；
│                                        拆分 AbortController（createController + pollController）；
│                                        catch 块引入 currentStage 变量替代字符串判断
└── server/
    └── test-mineru.mjs        [NEW]    端到端测试脚本，读取 test/ PDF，
                                        直连 MinerU API，打印完整请求/响应链路
```