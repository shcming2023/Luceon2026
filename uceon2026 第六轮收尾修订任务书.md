Copilot said: # 《Luceon2026 第六轮收尾修订任务书》 **任务性质**：第五轮完成后的生产收尾修订 **基线版本**：`44e807c` **目标**：消除正式投用前的关键风险，完成从“准生产可用”到“稳定生产可用”的最后一轮修订 --- ##
《Luceon2026 第六轮收尾修订任务书》
任务性质：第五轮完成后的生产收尾修订
基线版本：44e807c
目标：消除正式投用前的关键风险，完成从“准生产可用”到“稳定生产可用”的最后一轮修订

一、任务背景
Luceon2026 在第五轮修订后，已经完成以下关键建设：

本地 MinerU 引擎接入；
官方 API / 本地引擎双模式切换；
资料库高级筛选与统计摘要；
资产详情增强（标题编辑、Markdown 导出、未保存提示）；
基础容量统计；
JSON 级备份导出 / 导入。
当前版本 44e807c 已具备较强的功能完整性和较好的管理体验，但经过全面评审后确认，仍有若干问题会直接影响正式生产使用的可靠性与资产安全，主要集中在：

本地 MinerU 接口契约未完全对齐，存在解析失败风险；
备份恢复仅覆盖 JSON 元数据，未覆盖 MinIO 物理文件，无法形成完整资产备份；
导入恢复后前端状态不自动同步，存在数据一致性风险；
容量监控只有“已用量”，没有“容量管理”和告警阈值；
资产再利用链路仍未从资料层延伸到成品层。
因此需要开展第六轮收尾修订，以确保当前已实现能力可靠、有效、稳定地投入正式使用。

二、总体目标
第六轮收尾修订的目标不是大幅扩展新功能，而是围绕现有成果做稳定性收口、接口收口、资产安全收口、运维收口。

本轮四大目标
严格修正本地 MinerU 的接口适配，确保与宿主机已部署服务完全兼容；
完成“完整资产备份与恢复”能力，覆盖 JSON + 原始文件 + 解析产物；
修复导入恢复后的前端状态一致性，避免用户看到旧数据或误覆盖新数据；
补齐容量管理与告警机制，并为资产再利用打通最小闭环。
三、任务清单
模块 A：本地 MinerU 契约收口与稳定性修复
A-1. 严格对齐本地 MinerU 请求参数
优先级：P0

背景
当前 upload-server.mjs 中 /parse/local-mineru 发送给本地 MinerU 的参数包括：

language
enableOcr
enableFormula
enableTable
enable_ocr
enable_formula
enable_table
但用户已明确提供本地 MinerU 接口契约要求如下：

backend=hybrid-auto-engine
max_pages=1000
ocr_language=ch
table_enable=true
formula_enable=true
风险
若当前本地 MinerU 服务严格按既定契约校验参数，现有请求可能：

无法被识别；
解析参数被忽略；
在服务升级后直接失效。
修订要求
修改 server/upload-server.mjs 中 POST /parse/local-mineru 的 FormData 构造逻辑：

必须新增的标准字段
ts
form.append('backend', req.body?.backend || 'hybrid-auto-engine');
form.append('max_pages', String(req.body?.maxPages || 1000));
form.append('ocr_language', req.body?.language || 'ch');
form.append('table_enable', String(isEnabledFlag(req.body?.enableTable)));
form.append('formula_enable', String(isEnabledFlag(req.body?.enableFormula)));
兼容策略
可以暂时保留旧字段 language / enable_table / enable_formula 等，作为兼容兜底；
但标准字段必须优先发送；
后续文档以标准字段为准。
涉及文件
server/upload-server.mjs
src/utils/mineruLocalApi.ts（如需传 backend/maxPages）
src/store/types.ts（如决定把 localBackend、localMaxPages 纳入配置）
src/store/mockData.ts
src/app/pages/SettingsPage.tsx
A-2. 补齐本地 MinerU 返回结构兼容
优先级：P0

背景
用户提供的本地 MinerU 示例返回为：

Python
result = response.json()
md_text = result[0]["text"]
当前 extractLocalMarkdown(payload) 没有明确适配：

