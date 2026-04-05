import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'sonner';
import { AppProvider } from '../store/appContext';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { SourceMaterialsPage } from './pages/SourceMaterialsPage';
import { AssetDetailPage } from './pages/AssetDetailPage';
import { ProcessWorkbenchPage } from './pages/ProcessWorkbenchPage';
import { ProductsPage } from './pages/ProductsPage';
import { MetadataManagementPage } from './pages/MetadataManagementPage';
import { TaskCenterPage } from './pages/TaskCenterPage';
import { SettingsPage } from './pages/SettingsPage';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ProjectBackupPage } from './pages/backup/ProjectBackupPage';
import { DatabaseBackupPage } from './pages/backup/DatabaseBackupPage';
import { LatexToolPage } from './pages/backup/LatexToolPage';
import { FilesBrowserPage } from './pages/backup/FilesBrowserPage';
import { SchedulerPage } from './pages/backup/SchedulerPage';

export default function App() {
  return (
    <ErrorBoundary>
      <AppProvider>
        <BrowserRouter>
          <Layout>
            <ErrorBoundary>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/source-materials" element={<SourceMaterialsPage />} />
                <Route path="/asset/:id" element={<AssetDetailPage />} />
                <Route path="/process-workbench" element={<ProcessWorkbenchPage />} />
                <Route path="/products" element={<ProductsPage />} />
                <Route path="/metadata" element={<MetadataManagementPage />} />
                <Route path="/tasks" element={<TaskCenterPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                {/* Overleaf 备份系统路由 */}
                <Route path="/backup" element={<ProjectBackupPage />} />
                <Route path="/backup/database" element={<DatabaseBackupPage />} />
                <Route path="/backup/latex" element={<LatexToolPage />} />
                <Route path="/backup/files" element={<FilesBrowserPage />} />
                <Route path="/backup/scheduler" element={<SchedulerPage />} />
              </Routes>
            </ErrorBoundary>
          </Layout>
          <Toaster position="top-right" richColors />
        </BrowserRouter>
      </AppProvider>
    </ErrorBoundary>
  );
}
