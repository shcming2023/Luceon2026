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
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState<Date>(new Date());

  const fetchHealth = async () => {
    setLoading(true);
    try {
      // 聚合健康检查接口（稍后在 upload-server 中实现）
      const [res, diagRes] = await Promise.all([
        fetch('/__proxy/upload/ops/health'),
        fetch('/__proxy/upload/ops/mineru/diagnostics')
      ]);
      
      if (res.ok) {
        const data = await res.json();
        setReport(data);
      } else {
        throw new Error(`HTTP ${res.status}`);
      }

      if (diagRes.ok) {
        const diagData = await diagRes.json();
        setDiagnostics(diagData);
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

          {diagnostics && (
            <div className="mb-12">
              <div className="flex items-center gap-2 mb-6">
                <div className="w-1 h-4 bg-orange-500 rounded-full" />
                <h2 className="text-sm font-black text-slate-400 uppercase tracking-widest">MinerU 通畅诊断</h2>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mb-6">
                <div className={`p-6 rounded-3xl border transition-all duration-300 ${['orphan-processing-blocker', 'known-failed-but-mineru-processing'].includes(diagnostics.diagnosis.kind) ? 'bg-orange-50 border-orange-200' : 'bg-slate-50 border-slate-100'}`}>
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
                    <div>
                      <h3 className="text-sm font-bold text-slate-900">MinerU 队列状态: {diagnostics.diagnosis.status}</h3>
                      <p className="text-xs text-slate-500 mt-1">
                        MinerU 内部：处理中 {diagnostics.mineru.processingTasks}，排队中 {diagnostics.mineru.queuedTasks} | 
                        Luceon 追踪：处理中 {diagnostics.luceon.mineruProcessingTasks.length}，排队中 {diagnostics.luceon.mineruQueuedTasks.length}
                      </p>
                    </div>
                    {['orphan-processing-blocker', 'known-failed-but-mineru-processing'].includes(diagnostics.diagnosis.kind) && (
                      <div className="bg-red-100 text-red-700 px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-2">
                        <AlertCircle className="w-4 h-4" /> 发现阻塞风险
                      </div>
                    )}
                  </div>

                  {diagnostics.diagnosis.kind === 'orphan-processing-blocker' && (
                    <div className="bg-white border border-red-200 rounded-xl p-4 shadow-sm">
                      <h4 className="text-sm font-bold text-red-600 flex items-center gap-2">
                        MinerU 当前被未知任务占用，Luceon 队列暂停推进。请先执行人工清障。
                      </h4>
                      <p className="text-xs text-slate-600 mt-2">
                        诊断结果: {diagnostics.diagnosis.message}<br />
                        占用 Task ID: {diagnostics.diagnosis.blockingMineruTaskId}
                      </p>
                      <div className="mt-4 bg-slate-50 p-3 rounded-lg border border-slate-100">
                        <p className="text-xs font-semibold text-slate-700 mb-2">恢复建议（干跑）：</p>
                        <ol className="text-xs text-slate-600 list-decimal pl-4 space-y-1">
                          <li>停止 mineru_api tmux session</li>
                          <li>重新启动 conda mineru-api</li>
                          <li>运行 node server/tests/mineru-deep-check.mjs</li>
                          <li>确认 queued tasks 继续推进</li>
                        </ol>
                      </div>
                    </div>
                  )}

                  {diagnostics.diagnosis.kind === 'known-failed-but-mineru-processing' && (
                    <div className="bg-white border border-red-200 rounded-xl p-4 shadow-sm">
                      <h4 className="text-sm font-bold text-red-600 flex items-center gap-2">
                        已失败任务仍占用 MinerU
                      </h4>
                      <p className="text-xs text-slate-600 mt-2">
                        诊断结果: {diagnostics.diagnosis.message}<br />
                        Luceon Task: {diagnostics.diagnosis.blockingLuceonTaskId}<br />
                        MinerU Task: {diagnostics.diagnosis.blockingMineruTaskId}
                      </p>
                      <div className="mt-4 bg-slate-50 p-3 rounded-lg border border-slate-100">
                        <p className="text-xs font-semibold text-slate-700 mb-2">建议：等待完成或人工清障</p>
                        <p className="text-xs text-slate-600">
                          建议等待 MinerU 当前任务自然结束；若长时间无日志进展，可人工重启 MinerU，并将该 Luceon 任务转入人工审计或手动重试。注意：该任务在 Luceon 侧已 failed。
                        </p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="p-6 rounded-3xl border bg-slate-50 border-slate-100">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                      <Activity className="w-4 h-4" />
                      MinerU 全局日志观测（系统诊断）
                    </h3>
                    <span className="text-[9px] text-slate-400 font-medium">单任务进度请到任务详情页查看</span>
                    {diagnostics.logObservation ? (() => {
                      const level = diagnostics.logObservation.activityLevel || 'no-business-signal';
                      const levelConfig: Record<string, { bg: string; text: string; label: string }> = {
                        'active-progress':     { bg: 'bg-green-100', text: 'text-green-700', label: 'Active · 进度推进中' },
                        'active-stage-change': { bg: 'bg-green-100', text: 'text-green-700', label: 'Active · 阶段切换' },
                        'active-business-log': { bg: 'bg-blue-100',  text: 'text-blue-700',  label: 'Active · 业务日志' },
                        'api-alive-only':      { bg: 'bg-yellow-100',text: 'text-yellow-700',label: 'API 可达 · 无业务信号' },
                        'no-business-signal':  { bg: 'bg-slate-200', text: 'text-slate-600',  label: '无业务信号' },
                        'suspected-stale':     { bg: 'bg-orange-100',text: 'text-orange-700',label: '可能停滞' },
                        'stale-critical':      { bg: 'bg-red-100',   text: 'text-red-700',   label: '严重停滞' },
                        'failed-confirmed':    { bg: 'bg-red-100',   text: 'text-red-700',   label: '检测到错误' },
                        'log-observation-missing': { bg: 'bg-orange-100', text: 'text-orange-700', label: '日志通道 · 文件丢失' },
                        'log-observation-unreadable': { bg: 'bg-orange-100', text: 'text-orange-700', label: '日志通道 · 无法读取' },
                        'log-observation-stale': { bg: 'bg-orange-100', text: 'text-orange-700', label: '日志通道 · 严重滞后' },
                        'log-observation-no-business-signal': { bg: 'bg-orange-100', text: 'text-orange-700', label: '日志通道 · 无有效信号' },
                        'log-observation-unattributed': { bg: 'bg-slate-200', text: 'text-slate-600', label: '日志通道 · 无归因信号' },
                      };
                      const cfg = levelConfig[level] || levelConfig['no-business-signal'];
                      return (
                        <div className={`${cfg.bg} ${cfg.text} px-2 py-0.5 rounded text-[10px] font-bold`}>
                          {cfg.label}
                        </div>
                      );
                    })() : (
                      <div className="bg-slate-200 text-slate-600 px-2 py-0.5 rounded text-[10px] font-bold uppercase">
                        No Data
                      </div>
                    )}
                  </div>
                  {diagnostics.logObservation ? (
                    <div>
                      {diagnostics.diagnosis.kind === 'known-failed-but-mineru-processing' && (
                        <div className="bg-red-50 text-red-600 px-3 py-2 rounded-lg text-xs font-bold mb-3 border border-red-100 flex gap-2 items-start">
                          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                          该进度来自仍在运行的 MinerU 内部任务，但 Luceon 侧任务已 failed。
                        </div>
                      )}
                      {diagnostics.logObservation.phase && (
                        <>
                          <p className="text-xs text-slate-500 mb-2">最近解析进度</p>
                          <div className="bg-white rounded-xl p-3 border border-slate-200">
                            <p className="text-sm font-bold text-slate-800">
                              {diagnostics.logObservation.phase} {diagnostics.logObservation.current}/{diagnostics.logObservation.total}
                            </p>
                            <div className="flex items-center gap-2 mt-2">
                              <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                                <div className="h-full bg-blue-500 rounded-full" style={{ width: `${diagnostics.logObservation.percent}%` }} />
                              </div>
                              <span className="text-xs text-slate-500">{diagnostics.logObservation.percent}%</span>
                            </div>
                          </div>
                        </>
                      )}
                      {diagnostics.logObservation.latestWindow && (
                        <div className="bg-white rounded-xl p-3 border border-slate-200 mt-2">
                          <p className="text-xs text-slate-500 mb-1">Hybrid 窗口</p>
                          <p className="text-sm font-medium text-slate-700">
                            窗口 {diagnostics.logObservation.latestWindow.windowCurrent}/{diagnostics.logObservation.latestWindow.windowTotal} · 页面 {diagnostics.logObservation.latestWindow.pageStart}-{diagnostics.logObservation.latestWindow.pageEnd}/{diagnostics.logObservation.latestWindow.pageTotal}
                          </p>
                        </div>
                      )}
                      {diagnostics.logObservation.signalSummary && (
                        <div className="mt-3 text-[11px] text-slate-500 grid grid-cols-2 gap-x-4 gap-y-1">
                          <span>业务进度信号: <b className="text-slate-700">{diagnostics.logObservation.signalSummary.progressCount}</b></span>
                          <span>业务日志信号: <b className="text-slate-700">{diagnostics.logObservation.signalSummary.businessLogCount}</b></span>
                          <span>API 噪声信号: <b className="text-slate-400">{diagnostics.logObservation.signalSummary.apiNoiseCount}</b></span>
                          <span>错误信号: <b className={diagnostics.logObservation.signalSummary.errorCount > 0 ? 'text-red-600' : 'text-slate-700'}>{diagnostics.logObservation.signalSummary.errorCount}</b></span>
                        </div>
                      )}
                      <p className="text-xs text-slate-500 mt-3">
                        是否可唯一归因：{diagnostics.luceon.mineruProcessingTasks.length === 1 ? '是' : '否 (无法唯一归因)'}
                      </p>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500">
                      未在本机日志文件中观测到最新的阶段性进度输出。
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

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