ts
Array.isArray(payload) && payload[0]?.text
风险
即使本地解析成功，前端仍可能报：

本地 MinerU 未返回 Markdown 内容

修订要求
在 extractLocalMarkdown(payload) 中新增优先级明确的适配逻辑：

ts
if (Array.isArray(payload) && payload.length > 0 && typeof payload[0]?.text === 'string') {
  return payload[0].text.trim();
}
同时保留现有：

payload.md_text
payload.markdown
payload.text
payload.data?.md_text
payload.output?.markdown
以兼容其他部署版本。

涉及文件
server/upload-server.mjs
A-3. 修正本地 MinerU 健康检查判定标准
优先级：P1

背景
当前逻辑只要某个候选地址返回 HTTP < 500 即视为“本地在线”。

风险
404/405 也可能被错误显示为“服务在线”。

修订要求
健康检查应调整为更严格的判定：

推荐策略
首先请求：
GET {localEndpoint}/
返回 200 才视为健康；
如失败，再尝试：
GET {localEndpoint}/gradio_api/info
返回 200 才视为健康；
404/405 不应判定为可用。
涉及文件
server/upload-server.mjs
src/utils/mineruLocalApi.ts
src/app/pages/SettingsPage.tsx
A-4. 补充本地 MinerU 配置项，消除“写死参数”
优先级：P1

背景
当前虽然已有：

engine
localEndpoint
localTimeout
但用户明确给出了本地服务可配置项：

backend
max_pages
ocr_language
table_enable
formula_enable
其中部分参数当前是代码中写死或间接推断，不利于运维调整。

修订要求
扩展 MinerUConfig：

ts
export interface MinerUConfig {
  engine: 'local' | 'cloud';
  localEndpoint: string;
  localTimeout: number;
  localBackend: string;
  localMaxPages: number;
  localOcrLanguage: string;

  apiMode: 'precise' | 'agent';
  apiEndpoint: string;
  apiKey: string;
  timeout: number;
  modelVersion: 'pipeline' | 'vlm';
  enableOcr: boolean;
  enableFormula: boolean;
  enableTable: boolean;
  language: string;
  maxFileSize: number;
  maxPages: number;
}
默认值建议：

ts
engine: 'local'
localEndpoint: 'http://192.168.31.33:8083'
localTimeout: 300
localBackend: 'hybrid-auto-engine'
localMaxPages: 1000
localOcrLanguage: 'ch'
SettingsPage 需要新增本地配置表单项
本地地址
本地超时
backend
max_pages
OCR 语言
涉及文件
src/store/types.ts
src/store/mockData.ts
src/app/pages/SettingsPage.tsx
src/utils/mineruLocalApi.ts
server/upload-server.mjs
模块 B：完整资产备份与恢复
B-1. 当前 JSON 备份保留，但明确降级为“元数据备份”
优先级：P0

背景
当前已有：

GET /backup/export
POST /backup/import
但实际上这只备份了 dbCache，即：

materials
assetDetails
processTasks
tasks
products
flexibleTags
aiRules
settings
并不包含 MinIO 中的原始文件和解析产物。

风险
当前“备份”如果被误解为完整备份，会在灾难恢复时造成严重数据误判。

修订要求
先不删除现有接口，但必须：

在接口文档和 UI 文案中明确标注为：
“JSON 元数据备份”
在前端导出按钮文案中改成：
导出元数据 JSON
在导入按钮附近明确提示：
“仅恢复数据库记录，不恢复 MinIO 文件”
涉及文件
server/db-server.mjs
src/app/pages/SettingsPage.tsx
说明文档.md
B-2. 新增完整资产备份导出接口
优先级：P0

目标
实现真正意义上的“完整资产备份”，包含：

JSON 数据库内容；
MinIO 原始资料文件；
MinIO 解析产物文件。
建议输出结构
压缩包结构示例：

