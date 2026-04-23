import React, { useEffect, useState } from 'react';
import { ShieldCheck, AlertTriangle, Info, AlertOctagon, CheckCircle2, Loader2, Database, Search, RotateCw, Download, FileJson, FileText, Brain } from 'lucide-react';

interface Finding {
  kind: string;
  severity: 'error' | 'warn' | 'info';
  targetType: string;
  targetId: string;
  message: string;
  suggestion: string;
  repair?: any;
}

interface AuditReport {
  ok: boolean;
  findings: Finding[];
  counters: {
    materials: number;
    tasks: number;
    aiJobs: number;
    findings: number;
  };
}

/**
 * AuditPage — 一致性审计（只读运维视图）
 * 
 * 展示系统扫描出的引用不一致、物理文件缺失、过时状态等异常。
 * 遵循《阶段四第一批小任务书》：只读展示，不提供执行入口。
 */
export function AuditPage() {
  const [report, setReport] = useState<AuditReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  const fetchReport = async () => {
    setLoading(true);
    try {
      const res = await fetch('/__proxy/upload/audit/consistency');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setReport(data);
    } catch (err) {
      console.error('[AuditPage] Failed to fetch audit report', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport();
  }, []);

  const exportReport = (format: 'json' | 'md') => {
    if (!report) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `consistency-audit-report-${timestamp}.${format}`;
    
    let content = '';
    if (format === 'json') {
      content = JSON.stringify(report, null, 2);
    } else {
      content = `# EduAsset Consistency Audit Report\n\n`;
      content += `Generated at: ${new Date().toLocaleString()}\n\n`;
      content += `## Summary\n\n`;
      content += `- Materials: ${report.counters.materials}\n`;
      content += `- Tasks: ${report.counters.tasks}\n`;
      content += `- AI Jobs: ${report.counters.aiJobs}\n`;
      content += `- Total Findings: ${report.counters.findings}\n\n`;
      content += `## Findings List\n\n`;
      report.findings.forEach((f, i) => {
        content += `### ${i + 1}. [${f.severity.toUpperCase()}] ${f.kind}\n`;
        content += `- Target: ${f.targetType} (${f.targetId})\n`;
        content += `- Message: ${f.message}\n`;
        content += `- Suggestion: ${f.suggestion}\n\n`;
      });
    }

    const blob = new Blob([content], { type: format === 'json' ? 'application/json' : 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const getSeverityIcon = (severity: string) => {
    switch (severity) {
      case 'error': return <AlertOctagon className="w-3.5 h-3.5 text-red-500" />;
      case 'warn':  return <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />;
      default:      return <Info className="w-3.5 h-3.5 text-blue-500" />;
    }
  };

  const getSeverityClass = (severity: string) => {
    switch (severity) {
      case 'error': return 'bg-red-50 text-red-700 border-red-100';
      case 'warn':  return 'bg-amber-50 text-amber-700 border-amber-100';
      default:      return 'bg-blue-50 text-blue-700 border-blue-100';
    }
  };

  const filteredFindings = report?.findings.filter(f => {
    const matchesFilter = filter === 'all' || f.severity === filter;
    const matchesSearch = !search || 
      f.kind.toLowerCase().includes(search.toLowerCase()) || 
      f.message.toLowerCase().includes(search.toLowerCase()) || 
      f.targetId.toLowerCase().includes(search.toLowerCase());
    return matchesFilter && matchesSearch;
  }) || [];

  if (loading) {
    return (
      <div className="p-12 flex flex-col items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600 mb-4" />
        <p className="text-slate-500 text-sm font-medium">正在启动 Dry-run 一致性扫描...</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="p-12 text-center max-w-md mx-auto">
        <div className="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4">
          <AlertOctagon className="w-8 h-8 text-red-500" />
        </div>
        <h2 className="text-xl font-bold text-slate-900 mb-2">加载报告失败</h2>
        <p className="text-slate-500 text-sm mb-6">无法连接到审计接口。请确保后端服务正常运行且网络畅通。</p>
        <button 
          onClick={fetchReport}
          className="px-6 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-100"
        >
          重试扫描
        </button>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-7xl mx-auto pb-24 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Header */}
      <div className="flex items-center justify-between mb-10">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 bg-blue-600 rounded-2xl flex items-center justify-center shadow-xl shadow-blue-200">
            <ShieldCheck className="w-7 h-7 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">数据一致性审计</h1>
            <div className="flex items-center gap-2 mt-1.5">
              <span className="text-slate-500 text-sm">系统一致性自检报告（只读运维视图）</span>
              <span className="text-slate-200 text-xs">|</span>
              <span className="text-[10px] text-slate-400 font-mono uppercase tracking-widest">DRY-RUN MODE ACTIVE</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button 
            onClick={() => exportReport('json')}
            className="flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 text-slate-700 rounded-xl text-sm font-bold hover:bg-slate-50 hover:border-slate-300 transition-all shadow-sm"
          >
            <FileJson className="w-4 h-4 text-amber-500" /> 导出 JSON
          </button>
          <button 
            onClick={() => exportReport('md')}
            className="flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 text-slate-700 rounded-xl text-sm font-bold hover:bg-slate-50 hover:border-slate-300 transition-all shadow-sm"
          >
            <Download className="w-4 h-4 text-blue-500" /> 导出报告 (MD)
          </button>
          <button 
            onClick={fetchReport}
            className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-100 ml-2"
          >
            <RotateCw className="w-4 h-4" /> 重新扫描
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-sm transition-all hover:shadow-md">
          <div className="flex items-center justify-between mb-4">
            <div className="text-slate-400 text-[10px] font-extrabold uppercase tracking-widest">总素材数</div>
            <div className="p-1.5 bg-slate-50 rounded-lg"><Database className="w-3.5 h-3.5 text-slate-400" /></div>
          </div>
          <div className="text-3xl font-black text-slate-900">{report.counters.materials}</div>
        </div>
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-sm transition-all hover:shadow-md">
          <div className="flex items-center justify-between mb-4">
            <div className="text-slate-400 text-[10px] font-extrabold uppercase tracking-widest">总任务数</div>
            <div className="p-1.5 bg-slate-50 rounded-lg"><FileText className="w-3.5 h-3.5 text-slate-400" /></div>
          </div>
          <div className="text-3xl font-black text-slate-900">{report.counters.tasks}</div>
        </div>
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-sm transition-all hover:shadow-md">
          <div className="flex items-center justify-between mb-4">
            <div className="text-slate-400 text-[10px] font-extrabold uppercase tracking-widest">AI JOB 数</div>
            <div className="p-1.5 bg-slate-50 rounded-lg"><Brain className="w-3.5 h-3.5 text-slate-400" /></div>
          </div>
          <div className="text-3xl font-black text-slate-900">{report.counters.aiJobs}</div>
        </div>
        <div className="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl shadow-slate-200 transition-all hover:scale-[1.02]">
          <div className="flex items-center justify-between mb-4">
            <div className="text-slate-500 text-[10px] font-extrabold uppercase tracking-widest">发现问题项</div>
            <div className="p-1.5 bg-slate-800 rounded-lg"><AlertTriangle className="w-3.5 h-3.5 text-amber-500" /></div>
          </div>
          <div className="text-3xl font-black text-white">{report.counters.findings}</div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden mb-10">
        <div className="p-6 border-b border-slate-100 flex items-center justify-between gap-6 flex-wrap">
          <div className="flex items-center gap-3">
            <h2 className="font-bold text-slate-900">扫描清单 (Findings)</h2>
            <span className="px-2 py-0.5 bg-slate-100 text-slate-500 rounded text-[10px] font-bold">{filteredFindings.length} 项符合</span>
          </div>
          <div className="flex items-center gap-4 flex-1 max-w-2xl">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <input 
                type="text" 
                placeholder="搜索 Kind, ID 或说明..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-slate-50 border-none rounded-xl text-sm focus:ring-2 focus:ring-blue-500 transition-all"
              />
            </div>
            <div className="flex bg-slate-100 p-1 rounded-xl">
              {(['all', 'error', 'warn', 'info'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setFilter(s)}
                  className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${
                    filter === s ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-400 hover:text-slate-600'
                  }`}
                >
                  {s.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        </div>

        {filteredFindings.length === 0 ? (
          <div className="py-20 text-center">
            <div className="w-16 h-16 bg-green-50 rounded-full flex items-center justify-center mx-auto mb-4">
              <CheckCircle2 className="w-8 h-8 text-green-500" />
            </div>
            <h3 className="text-lg font-bold text-slate-900">未发现匹配的异常</h3>
            <p className="text-slate-500 text-sm max-w-xs mx-auto mt-2">系统运行状态良好，或当前过滤器过滤掉了所有发现项。</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50/50 text-[10px] font-bold text-slate-400 uppercase tracking-widest border-b border-slate-100">
                  <th className="px-8 py-5">问题摘要</th>
                  <th className="px-8 py-5">目标资产</th>
                  <th className="px-8 py-5">详细说明</th>
                  <th className="px-8 py-5">恢复建议</th>
                  <th className="px-8 py-5">自动修复</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {filteredFindings.map((f, idx) => (
                  <tr key={idx} className="group hover:bg-slate-50/50 transition-colors">
                    <td className="px-8 py-6">
                      <div className="flex flex-col gap-2">
                        <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-black border uppercase tracking-wider w-fit ${getSeverityClass(f.severity)}`}>
                          {getSeverityIcon(f.severity)}
                          {f.kind}
                        </div>
                        {f.kind === 'orphan-object' && (
                          <div className="inline-flex items-center gap-1.5 text-[10px] font-bold text-red-600 bg-red-50/50 px-2 py-0.5 rounded-md border border-red-100 w-fit">
                            <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
                            涉及物理删除，需人工确认
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      <div className="text-xs font-bold text-slate-900">{f.targetType}</div>
                      <div className="text-[10px] text-slate-400 font-mono mt-1 select-all">{f.targetId}</div>
                    </td>
                    <td className="px-8 py-6">
                      <p className="text-xs text-slate-600 leading-relaxed max-w-sm">{f.message}</p>
                    </td>
                    <td className="px-8 py-6">
                      <div className="text-xs text-blue-600 font-bold leading-relaxed max-w-sm italic flex items-start gap-2">
                        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5 opacity-50" />
                        {f.suggestion}
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      {f.repair ? (
                        <div className="flex items-center gap-2 text-xs text-emerald-600 font-black">
                          <div className="w-5 h-5 bg-emerald-50 rounded flex items-center justify-center">
                            <CheckCircle2 className="w-3 h-3" />
                          </div>
                          READY
                        </div>
                      ) : (
                        <div className="text-[10px] font-bold text-slate-300 uppercase tracking-widest">Manual Only</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Footer Info */}
      <div className="p-8 bg-blue-50/50 rounded-3xl border border-blue-100/50 text-center">
        <h4 className="text-sm font-bold text-blue-900 mb-2 flex items-center justify-center gap-2">
          <ShieldCheck className="w-4 h-4" />
          安全运维声明
        </h4>
        <p className="text-xs text-blue-700/70 max-w-3xl mx-auto leading-relaxed">
          一致性审计页面目前处于 **只读视图** 模式。所有扫描发现项仅供审计参考。
          如需清理或修复数据异常，请按运维手册另行审批执行，严禁在未备份情况下进行生产环境数据干预。
          涉及 `orphan-object` 清理时，必须确认已完成系统全量导出备份。
        </p>
      </div>
    </div>
  );
}
