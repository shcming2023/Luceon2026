import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';
import { AppProvider } from '../store/appContext';
import { Layout } from './components/Layout';
import { SourceMaterialsPage } from './pages/SourceMaterialsPage';
import { AssetDetailPage } from './pages/AssetDetailPage';
import { ProductsPage } from './pages/ProductsPage';
import { MetadataManagementPage } from './pages/MetadataManagementPage';
import { SettingsPage } from './pages/SettingsPage';
import { ErrorBoundary } from './components/ErrorBoundary';
import { LatexToolPage } from './pages/backup/LatexToolPage';

export default function App() {
  return (
    <ErrorBoundary>
      <AppProvider>
        {/* BrowserRouter basename="/cms" — Nginx 以 /cms/ 前缀提供服务 */}
        <BrowserRouter basename="/cms">
          <Layout>
            <ErrorBoundary>
              <Routes>
                {/* ── 子系统一：EduAsset CMS ─────────────────────────────── */}
                {/* 默认重定向到原始资料库 */}
                <Route path="/" element={<Navigate to="/source-materials" replace />} />
                {/* 原始资料库：文件上传、MinerU OCR 解析、AI 清洗打标签（完成） */}
                <Route path="/source-materials" element={<SourceMaterialsPage />} />
                {/* 资产详情：解析结果查看、字段编辑、AI 规则配置（完成） */}
                <Route path="/asset/:id" element={<AssetDetailPage />} />
                {/* 成品库：查看由资料生成的成品资产（完成） */}
                <Route path="/products" element={<ProductsPage />} />
                {/* 元数据管理：灵活标签/AI规则/成品分类管理（完成） */}
                <Route path="/metadata" element={<MetadataManagementPage />} />
                {/* 系统设置：API Key 配置、存储设置（完成） */}
                <Route path="/settings" element={<SettingsPage />} />

                {/* ── 子系统二：LaTeX 工具集 ────────────────────────────────── */}
                <Route path="/backup/latex" element={<LatexToolPage />} />
              </Routes>
            </ErrorBoundary>
          </Layout>
          <Toaster position="top-right" richColors />
        </BrowserRouter>
      </AppProvider>
    </ErrorBoundary>
  );
}
