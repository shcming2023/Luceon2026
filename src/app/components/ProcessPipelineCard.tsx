import { CheckCircle, Download, ExternalLink, FileText, Loader, Play, RefreshCw, XCircle, Database } from 'lucide-react';
import type { Material } from '../../store/types';
import { fixFilenameEncoding } from '../utils/filename';
 
export function ProcessPipelineCard({
  material,
  originalUrl,
  onRefreshOriginalUrl,
  mineruEngineLabel,
  mineruRunning,
  mineruProgress,
  mineruProgressMsg,
  mineruRetryCount,
  onMineruParse,
  onDownloadParsedZip,
  aiAnalyzing,
  onAiAnalyze,
  aiDisabledReason,
}: {
  material?: Material;
  originalUrl: string | null;
  onRefreshOriginalUrl: (opts?: { silent?: boolean }) => void;
  mineruEngineLabel: string;
  mineruRunning: boolean;
  mineruProgress: number;
  mineruProgressMsg: string;
  mineruRetryCount: number;
  onMineruParse: () => void;
  onDownloadParsedZip: () => void;
  aiAnalyzing: boolean;
  onAiAnalyze: () => void;
  aiDisabledReason: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <h2 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
        <Database size={15} className="text-blue-500" /> 文件处理流程
      </h2>
 
      <div className="space-y-3">
        {material?.metadata?.objectName || material?.metadata?.fileUrl ? (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
            <p className="text-xs font-semibold text-gray-600 mb-2 flex items-center gap-1.5">
              <span className="w-4 h-4 rounded-full bg-blue-100 text-blue-600 text-[10px] flex items-center justify-center font-bold">1</span>
              原始文件上传
            </p>
            <div className="space-y-1 text-xs text-gray-500">
              {material?.metadata?.fileName ? (
                <p className="flex items-center gap-1.5 text-gray-700 font-medium">
                  <FileText size={12} className="text-blue-400 flex-shrink-0" />
                  <span className="break-all">{fixFilenameEncoding(material.metadata.fileName)}</span>
                </p>
              ) : material?.metadata?.objectName ? (
                <p className="flex items-center gap-1.5 text-gray-700 font-medium">
                  <FileText size={12} className="text-blue-400 flex-shrink-0" />
                  <span className="break-all">{material.metadata.objectName.split('/').pop()}</span>
                </p>
              ) : null}
              <div className="flex items-center gap-3 flex-wrap">
                {material?.size && (
                  <span>大小：<span className="text-gray-700">{material.size}</span></span>
                )}
                {material?.metadata?.format && (
                  <span>格式：<span className="text-gray-700">{material.metadata.format}</span></span>
                )}
                {material?.metadata?.provider && (
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    material.metadata.provider === 'minio' ? 'bg-blue-50 text-blue-600' : 'bg-gray-200 text-gray-500'
                  }`}>
                    {material.metadata.provider === 'minio' ? 'MinIO' : 'tmpfiles'}
                  </span>
                )}
              </div>
              {material?.uploadedAt && (
                <p>上传时间：<span className="text-gray-700">{new Date(material.uploadedAt).toLocaleString('zh-CN')}</span></p>
              )}
            </div>
            {originalUrl && (
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={() => onRefreshOriginalUrl({ silent: false })}
                  className="flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"
                  type="button"
                >
                  <RefreshCw size={10} /> 刷新链接
                </button>
                <a
                  href={originalUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100"
                >
                  <ExternalLink size={10} /> 预览
                </a>
                <a
                  href={originalUrl}
                  download
                  className="flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"
                >
                  下载
                </a>
              </div>
            )}
          </div>
        ) : (
          <div className="rounded-lg border border-yellow-100 bg-yellow-50 p-3">
            <p className="text-xs font-semibold text-yellow-700 mb-1 flex items-center gap-1.5">
              <span className="w-4 h-4 rounded-full bg-yellow-100 text-yellow-600 text-[10px] flex items-center justify-center font-bold">1</span>
              原始文件上传
            </p>
            <p className="text-xs text-yellow-600">⚠ 文件尚未上传，请先在工作台上传文件</p>
          </div>
        )}
 
        <div className="flex justify-center">
          <div className="w-px h-4 bg-gray-200" />
        </div>
 
        <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
              <span className="w-4 h-4 rounded-full bg-orange-100 text-orange-600 text-[10px] flex items-center justify-center font-bold">2</span>
              MinerU 解析产物
            </p>
            <span className={`px-2 py-1 rounded-full text-[10px] font-medium ${
              mineruEngineLabel === '本地 Gradio' ? 'bg-green-50 text-green-700' : 'bg-blue-50 text-blue-700'
            }`}>
              {mineruEngineLabel}
            </span>
          </div>
 
          <div className="space-y-2">
            {material?.mineruStatus === 'completed' && (
              <p className="text-xs text-green-600 flex items-center gap-1">
                <CheckCircle size={12} /> 解析完成
                {material.metadata?.parsedFilesCount && (
                  <span className="text-gray-500">（{material.metadata.parsedFilesCount} 个文件）</span>
                )}
              </p>
            )}
            {material?.mineruStatus === 'failed' && (
              <p className="text-xs text-red-500 flex items-center gap-1">
                <XCircle size={12} /> 解析失败
              </p>
            )}
            {material?.mineruStatus === 'processing' && (
              <p className="text-xs text-blue-500 flex items-center gap-1">
                <Loader size={12} className="animate-spin" /> 解析中
              </p>
            )}
 
            {mineruRunning && (
              <div>
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span className="flex items-center gap-1">
                    {mineruProgressMsg}
                    {mineruRetryCount > 0 && (
                      <span className="px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded text-[10px] font-medium">
                        重试 {mineruRetryCount}/3
                      </span>
                    )}
                  </span>
                  <span>{mineruProgress}%</span>
                </div>
                <div className="w-full bg-gray-100 rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-500 ${mineruRetryCount > 0 ? 'bg-yellow-500' : 'bg-orange-500'}`}
                    style={{ width: `${mineruProgress}%` }}
                  />
                </div>
              </div>
            )}
 
            <button
              onClick={onMineruParse}
              disabled={mineruRunning}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors w-full justify-center"
              type="button"
            >
              {mineruRunning
                ? <><Loader size={12} className="animate-spin" /> 解析中...</>
                : <><Play size={12} /> {material?.mineruStatus === 'completed' ? '重新解析' : '开始解析'}</>
              }
            </button>
 
            {material?.metadata?.markdownObjectName && (
              <button
                onClick={onDownloadParsedZip}
                className="flex items-center justify-center gap-1.5 text-xs px-3 py-1.5 bg-blue-50 text-blue-700 border border-blue-200 rounded-lg hover:bg-blue-100 w-full"
                type="button"
              >
                <Download size={11} /> 下载解析产物 ZIP
              </button>
            )}
 
            {material?.metadata?.parsedAt && (
              <p className="text-xs text-gray-400">
                解析时间：<span className="text-gray-600">{new Date(material.metadata.parsedAt).toLocaleString('zh-CN')}</span>
              </p>
            )}
          </div>
        </div>
 
        <div className="flex justify-center">
          <div className="w-px h-4 bg-gray-200" />
        </div>
 
        <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
              <span className="w-4 h-4 rounded-full bg-purple-100 text-purple-600 text-[10px] flex items-center justify-center font-bold">3</span>
              AI 元数据分析
            </p>
            <div className="flex items-center gap-1">
              {material?.aiStatus === 'analyzed' && (
                <span className="flex items-center gap-0.5 text-xs text-green-600">
                  <CheckCircle size={12} />
                  {material.metadata?.aiConfidence && (
                    <span className="text-gray-500">({material.metadata.aiConfidence}%)</span>
                  )}
                </span>
              )}
              {material?.aiStatus === 'failed' && (
                <span className="flex items-center gap-0.5 text-xs text-red-500">
                  <XCircle size={12} />
                </span>
              )}
              {material?.aiStatus === 'analyzing' && (
                <span className="flex items-center gap-0.5 text-xs text-purple-500">
                  <Loader size={12} className="animate-spin" />
                </span>
              )}
            </div>
          </div>
 
          <button
            onClick={onAiAnalyze}
            disabled={aiAnalyzing || !!aiDisabledReason}
            title={aiDisabledReason}
            className="flex items-center justify-center gap-1.5 text-xs px-3 py-1.5 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors w-full mb-3"
            type="button"
          >
            {aiAnalyzing
              ? <><Loader size={12} className="animate-spin" /> 分析中...</>
              : <><Play size={12} /> {material?.aiStatus === 'analyzed' ? '重新分析' : '开始 AI 分析'}</>
            }
          </button>
 
          <div className="space-y-0.5 text-xs text-gray-500">
            {material?.metadata?.aiAnalyzedAt && (
              <p>分析时间：<span className="text-gray-700">{new Date(material.metadata.aiAnalyzedAt).toLocaleString('zh-CN')}</span></p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

