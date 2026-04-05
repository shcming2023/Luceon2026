import { useState, useEffect, useCallback } from 'react';
import { backupFetch, formatSize } from '../../../utils/backupApi';
import { FolderOpen, File, ChevronRight, Home } from 'lucide-react';

interface FileEntry {
  name: string;
  isDirectory: boolean;
  size?: number;
}

interface FilesResponse {
  currentPath: string;
  files: FileEntry[];
}

export function FilesBrowserPage() {
  const [currentPath, setCurrentPath] = useState('');
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const loadFiles = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const res = await backupFetch<FilesResponse>(`/backup/files?path=${encodeURIComponent(path)}`);
      setCurrentPath(res.currentPath ?? path);
      setFiles(res.files ?? []);
    } catch (_) {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFiles('');
  }, [loadFiles]);

  const navigate = (path: string) => {
    loadFiles(path);
  };

  const goUp = () => {
    if (!currentPath) return;
    const parts = currentPath.replace(/\\/g, '/').split('/').filter(Boolean);
    parts.pop();
    loadFiles(parts.join('/'));
  };

  // 面包屑
  const crumbs = currentPath ? currentPath.replace(/\\/g, '/').split('/').filter(Boolean) : [];

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">文件浏览</h1>
        <p className="text-sm text-gray-500 mt-0.5">浏览备份目录中的文件</p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        {/* 面包屑 */}
        <div className="flex items-center gap-1 text-sm text-gray-500 mb-4 flex-wrap">
          <button onClick={() => navigate('')} className="flex items-center gap-1 text-blue-600 hover:text-blue-800">
            <Home size={14} /> 根目录
          </button>
          {crumbs.map((part, i) => {
            const path = crumbs.slice(0, i + 1).join('/');
            return (
              <span key={path} className="flex items-center gap-1">
                <ChevronRight size={14} />
                <button onClick={() => navigate(path)} className="text-blue-600 hover:text-blue-800">
                  {part}
                </button>
              </span>
            );
          })}
        </div>

        {loading && <p className="text-sm text-gray-400">加载中...</p>}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {/* 上级目录 */}
          {currentPath && (
            <button
              onClick={goUp}
              className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg text-left hover:bg-gray-100 transition-colors"
            >
              <FolderOpen size={18} className="text-yellow-500 flex-shrink-0" />
              <span className="text-sm text-gray-700">..</span>
            </button>
          )}

          {files.map((f) => (
            <button
              key={f.name}
              onClick={() => f.isDirectory ? navigate(currentPath ? `${currentPath}/${f.name}` : f.name) : undefined}
              disabled={!f.isDirectory}
              className={`flex items-center gap-3 p-3 bg-gray-50 rounded-lg text-left transition-colors
                ${f.isDirectory ? 'hover:bg-gray-100 cursor-pointer' : 'cursor-default opacity-80'}`}
            >
              {f.isDirectory
                ? <FolderOpen size={18} className="text-yellow-500 flex-shrink-0" />
                : <File size={18} className="text-gray-400 flex-shrink-0" />
              }
              <div className="min-w-0">
                <div className="text-sm text-gray-800 truncate">{f.name}</div>
                {!f.isDirectory && f.size != null && (
                  <div className="text-xs text-gray-400">{formatSize(f.size)}</div>
                )}
              </div>
            </button>
          ))}

          {!loading && files.length === 0 && !currentPath && (
            <p className="text-sm text-gray-400 col-span-full">目录为空</p>
          )}
        </div>
      </div>
    </div>
  );
}
