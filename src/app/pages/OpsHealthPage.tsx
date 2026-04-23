import React, { useEffect, useState } from 'react';
import { 
  Activity, 
  Server, 
  Database, 
  Cpu, 
  Brain, 
  HardDrive, 
  CheckCircle2, 
  AlertCircle, 
  Loader2, 
  Clock, 
  ShieldCheck,
  RefreshCw,
  Globe
} from 'lucide-react';

interface HealthStatus {
  status: 'ok' | 'error' | 'warning' | 'unknown';
  message?: string;
  details?: any;
  version?: string;
}

interface OpsHealthReport {
  frontend: HealthStatus;
  uploadServer: HealthStatus;
  dbServer: HealthStatus;
  minio: HealthStatus;
  mineru: HealthStatus;
  ollama: HealthStatus;
  timestamp: string;
}

/**
 * OpsHealthPage — 系统健康仪表盘（只读运维视图）
 * 
 * 遵循《阶段四第二批小任务书》：展示系统各组件健康状态，无运维按钮。
 */
export function OpsHealthPage() {
  const [report, setReport] = useState<OpsHealthReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState<Date>(new Date());

  const fetchHealth = async () => {
    setLoading(true);
    try {
      // 聚合健康检查接口（稍后在 upload-server 中实现）
      const res = await fetch('/__proxy/upload/ops/health');
      if (res.ok) {
        const data = await res.json();
        setReport(data);
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (err) {
      console.error('[OpsHealthPage] Failed to fetch health report', err);
      // 降级：如果聚合接口不可用，至少标记前端 OK
      setReport({
        frontend: { status: 'ok' },
        uploadServer: { status: 'error', message: '无法连接到 upload-server' },
        dbServer: { status: 'unknown' },
        minio: { status: 'unknown' },
        mineru: { status: 'unknown' },
        ollama: { status: 'unknown' },
        timestamp: new Date().toISOString()
      });
    } finally {
      setLoading(false);
      setLastRefreshed(new Date());
    }
  };

  useEffect(() => {
    fetchHealth();
    // 每 60 秒自动刷新一次
    const timer = setInterval(fetchHealth, 60000);
    return () => clearInterval(timer);
  }, []);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'ok': return <CheckCircle2 className="w-5 h-5 text-emerald-500" />;
      case 'error': return <AlertCircle className="w-5 h-5 text-red-500" />;
      case 'warning': return <AlertCircle className="w-5 h-5 text-amber-500" />;
      default: return <RefreshCw className="w-5 h-5 text-slate-300 animate-spin-slow" />;
    }
  };

  const getStatusBg = (status: string) => {
    switch (status) {
      case 'ok': return 'bg-emerald-50 border-emerald-100';
      case 'error': return 'bg-red-50 border-red-100';
      case 'warning': return 'bg-amber-50 border-amber-100';
      default: return 'bg-slate-50 border-slate-100';
    }
  };

  const StatusCard = ({ title, status, icon: Icon, subtext }: { title: string, status: HealthStatus, icon: any, subtext?: string }) => (
    <div className={`p-6 rounded-3xl border transition-all duration-300 hover:shadow-lg ${getStatusBg(status.status)}`}>
      <div className="flex items-center justify-between mb-4">
        <div className="p-2.5 bg-white rounded-xl shadow-sm">
          <Icon className="w-5 h-5 text-slate-600" />
        </div>
        {getStatusIcon(status.status)}
      </div>
      <h3 className="text-sm font-bold text-slate-900 mb-1">{title}</h3>
      <p className="text-[10px] font-medium text-slate-500 uppercase tracking-widest">
        {status.status === 'ok' ? (status.version || 'CONNECTED') : (status.message || 'DISCONNECTED')}
      </p>
      {subtext && <p className="mt-3 text-[11px] text-slate-400 leading-relaxed italic">{subtext}</p>}
      {status.details && (
        <div className="mt-4 pt-4 border-t border-slate-100/50">
          <pre className="text-[9px] text-slate-400 font-mono overflow-x-auto max-h-24">
            {JSON.stringify(status.details, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );

  return (
    <div className="p-8 max-w-7xl mx-auto pb-24 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Header */}
      <div className="flex items-center justify-between mb-12">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 bg-slate-900 rounded-2xl flex items-center justify-center shadow-xl shadow-slate-200">
            <Activity className="w-7 h-7 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">系统运维概览</h1>
            <div className="flex items-center gap-2 mt-1.5">
              <span className="text-slate-500 text-sm">全链路服务健康状态监控（只读）</span>
              <span className="text-slate-200 text-xs">|</span>
              <span className="text-[10px] text-slate-400 font-mono uppercase tracking-widest flex items-center gap-1">
                <Clock className="w-3 h-3" />
                LAST CHECKED: {lastRefreshed.toLocaleTimeString()}
              </span>
            </div>
          </div>
        </div>
        <button 
          onClick={fetchHealth}
          disabled={loading}
          className="flex items-center gap-2 px-5 py-2.5 bg-white border border-slate-200 text-slate-700 rounded-xl text-sm font-bold hover:bg-slate-50 hover:border-slate-300 transition-all shadow-sm active:scale-95 disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          立即刷新
        </button>
      </div>

      {!report ? (
        <div className="py-24 text-center">
          <Loader2 className="w-10 h-10 animate-spin text-blue-600 mx-auto mb-4" />
          <p className="text-slate-500 font-medium">正在获取各组件实时状态...</p>
        </div>
      ) : (
        <>
          {/* Core Services */}
          <div className="mb-12">
            <div className="flex items-center gap-2 mb-6">
              <div className="w-1 h-4 bg-blue-500 rounded-full" />
              <h2 className="text-sm font-black text-slate-400 uppercase tracking-widest">核心后端服务</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <StatusCard 
                title="Frontend App" 
                status={report.frontend} 
                icon={Globe} 
                subtext="浏览器访问与 SPA 路由状态"
              />
              <StatusCard 
                title="Upload Server" 
                status={report.uploadServer} 
                icon={Server} 
                subtext="文件上传、解析调度、Worker 状态"
              />
              <StatusCard 
                title="Database Server" 
                status={report.dbServer} 
                icon={Database} 
                subtext="JSON 文件持久化与 REST API 可达性"
              />
            </div>
          </div>

          {/* AI & Infrastructure */}
          <div className="mb-12">
            <div className="flex items-center gap-2 mb-6">
              <div className="w-1 h-4 bg-purple-500 rounded-full" />
              <h2 className="text-sm font-black text-slate-400 uppercase tracking-widest">AI 与基础设施</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <StatusCard 
                title="MinIO Storage" 
                status={report.minio} 
                icon={HardDrive} 
                subtext="对象存储桶联通性与存储权限"
              />
              <StatusCard 
                title="Local MinerU" 
                status={report.mineru} 
                icon={Cpu} 
                subtext="本地 PDF 解析引擎 (FastAPI) 状态"
              />
              <StatusCard 
                title="Ollama (Qwen3.5)" 
                status={report.ollama} 
                icon={Brain} 
                subtext="本地 AI 推理引擎及 qwen3.5:9b 模型就绪度"
              />
            </div>
          </div>

          {/* System Info Footer */}
          <div className="p-8 bg-slate-50 rounded-[2.5rem] border border-slate-100 flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-white rounded-2xl flex items-center justify-center shadow-sm">
                <ShieldCheck className="w-6 h-6 text-blue-500" />
              </div>
              <div>
                <h4 className="text-sm font-bold text-slate-900">运维说明</h4>
                <p className="text-[11px] text-slate-500 max-w-xl mt-1">
                  本仪表盘仅用于实时监控系统组件状态。如发现服务 Error，请检查相关容器日志（如 `docker compose logs`）。
                  系统不提供 UI 端的自动重启功能，以确保生产环境的变更可审计性。
                </p>
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">EduAsset OS Version</div>
              <div className="text-sm font-black text-slate-900">v2026.04.23-UAT</div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