Text
luceon2026-full-backup-2026-04-10/
├── db/
│   └── db-data.json
├── originals/
│   └── originals/{materialId}/...
├── parsed/
│   └── parsed/{materialId}/...
└── manifest.json
manifest.json 内容建议
JSON
{
  "version": "1.0.0",
  "createdAt": "2026-04-10T18:30:00Z",
  "materialsCount": 156,
  "rawObjectCount": 156,
  "parsedObjectCount": 892,
  "dbFile": "db/db-data.json"
}
后端实现建议
新增 upload-server.mjs 路由：

POST /backup/full-export
流程：

调用 db-server 获取完整 JSON；
遍历 materials，收集可能涉及的对象：
metadata.objectName
metadata.markdownObjectName
parsed/{id}/ 前缀下所有对象；
从 MinIO 拉取对象流；
使用 archiver 或 tar-stream 打包；
流式返回压缩包给前端。
涉及文件
server/upload-server.mjs
package.json / 服务端依赖（新增 archiver 若尚未安装）
src/app/pages/SettingsPage.tsx
B-3. 新增完整资产恢复接口
优先级：P0

目标
支持把完整备份包导回系统，包括：

数据库 JSON；
原始文件；
解析产物。
建议新增接口
POST /backup/full-import
恢复流程建议
接收上传的 zip/tar.gz；
解压到临时目录；
校验 manifest.json；
恢复 db-data.json 到 db-server；
遍历 originals/ 上传回 MinIO raw bucket；
遍历 parsed/ 上传回 MinIO parsed bucket；
返回恢复统计结果。
重要约束
必须提供模式选择：

replace：覆盖现有数据；
merge：仅导入不存在对象。
涉及文件
server/upload-server.mjs
server/db-server.mjs
src/app/pages/SettingsPage.tsx
模块 C：导入恢复后的前端状态一致性修复
C-1. 导入 JSON 后强制刷新应用状态
优先级：P0

背景
当前 SettingsPage.tsx 导入成功后只刷新统计，不刷新实际业务状态。

风险
用户导入成功后仍看到旧数据；
后续操作可能把旧 state 再次写回后端；
恢复结果不可信。
修订要求
最简单可靠的处理方式：

方案 A（推荐）
导入成功后：

清除相关 localStorage；
弹 toast：
“导入成功，页面即将刷新以加载最新数据”
setTimeout(() => window.location.reload(), 1200)
需要清理的 localStorage keys
参考 appContext.tsx 中的 LS：

app_ai_config
app_mineru_config
app_minio_config
app_materials
app_process_tasks
app_tasks
app_products
app_asset_details
app_flexible_tags
app_ai_rules
app_ai_rule_settings
涉及文件
src/app/pages/SettingsPage.tsx
C-2. 为导入恢复增加“危险操作确认”
优先级：P1

背景
目前导入前只有浏览器 window.confirm。

修订要求
增加更明确的恢复提示：

导入 JSON 会覆盖当前数据库；
完整恢复会覆盖数据库与 MinIO 文件；
覆盖前会自动创建 .bak 或服务端备份快照。
建议
用统一 toast/弹窗方案，而不是裸 confirm。

涉及文件
src/app/pages/SettingsPage.tsx
模块 D：容量管理与告警
D-1. 将“用量统计”升级为“容量管理”
优先级：P1

背景
当前只展示已用量：

JSON 大小
对象存储总大小
总占用
没有：

上限；
剩余量；
告警阈值。
修订要求
在设置页新增可配置软上限：

ts
{
  dbSoftLimitMB: number;
  storageSoftLimitGB: number;
}
默认建议：

dbSoftLimitMB = 100
storageSoftLimitGB = 20
前端展示要求
显示使用率百分比；
超过 70% 显示黄色；
超过 90% 显示红色；
加提示文案：
“接近容量上限，建议清理或扩容”
涉及文件
src/app/pages/SettingsPage.tsx
src/store/types.ts（如加入 settings）
server/db-server.mjs（如需持久化阈值）
D-2. 容量统计增加 materials 总字节与对象分层占用
优先级：P2

背景
当前 db-server /stats 只给出 fileSize 和各集合 count。

建议增强
db-server /stats 中增加：

materialsTotalSizeBytes
materialsByStatus
materialsBySubject
upload-server /storage-stats 中保留：

raw bucket size
parsed bucket size
这样设置页可以形成更有意义的容量画像。

