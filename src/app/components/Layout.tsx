import type { ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  FolderOpen,
  Tag,
  Settings,
  ChevronRight,
  FileText,
  Package,
} from 'lucide-react';

interface NavItem {
  label: string;
  path: string;
  icon: ReactNode;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: '内容管理',
    items: [
      { label: '原始资料',   path: '/source-materials',  icon: <FolderOpen size={18} /> },
      { label: '成品库',     path: '/products',          icon: <Package size={18} /> },
      { label: '元数据管理', path: '/metadata',           icon: <Tag size={18} /> },
      { label: '系统设置',   path: '/settings',           icon: <Settings size={18} /> },
    ],
  },
  {
    title: 'Overleaf 备份',
    items: [
      { label: 'LaTeX 工具', path: '/backup/latex',      icon: <FileText size={18} /> },
    ],
  },
];

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/';
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-white border-r border-gray-200 flex flex-col">
        {/* Logo */}
        <div className="h-14 flex items-center px-5 border-b border-gray-100">
          <span className="font-bold text-base text-gray-800">教材资料 CMS</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 overflow-y-auto">
          {NAV_GROUPS.map((group, gi) => (
            <div key={group.title}>
              {gi > 0 && <div className="mx-4 my-2 border-t border-gray-100" />}
              <div className="px-4 py-1 text-xs font-semibold text-gray-400 uppercase tracking-wide">
                {group.title}
              </div>
              {group.items.map((item) => {
                const active = isActive(item.path);
                return (
                  <button
                    key={item.path}
                    onClick={() => navigate(item.path)}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm font-medium transition-colors rounded-lg mx-1 mb-0.5
                      ${active
                        ? 'bg-blue-50 text-blue-700'
                        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                      }`}
                    style={{ width: 'calc(100% - 8px)' }}
                  >
                    <span className={active ? 'text-blue-600' : 'text-gray-400'}>{item.icon}</span>
                    <span className="flex-1 text-left">{item.label}</span>
                    {active && <ChevronRight size={14} className="text-blue-400" />}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-gray-100 text-xs text-gray-400">
          v0.0.1
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
