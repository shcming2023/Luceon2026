---
name: eduasset-cms-round2-revision
overview: 针对第二轮UAT诊断报告的7个问题（BUG-06/08/14/15 + NEW-01/02/03）进行修复，涵盖编辑保存状态覆盖、单条/批量AI分析、成品库删除、元数据删除、Dashboard图表动态化、占位符清理。
design:
  architecture:
    framework: react
    component: shadcn
  styleKeywords:
    - Enterprise CMS
    - Dark Sidebar
    - Light Content
    - Card-based Layout
    - Blue Primary (#3b82f6)
    - Functional Enhancement
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
  - id: fix-bug06-p0
    content: "修复BUG-06: AssetDetailPage handleSaveEdit补充aiStatus必填字段防止undefined覆盖"
    status: completed
  - id: implement-analysis-p1
    content: "实现BUG-08+NEW-01: 提取triggerAnalysis公共函数，绑定单条和批量启动分析真实逻辑"
    status: completed
  - id: dashboard-charts-p2
    content: "实现NEW-02: Dashboard weeklyData/monthlyTrend从硬编码改为Store数据动态派生"
    status: completed
  - id: product-delete-p2
    content: "实现BUG-14: 成品库ProductsPage新增DELETE_PRODUCT action/reducer/UI删除功能"
    status: completed
  - id: metadata-delete-p2
    content: "实现BUG-15: 元数据管理页面对接标签/规则真实删除reducer，优化字段删除提示"
    status: completed
  - id: cleanup-placeholders-p2
    content: "完成NEW-03: 清理全局多页面占位按钮toast文案，统一为友好提示"
    status: completed
---

## 产品概述

基于第二轮UAT诊断报告（修复率60%，9/15已修复），系统性修复第一轮遗留的4个未解决问题及本轮新发现的3个问题。涵盖1个P0紧急Bug、2个P1核心功能缺失、4个P2体验优化。

## 核心功能

- **BUG-06 [P0]**: AssetDetailPage `handleSaveEdit` 缺少 `aiStatus` 必填字段导致reducer覆盖为undefined，引发状态异常
- **BUG-08 + NEW-01 [P1]**: 单条/批量"启动分析"按钮仅有toast提示，需提取公共AI触发函数并绑定真实逻辑
- **NEW-02 [P2]**: Dashboard 图表数据(weeklyData/monthlyTrend)仍为硬编码静态数组，需与Store数据联动
- **BUG-14 [P2]**: ProductsPage 缺乏删除功能（无删除按钮+无action+无reducer）
- **BUG-15 [P2]**: MetadataManagementPage 删除字段/标签/规则时仅有toast，未触发真实删除操作
- **NEW-03 [P2]**: 全局多页面大量占位按钮仅弹出toast.info('xxx功能开发中')，需统一清理优化

## Tech Stack

- 前端框架: React 18 + TypeScript + Vite 6 + Tailwind CSS 4
- 状态管理: React Context + useReducer (现有架构)
- UI 组件库: shadcn/ui (Radix UI 原语组件)
- 图表: Recharts
- 通知系统: sonner

## 实现方案

### BUG-06修复策略 (handleSaveEdit aiStatus缺失)

**根因分析**: types.ts第279行定义`UPDATE_MATERIAL_AI_STATUS`的payload中aiStatus为必填字段；appReducer.ts第116行直接展开`aiStatus,`赋值；但AssetDetailPage.tsx第160-177行的handleSaveEdit dispatch时未传入aiStatus，导致reducer将其覆盖为undefined。
**修复**: 在handleSaveEdit的dispatch payload中补充`aiStatus: assetData?.aiStatus ?? 'pending'`，从当前assetData读取现有值保持不变。

### BUG-08 + NEW-01修复策略 (启动分析功能)

**核心思路**: 将handleUploadConfirm(第762-1141行)中的MinerU+AI完整流程提取为独立异步函数`triggerAnalysis(materialId, material)`，供三处复用:

1. 上传后自动分析 -- handleUploadConfirm内部调用(保持不变)
2. 单条"启动分析"按钮(第1413行) -- 对pending状态的资料卡片绑定
3. 批量"AI分析"按钮(第1311行) -- 遍历selectedIds逐个触发
**约束**: 触发前检查previewUrl是否存在(有文件才能分析)、mineruConfig.apiKey和aiConfig.apiKey是否已配置。复用已有工具函数(withTimeout/resolveMineruCreateEndpoint/parseAiJson等)。

### BUG-14修复策略 (成品库删除功能)

- types.ts新增`{ type: 'DELETE_PRODUCT'; payload: number[] }`
- appReducer.ts实现DELETE_PRODUCT reducer(过滤products数组)
- ProductsPage.tsx添加: 卡片悬停区删除按钮 + 底部操作栏删除按钮 + 删除确认Dialog(复用SourceMaterialsPage的Dialog模式)

### BUG-15修复策略 (元数据管理真实删除)

- types.ts新增`DELETE_FLEXIBLE_TAG`(payload: number[])和`DELETE_AI_RULE`(payload: number[])
- appReducer.ts实现两个reducer
- MetadataManagementPage.tsx三处修改:
- 字段删除(第92-96行): STRUCTURED_FIELDS为本地常量数组 -> 改为友好提示"预置字段不可手动删除"
- 标签删除(第197-202行): toast改为dispatch DELETE_FLEXIBLE_TAG + 确认弹窗
- 规则删除(第450-455行): toast改为dispatch DELETE_AI_RULE + 确认弹窗

### NEW-02修复策略 (Dashboard图表动态化)

- weeklyData: 从硬编码7天数据改为useMemo派生 -- 基于materials的uploadTimestamp按星期聚合(最近7天)，fallback到默认值避免空白
- monthlyTrend: 同理按月聚合最近7个月数据
- 保持现有的Recharts BarChart/AreaChart配置不变

### NEW-03修复策略 (占位按钮清理)

- **原则**: 功能性非核心按钮保留UI但优化文案为"该功能即将上线"；创建类按钮同上
- **涉及位置**: AssetDetailPage(分享/在线预览/版本创建/版本预览)、ProductsPage(使用统计/生成新成品)、MetadataManagementPage(添加字段/创建标签/新建规则)、SourceMaterialsPage(批量下载)

## 架构设计

采用分层架构修改，涉及Store层(types+reducer)和Pages层6个文件。数据流遵循现有模式: UI事件 -> dispatch(action) -> reducer更新state -> 组件重渲染。

## 目录结构

```
/workspace/dev/Ui/src/
├── store/
│   ├── types.ts                     # [MODIFY] 新增3个Action类型
│   ├── appReducer.ts                # [MODIFY] 实现3个新reducer
├── app/pages/
│   ├── AssetDetailPage.tsx          # [MODIFY] BUG-06补aiStatus + NEW-03文案优化
│   ├── SourceMaterialsPage.tsx      # [MODIFY] BUG-08/NEW-01提取triggerAnalysis并绑定
│   ├── ProductsPage.tsx             # [MODIFY] BUG-14新增删除功能 + NEW-03文案优化
│   ├── MetadataManagementPage.tsx   # [MODIFY] BUG-15对接真实删除 + NEW-03文案优化
│   └── Dashboard.tsx                # [MODIFY] NEW-02图表数据动态化
```

## 关键代码结构

```typescript
// types.ts 新增Action类型
| { type: 'DELETE_PRODUCT'; payload: number[] }
| { type: 'DELETE_FLEXIBLE_TAG'; payload: number[] }
| { type: 'DELETE_AI_RULE'; payload: number[] }

// AssetDetailPage.tsx handleSaveEdit修复要点
const handleSaveEdit = () => {
    dispatch({
      type: 'UPDATE_MATERIAL_AI_STATUS',
      payload: {
        id: assetId,
        aiStatus: assetData?.aiStatus ?? 'pending', // 新增：保持当前状态不变
        title: editForm.title,
        metadata: { subject, grade, type, standard, summary },
      },
    });

// SourceMaterialsPage.tsx triggerAnalysis函数签名
const triggerAnalysis = async (materialId: number, material: Material): Promise<void>;
// 从handleUploadConfirm提取Step2~Step5(MinerU轮询+下载zip+AI识别+dispatch结果)
```

延续现有项目的现代企业级CMS风格：深色侧边栏(#0F172A)+浅色内容区(#F8FAFC)、蓝色主色调(#3b82f6)、卡片式布局(rounded-xl)、圆角设计风格(rounded-lg/xl)。所有修改严格遵循现有UI规范和交互模式，不引入新的视觉语言。本次修订主要涉及功能层面的完善，UI改动集中在新增的删除确认弹窗、启动分析按钮的状态变化反馈、以及Dashboard图表数据的动态展示。

## SubAgent

- **code-explorer**
- Purpose: 在执行阶段精确定位代码行号和上下文，确保每处修改点准确无误
- Expected outcome: 所有7个问题的修改位置得到二次验证，降低回归风险