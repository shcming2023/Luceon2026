import { useEffect, useState } from 'react';
import { AlertTriangle, Activity, XCircle, RefreshCw } from 'lucide-react';

export function DependencyHealthBanner() {
  const [health, setHealth] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchHealth = async () => {
    setLoading(true);
    try {
      const res = await fetch('/__proxy/upload/ops/dependency-health');
      if (res.ok) {
        const data = await res.json();
        setHealth(data);
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

  if (!health) return null;

  const minioOk = health.dependencies?.minio?.ok;
  const mineruOk = health.dependencies?.mineru?.ok;
  const ollamaOk = health.dependencies?.ollama?.ok || health.dependencies?.ollama?.skipped;

  if (health.ok && minioOk && mineruOk && ollamaOk) {
    return null; // All healthy, don't show banner to save space
  }

  return (
    <div className={`px-6 py-3 border-b flex items-center justify-between ${health.blocking ? 'bg-red-50 border-red-200 text-red-800' : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
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
            <span>Ollama {health.dependencies?.ollama?.skipped ? '未启用' : ollamaOk ? '正常' : '未启动'}</span>
          </div>
        </div>
      </div>
      
      <div className="flex items-center gap-3">
        {!mineruOk && (
          <div className="text-[11px] font-mono bg-white/60 px-2 py-1 rounded border border-red-100 text-red-700 select-all">
            {health.commands?.mineru || 'bash ops/start-mineru-api.sh'}
          </div>
        )}
        {(!ollamaOk && !health.dependencies?.ollama?.skipped) && (
          <div className="text-[11px] font-mono bg-white/60 px-2 py-1 rounded border border-amber-100 text-amber-700 select-all">
            ollama serve
          </div>
        )}
        <button onClick={fetchHealth} disabled={loading} className="p-1 hover:bg-black/5 rounded transition-colors disabled:opacity-50">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
    </div>
  );
}
