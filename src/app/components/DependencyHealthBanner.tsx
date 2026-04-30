import { useEffect, useState } from 'react';
import { AlertTriangle, Activity, XCircle, RefreshCw, Wrench, TerminalSquare, CheckCircle } from 'lucide-react';
import { toast } from 'sonner';

export function DependencyHealthBanner() {
  const [health, setHealth] = useState<any>(null);
  const [supervisorStatus, setSupervisorStatus] = useState<{ok: boolean, command?: string} | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchHealth = async () => {
    setLoading(true);
    try {
      const [resHealth, resSupervisor] = await Promise.all([
        fetch('/__proxy/upload/ops/dependency-health').catch(() => null),
        fetch('/__proxy/upload/ops/dependency-repair/status').catch(() => null)
      ]);
      
      if (resHealth?.ok) setHealth(await resHealth.json());
      
      if (resSupervisor) {
        if (resSupervisor.ok) {
          setSupervisorStatus({ ok: true });
        } else {
          const data = await resSupervisor.json().catch(() => ({}));
          setSupervisorStatus({ 
            ok: false, 
            command: data.command || 'node ops/luceon-dependency-supervisor.mjs' 
          });
        }
      }
    } catch (err) {
      console.warn('Failed to fetch dependency health', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    const timer = setInterval(fetchHealth, 15000);
    return () => clearInterval(timer);
  }, []);

  const handleAction = async (action: string) => {
    setActionLoading(action);
    try {
      const res = await fetch('/__proxy/upload/ops/dependency-repair', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      });
      const data = await res.json();
      if (res.ok) {
        toast.success(`执行成功: ${action}`);
        setTimeout(fetchHealth, 2000);
      } else {
        toast.error(`执行失败: ${data.message || data.error || 'Unknown error'}`);
      }
    } catch (err) {
      toast.error(`请求失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setActionLoading(null);
    }
  };

  if (!health) return null;

  const minioOk = health.dependencies?.minio?.ok;
  const mineruOk = health.dependencies?.mineru?.ok;
  const ollamaOk = health.dependencies?.ollama?.ok || health.dependencies?.ollama?.skipped;

  if (health.ok && minioOk && mineruOk && ollamaOk) {
    return null; // All healthy, don't show banner to save space
  }

  const supervisorActive = supervisorStatus?.ok;

  return (
    <div className={`px-6 py-3 border-b flex flex-col md:flex-row items-start md:items-center justify-between gap-3 ${health.blocking ? 'bg-red-50 border-red-200 text-red-800' : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 font-semibold text-sm">
          {health.blocking ? <XCircle className="w-5 h-5 text-red-600" /> : <AlertTriangle className="w-5 h-5 text-amber-600" />}
          <span>系统诊断: {health.blocking ? '部分核心依赖未启动，任务解析可能受阻' : '部分非核心依赖未就绪'}</span>
        </div>
        
        <div className="flex items-center gap-4 text-xs">
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${minioOk ? 'bg-green-500' : 'bg-red-500'}`} />
            <span>MinIO {minioOk ? '正常' : '异常'}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${mineruOk ? 'bg-green-500' : 'bg-red-500'}`} />
            <span>MinerU {mineruOk ? '正常' : '未启动'}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${health.dependencies?.ollama?.skipped ? 'bg-gray-400' : ollamaOk ? 'bg-green-500' : 'bg-amber-500'}`} />
            <span>Ollama {health.dependencies?.ollama?.skipped ? '未启用' : ollamaOk ? '正常' : 'AI 元数据识别受阻'}</span>
          </div>
        </div>
      </div>
      
      <div className="flex flex-wrap items-center gap-3">
        {!supervisorActive && (
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-gray-500">启动修复代理:</span>
            <div className="text-[11px] font-mono bg-white/60 px-2 py-1 rounded border border-gray-200 text-gray-700 select-all flex items-center gap-1">
              <TerminalSquare className="w-3 h-3" />
              {supervisorStatus?.command || 'node ops/luceon-dependency-supervisor.mjs'}
            </div>
          </div>
        )}

        {supervisorActive && !mineruOk && (
          <button 
            onClick={() => handleAction('start-mineru')} 
            disabled={!!actionLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-100 hover:bg-red-200 text-red-700 rounded text-xs font-medium transition-colors"
          >
            {actionLoading === 'start-mineru' ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wrench className="w-3.5 h-3.5" />}
            一键修复 MinerU
          </button>
        )}

        {supervisorActive && (
          <button 
            onClick={() => handleAction('restart-sidecar')} 
            disabled={!!actionLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white/60 hover:bg-white/80 border border-gray-200 text-gray-700 rounded text-xs font-medium transition-colors"
          >
            {actionLoading === 'restart-sidecar' ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Activity className="w-3.5 h-3.5" />}
            重启日志观测
          </button>
        )}

        {supervisorActive && !ollamaOk && !health.dependencies?.ollama?.skipped && (
          <button 
            onClick={() => handleAction('start-ollama')} 
            disabled={!!actionLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-100 hover:bg-amber-200 text-amber-700 rounded text-xs font-medium transition-colors"
          >
            {actionLoading === 'start-ollama' ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wrench className="w-3.5 h-3.5" />}
            一键启动 Ollama
          </button>
        )}

        {(!ollamaOk && !health.dependencies?.ollama?.skipped && !supervisorActive) && (
          <div className="text-[11px] font-mono bg-white/60 px-2 py-1 rounded border border-amber-100 text-amber-700 select-all">
            ollama serve
          </div>
        )}

        <button 
          onClick={fetchHealth} 
          disabled={loading || !!actionLoading} 
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 rounded text-xs font-medium transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          重新检测
        </button>
      </div>
    </div>
  );
}