涉及文件
server/db-server.mjs
src/app/pages/SettingsPage.tsx
模块 E：资产再利用最小闭环
E-1. 从资产详情生成成品的最小入口
优先级：P2

背景
目前资料层管理已很强，但成品层仍是空壳，products 无实质生成入口。

修订目标
在不大幅重构的前提下，先打通一个最小闭环：

在 AssetDetailPage.tsx 中新增按钮：

生成成品
点击后：

从当前 material 和 assetDetails 读取：
title
tags
metadata.subject / grade / type / summary
markdown 内容（若存在）
创建一个最小 Product 记录；
写入：
source
lineage: [material.id]
createdAt
status: 'completed'
价值
即使成品库仍然简单，也能实现：

原始资料 → 解析 → AI 分析 → 成品入库

这能满足“再利用闭环最小成立”的要求。

涉及文件
src/store/types.ts
src/store/appReducer.ts
src/app/pages/AssetDetailPage.tsx
ProductsPage 或相应成品展示页（若存在）
E-2. 写实 lineage 字段
优先级：P2

背景
Product 已定义：

ts
lineage: string[];
但没有实际写入逻辑。

修订要求
在成品生成时必须写：

ts
lineage: [String(material.id)]
如后续支持多资料合成，则扩展为多个源 material id。

模块 F：文档与测试收尾
F-1. 更新说明文档与部署文档
优先级：P1

必须修正文档点
明确区分：
JSON 元数据备份
完整资产备份
本地 MinerU 参数以用户给定标准为准
默认引擎为本地 Gradio
完整恢复需要恢复 MinIO 文件
涉及文件
说明文档.md
如存在 DEPLOY.md、README.md，同步修订
F-2. 增加本地 MinerU 集成测试脚本
优先级：P1

目标
新增服务端测试脚本，验证：

本地健康检查；
本地解析调用；
Markdown 提取；
MinIO 回存；
markdownObjectName / markdownUrl 返回。
建议文件
server/test-local-mineru.mjs
F-3. 增加备份恢复冒烟测试脚本
优先级：P2

目标
验证：

/backup/export
/backup/import
完整备份（如本轮实现）
完整恢复（如本轮实现）
四、优先级排序
第一批：上线前必须完成
P0

A-1 严格修正本地 MinerU 参数契约
A-2 补齐本地 MinerU 返回结构兼容
B-2 新增完整资产备份导出
B-3 新增完整资产恢复
C-1 导入后强制刷新前端状态
第二批：建议本轮一并完成
P1

A-3 修正健康检查判定
A-4 本地配置项完整化
B-1 明确 JSON 备份是元数据备份
C-2 危险操作确认增强
D-1 容量管理与告警
F-1 文档修订
F-2 本地 MinerU 冒烟测试
第三批：下一轮可继续推进
P2

D-2 细化容量画像
E-1 生成成品最小闭环
E-2 写实 lineage
F-3 备份恢复测试脚本
五、涉及文件总表
后端
server/upload-server.mjs
server/db-server.mjs
server/test-local-mineru.mjs（新增）
server/test-backup-restore.mjs（可新增）
前端
src/app/pages/SettingsPage.tsx
src/app/pages/AssetDetailPage.tsx
src/store/types.ts
src/store/mockData.ts
src/utils/mineruLocalApi.ts
文档
说明文档.md
README.md / DEPLOY.md（如存在）
六、交付标准
第六轮完成后，应达到以下验收标准：

本地 MinerU
能按你提供的接口规范稳定调用；
能正确获取 Markdown；
能稳定回存 MinIO；
本地/官方切换后均可用。
备份恢复
能导出完整资产包；
能恢复 JSON + 原始文件 + 解析产物；
恢复后刷新页面即可看到一致状态。
容量与运维
能看到已用量；
能看到软上限和告警；
能明确区分元数据备份和完整备份。
资产再利用
至少能从资产详情生成一个最小成品；
lineage 被真实写入。
七、建议版本策略
当前 44e807c：作为第五轮里程碑版本
第六轮完成后：建议打正式稳定标签
建议版本号
44e807c：v0.9.x
第六轮完成后：v1.0.0