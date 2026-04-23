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
import { WorkspacePage } from './pages/WorkspacePage';
import { TaskManagementPage } from './pages/TaskManagementPage';
import { TaskDetailPage } from './pages/TaskDetailPage';
import { AuditPage } from './pages/AuditPage';

export default function App() {
  return (
    <ErrorBoundary>
      <AppProvider>
        {/* BrowserRouter basename="/cms" — Nginx 以 /cms/ 前缀提供服务 */}
        <BrowserRouter basename="/cms">
          <Layout>
            <ErrorBoundary>
              <Routes>
                <Route path="/" element={<Navigate to="/workspace" replace />} />

                {/* ── 子系统一：EduAsset CMS ─────────────────────── */}
                <Route path="/workspace" element={<WorkspacePage />} />
                <Route path="/tasks" element={<TaskManagementPage />} />
                <Route path="/tasks/:id" element={<TaskDetailPage />} />
                <Route path="/source-materials" element={<Navigate to="/workspace" replace />} />
                <Route path="/legacy/source-materials" element={<SourceMaterialsPage />} />
                {/* 资产详情：解析结果查看、字段编辑、AI 规则配置 */}
                <Route path="/asset/:id" element={<AssetDetailPage />} />
                {/* 已处理资料库：检索已完成 MinerU 解析 + AI 元数据识别的资产 */}
                <Route path="/library" element={<ProductsPage />} />
                <Route path="/products" element={<Navigate to="/library" replace />} />
                {/* 元数据管理：灵活标签/AI规则/成品分类管理 */}
                <Route path="/metadata" element={<Navigate to="/settings?tab=dictionary" replace />} />
                <Route path="/legacy/metadata" element={<MetadataManagementPage />} />
                {/* 系统设置：API Key 配置、存储设置 */}
                <Route path="/settings" element={<SettingsPage />} />
                {/* 运维审计：查看数据一致性扫描结果 */}
                <Route path="/audit" element={<AuditPage />} />

                {/* ── 子系统二：LaTeX 工具集 ──────────────────────── */}
                <Route path="/backup/latex" element={<LatexToolPage />} />

                {/* 兜底重定向 */}
                <Route path="*" element={<Navigate to="/workspace" replace />} />
              </Routes>
            </ErrorBoundary>
          </Layout>
          <Toaster position="top-right" richColors />
        </BrowserRouter>
      </AppProvider>
    </ErrorBoundary>
  );
}
