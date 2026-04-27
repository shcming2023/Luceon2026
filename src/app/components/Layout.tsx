import type { ReactNode } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import {
  Settings,
  FileText,
  BookOpen,
  Bell,
  GraduationCap,
  ListTodo,
  ShieldCheck,
  Activity,
} from 'lucide-react';
import { BatchProcessingController } from './BatchUploadModal';

/* ── 侧边栏主导航（PRD v0.4 §10.3） ──────────────────── */
const SIDE_NAV = [
  { name: '任务管理',   href: '/tasks',     icon: ListTodo },
  { name: '成果库',     href: '/library',   icon: GraduationCap },
];

/* ── 侧边栏底部导航 ──────────────────────────────────────────── */
const BOTTOM_NAV = [
  { name: '一致性审计',   href: '/audit',             icon: ShieldCheck },
  { name: '系统健康',     href: '/ops/health',        icon: Activity },
  { name: '系统设置',     href: '/settings',          icon: Settings },
  { name: 'LaTeX 工具',   href: '/backup/latex',      icon: FileText },
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
    <div className="h-screen flex flex-col bg-slate-50 overflow-hidden">
      {/* ── 顶部导航栏 ─────────────────────────────────────── */}
      <header className="h-14 bg-white/60 backdrop-blur-md border-b border-slate-200 flex-shrink-0 z-50">
        <div className="h-full px-8 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <Link to="/tasks" className="font-bold text-lg text-blue-700 whitespace-nowrap">
              EduDoc Platform
            </Link>
          </div>

          {/* 右侧：操作按钮 + 通知 + 头像 */}
          <div className="flex items-center gap-3">
            <button className="relative p-2 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors">
              <Bell className="w-5 h-5" />
            </button>
            <button
              onClick={() => navigate('/settings')}
              className="relative p-2 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors"
            >
              <Settings className="w-5 h-5" />
            </button>
            <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 border-2 border-blue-200 flex items-center justify-center text-white text-xs font-bold">
              U
            </div>
          </div>
        </div>
      </header>

      {/* ── 主体区域 ───────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* 侧边栏 */}
        <aside className="w-60 flex-shrink-0 bg-white border-r border-slate-200 flex flex-col overflow-hidden">
          {/* 品牌 Logo 区 */}
          <div className="px-5 pt-5 pb-3">
            <div className="flex items-center gap-3 mb-1.5">
              <div className="w-9 h-9 bg-blue-600 rounded-xl flex items-center justify-center flex-shrink-0">
                <BookOpen className="w-4.5 h-4.5 text-white" />
              </div>
              <div className="leading-tight">
                <div className="font-extrabold text-blue-700 text-sm">EduDoc</div>
                <div className="font-extrabold text-blue-700 text-sm">Platform</div>
              </div>
            </div>
            <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              教育文档处理平台
            </p>
          </div>

          {/* 主导航 */}
          <nav className="flex-1 px-3 py-2 overflow-y-auto">
            <div className="mb-1 px-3 py-1.5">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                内容管理
              </span>
            </div>
            {SIDE_NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <button
                  key={item.href}
                  onClick={() => navigate(item.href)}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 mb-0.5 rounded-lg text-sm font-medium transition-colors ${
                    active
                      ? 'bg-blue-50 text-blue-700'
                      : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                  }`}
                >
                  <item.icon className={`w-[18px] h-[18px] flex-shrink-0 ${active ? 'text-blue-600' : 'text-slate-400'}`} />
                  <span className="text-xs font-semibold tracking-wide">{item.name}</span>
                </button>
              );
            })}
          </nav>

          {/* 底部导航 */}
          <div className="px-3 py-3 border-t border-slate-100">
            {BOTTOM_NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <button
                  key={item.href}
                  onClick={() => navigate(item.href)}
                  className={`w-full flex items-center gap-3 px-3 py-2 mb-0.5 rounded-lg text-sm transition-colors ${
                    active
                      ? 'bg-blue-50 text-blue-700 font-medium'
                      : 'text-slate-500 hover:bg-slate-50 hover:text-slate-900'
                  }`}
                >
                  <item.icon className={`w-4 h-4 flex-shrink-0 ${active ? 'text-blue-600' : 'text-slate-400'}`} />
                  <span className="text-xs font-semibold">{item.name}</span>
                </button>
              );
            })}
            <div className="mt-2 px-3 text-[10px] text-slate-300">v0.2.0</div>
          </div>
        </aside>

        {/* 页面内容 */}
        <main className="flex-1 overflow-y-auto">
          {children}
          <BatchProcessingController />
        </main>
      </div>
    </div>
  );
}
