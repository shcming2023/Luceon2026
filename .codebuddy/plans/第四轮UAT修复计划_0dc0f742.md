---
name: 第四轮UAT修复计划
overview: 修复第四轮 UAT 发现的 2 个 P0 级语法错误：SourceMaterialsPage 括号不匹配（NEW-06）和 ProductsPage JSX 结构错误（NEW-07），使项目恢复编译能力。
todos:
  - id: fix-syntax-errors
    content: 修复 SourceMaterialsPage.tsx 第1340行括号 + ProductsPage.tsx 第286-289行多余JSX
    status: completed
  - id: verify-build
    content: 运行 pnpm run build 验证编译通过
    status: completed
    dependencies:
      - fix-syntax-errors
---

## 产品概述

根据《EduAsset CMS - 第四轮 UAT 诊断报告》修复 2 个 P0 级语法错误，使项目恢复可编译、可启动状态。

## 核心功能

- **NEW-06 [P0]**：修复 `SourceMaterialsPage.tsx` 第 1340 行 `dispatch` 调用括号不匹配（缺少一个 `}`）
- **NEW-07 [P0]**：修复 `ProductsPage.tsx` 第 286-289 行多余的、缺少开标签的下载按钮 JSX 代码

## 技术栈

- React + TypeScript + Vite (esbuild)

## 实现方案

两个问题均为明确的语法错误，修复方式确定且无歧义：

1. **NEW-06**：在 `SourceMaterialsPage.tsx` 第 1340 行，将 `} );` 修正为 `} });`（补全 payload 对象的闭合 `}`）
2. **NEW-07**：在 `ProductsPage.tsx` 中，删除第 286-289 行共 4 行多余的代码片段（该片段是下载按钮的重复残留，缺少 `<button>` 开标签，导致 JSX 解析失败）

修复后运行 `pnpm run build` 验证编译通过。

## 修改文件

```
src/app/pages/SourceMaterialsPage.tsx  # [MODIFY] 修正第1340行括号
src/app/pages/ProductsPage.tsx         # [MODIFY] 删除第286-289行多余代码
```