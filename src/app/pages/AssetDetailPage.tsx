import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Tag, FileText, Play, Cpu, CheckCircle, XCircle, Loader, Save, Database, ExternalLink, RefreshCw, ChevronDown, ChevronRight, Pencil, Copy, Download } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import type { ProcessTask } from '../../store/types';
import { runMinerUPipeline } from '../../utils/mineruApi';

// ─── 枚举选项定义 ──────────────────────────────────────────────

const LANGUAGE_OPTIONS = ['中文', '英文', '双语', '其他'];
const GRADE_OPTIONS = ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', 'G10', 'G11', 'G12', '通用'];
const SUBJECT_OPTIONS = ['语文', '英语', '数学', '物理', '化学', '生物', '历史', '地理', '政治', '科学', '综合', '其他'];
const COUNTRY_OPTIONS = ['中国', '英国', '美国', '新加坡', '澳大利亚', '加拿大', '其他'];
const MATERIAL_TYPE_OPTIONS = ['课本', '讲义', '练习册', '试卷', '答案', '教案', '课件', '大纲', '其他'];

// ─── 元数据字段中文标签 ────────────────────────────────────────
const META_LABELS: Record<string, string> = {
  language:    '语言',
  grade:       '年级',
  subject:     '学科',
  country:     '国家/地区',
  format:      '格式',
  size:        '文件大小',
  pages:       '页数',
  type:        '资料类型',
  summary:     '内容摘要',
  aiConfidence: '识别置信度',
};

// ─── 文件大小格式化 ────────────────────────────────────────────
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

// ─── MinIO 对象类型 ────────────────────────────────────────────
interface MinioObject {
  objectName: string;
  name: string;
  size: number;
  lastModified: string;
  presignedUrl: string;
}

// ─── 文件溯源卡片 ──────────────────────────────────────────────
function FileLineageCard({
  material,
}: {
  material: NonNullable<ReturnType<typeof useAppStore>['state']['materials'][0]>;
}) {
  const objectName = material.metadata?.objectName;
  const provider   = material.metadata?.provider;
  const markdownObjectName = material.metadata?.markdownObjectName;
  const parsedFilesCount   = material.metadata?.parsedFilesCount;
  const parsedAt           = material.metadata?.parsedAt;
  const aiConfidence       = material.metadata?.aiConfidence;
  const aiAnalyzedAt       = material.metadata?.aiAnalyzedAt;

  const [originalUrl, setOriginalUrl]   = useState<string | null>(null);
  const [refreshing, setRefreshing]     = useState(false);
  const [parsedFiles, setParsedFiles]   = useState<MinioObject[]>([]);
  const [listLoading, setListLoading]   = useState(false);
  const [listExpanded, setListExpanded] = useState(false);
  const [mdPreview, setMdPreview]       = useState<string | null>(null);
  const [mdLoading, setMdLoading]       = useState(false);
  const hasFetched = useRef(false);

  // 挂载时刷新原始文件预签名 URL
  useEffect(() => {
    if (!objectName) return;
    fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(objectName)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.url) setOriginalUrl(d.url); })
      .catch(() => {});
  }, [objectName]);

  // 展开解析产物列表时懒加载
  const handleExpandParsed = async () => {
    const next = !listExpanded;
    setListExpanded(next);
    if (!next || hasFetched.current || !material.id) return;
    hasFetched.current = true;
    setListLoading(true);
    try {
      const r = await fetch(`/__proxy/upload/list?prefix=${encodeURIComponent(`parsed/${material.id}`)}`);
      if (r.ok) {
        const d = await r.json();
        setParsedFiles(d.objects ?? []);
      }
    } catch {
      // silent
    } finally {
      setListLoading(false);
    }
  };

  // 手动刷新原始文件 URL
  const handleRefreshOriginal = async () => {
    if (!objectName) return;
    setRefreshing(true);
    try {
      const r = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(objectName)}`);
      const d = await r.json();
      if (d?.url) { setOriginalUrl(d.url); toast.success('访问链接已刷新'); }
    } catch {
      toast.error('刷新失败，请检查 MinIO 连接');
    } finally {
      setRefreshing(false);
    }
  };

  // 预览 full.md
  const handlePreviewMd = async (url: string) => {
    if (mdPreview !== null) { setMdPreview(null); return; }
    setMdLoading(true);
    try {
      const r = await fetch(url);
      if (r.ok) setMdPreview(await r.text());
      else toast.error('无法读取 Markdown 内容');
    } catch {
      toast.error('读取失败');
    } finally {
      setMdLoading(false);
    }
  };

  const hasOriginal = !!(objectName || material.metadata?.fileUrl);
  const hasParsed   = !!(markdownObjectName || (parsedFilesCount && parsedFilesCount !== '0'));
  const hasAi       = material.aiStatus === 'analyzed';

  if (!hasOriginal && !hasParsed && !hasAi) return null;

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <h2 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
        <Database size={15} className="text-blue-500" /> 文件溯源
      </h2>

      <div className="space-y-3">
        {/* ── 层 1：原始文件 ── */}
        {hasOriginal && (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
            <p className="text-xs font-semibold text-gray-600 mb-2 flex items-center gap-1.5">
              <span className="w-4 h-4 rounded-full bg-blue-100 text-blue-600 text-[10px] flex items-center justify-center font-bold">1</span>
              原始文件上传
            </p>
            <div className="space-y-1 text-xs text-gray-500">
              {objectName && (
                <p className="break-all font-mono text-gray-400">
                  <span className="text-gray-600 not-italic">路径：</span>{objectName}
                </p>
              )}
              <div className="flex items-center gap-3 flex-wrap">
                {material.size && (
                  <span>大小：<span className="text-gray-700">{material.size}</span></span>
                )}
                {material.metadata?.format && (
                  <span>格式：<span className="text-gray-700">{material.metadata.format}</span></span>
                )}
                {provider && (
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${provider === 'minio' ? 'bg-blue-50 text-blue-600' : 'bg-gray-200 text-gray-500'}`}>
                    {provider === 'minio' ? 'MinIO' : 'tmpfiles'}
                  </span>
                )}
              </div>
              {material.uploadedAt && (
                <p>上传时间：<span className="text-gray-700">{new Date(material.uploadedAt).toLocaleString('zh-CN')}</span></p>
              )}
            </div>
            {objectName && (
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={handleRefreshOriginal}
                  disabled={refreshing}
                  className="flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                >
                  <RefreshCw size={10} className={refreshing ? 'animate-spin' : ''} /> 刷新链接
                </button>
                {originalUrl && (
                  <>
                    <a href={originalUrl} target="_blank" rel="noreferrer"
                      className="flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100">
                      <ExternalLink size={10} /> 预览
                    </a>
                    <a href={originalUrl} download
                      className="flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-gray-200 bg-white text-gray-600 hover:bg-gray-50">
                      下载
                    </a>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* 连接线 */}
        {hasOriginal && hasParsed && (
          <div className="flex justify-center">
            <div className="w-px h-4 bg-gray-200" />
          </div>
        )}

        {/* ── 层 2：MinerU 解析产物 ── */}
        {hasParsed && (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
            <button
              onClick={handleExpandParsed}
              className="w-full text-left flex items-center justify-between"
            >
              <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
                <span className="w-4 h-4 rounded-full bg-orange-100 text-orange-600 text-[10px] flex items-center justify-center font-bold">2</span>
                MinerU 解析产物
                {parsedFilesCount && (
                  <span className="ml-1 text-gray-400 font-normal">（{parsedFilesCount} 个文件）</span>
                )}
              </p>
              {listExpanded ? <ChevronDown size={13} className="text-gray-400" /> : <ChevronRight size={13} className="text-gray-400" />}
            </button>

            {parsedAt && (
              <p className="text-xs text-gray-500 mt-1">
                解析时间：<span className="text-gray-700">{new Date(parsedAt).toLocaleString('zh-CN')}</span>
              </p>
            )}

            {markdownObjectName && (
              <p className="text-xs text-gray-400 font-mono mt-1 break-all">
                <span className="text-gray-500 not-italic">路径：</span>{markdownObjectName.replace(/\/full\.md$/, '/')}<span className="text-orange-500">full.md</span>
              </p>
            )}

            {listExpanded && (
              <div className="mt-2">
                {listLoading ? (
                  <div className="flex items-center gap-1.5 text-xs text-gray-400 py-2">
                    <Loader size={12} className="animate-spin" /> 加载文件列表...
                  </div>
                ) : parsedFiles.length === 0 ? (
                  <p className="text-xs text-gray-400 py-1">暂无文件记录（MinIO 中可能尚未存储）</p>
                ) : (
                  <div className="space-y-1 max-h-52 overflow-auto">
                    {parsedFiles.map((f) => (
                      <div key={f.objectName} className="flex items-center justify-between text-xs py-1 border-b border-gray-100 last:border-0">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <FileText size={11} className={f.name.endsWith('.md') ? 'text-orange-400' : f.name.endsWith('.json') ? 'text-green-500' : 'text-gray-400'} />
                          <span className="truncate text-gray-700 font-mono max-w-36" title={f.name}>{f.name}</span>
                          <span className="text-gray-400 flex-shrink-0">{fmtSize(f.size)}</span>
                        </div>
                        <div className="flex items-center gap-1.5 ml-2 flex-shrink-0">
                          {f.name.endsWith('.md') && (
                            <button
                              onClick={() => handlePreviewMd(f.presignedUrl)}
                              className="text-[10px] px-1.5 py-0.5 rounded bg-orange-50 text-orange-600 hover:bg-orange-100"
                            >
                              {mdLoading ? '...' : mdPreview !== null ? '收起' : '预览'}
                            </button>
                          )}
                          {f.presignedUrl && (
                            <a href={f.presignedUrl} target="_blank" rel="noreferrer"
                              className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 hover:bg-blue-100">
                              <ExternalLink size={9} className="inline" /> 下载
                            </a>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Markdown 预览区 */}
            {mdPreview !== null && (
              <div className="mt-2">
                <pre className="bg-white rounded border border-orange-100 p-2 text-[11px] text-gray-700 overflow-auto max-h-48 whitespace-pre-wrap leading-relaxed">
                  {mdPreview.slice(0, 2000)}{mdPreview.length > 2000 ? '\n\n...(内容已截断)' : ''}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* 连接线 */}
        {hasParsed && hasAi && (
          <div className="flex justify-center">
            <div className="w-px h-4 bg-gray-200" />
          </div>
        )}

        {/* ── 层 3：AI 分析结果 ── */}
        {hasAi && (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
            <p className="text-xs font-semibold text-gray-600 mb-2 flex items-center gap-1.5">
              <span className="w-4 h-4 rounded-full bg-purple-100 text-purple-600 text-[10px] flex items-center justify-center font-bold">3</span>
              AI 元数据分析
              {aiConfidence && (
                <span className="ml-1 px-1.5 py-0.5 rounded bg-purple-50 text-purple-600 text-[10px]">置信度 {aiConfidence}%</span>
              )}
            </p>
            <div className="space-y-0.5 text-xs text-gray-500">
              {material.metadata?.subject && (
                <p>学科：<span className="text-gray-700">{material.metadata.subject}</span></p>
              )}
              {material.metadata?.grade && (
                <p>年级：<span className="text-gray-700">{material.metadata.grade}</span></p>
              )}
              {material.metadata?.language && (
                <p>语言：<span className="text-gray-700">{material.metadata.language}</span></p>
              )}
              {aiAnalyzedAt && (
                <p>分析时间：<span className="text-gray-700">{new Date(aiAnalyzedAt).toLocaleString('zh-CN')}</span></p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── 可编辑 Select 组件 ────────────────────────────────────────
function MetaSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-purple-300 bg-white text-gray-700"
      >
        <option value="">— 未识别 —</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </div>
  );
}

export function AssetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const numId = Number(id);
  const { state, dispatch } = useAppStore();
  const navigate = useNavigate();

  const detail = state.assetDetails[numId];
  const material = state.materials.find((m) => m.id === numId);

  const [tagInput, setTagInput] = useState('');
  const [editingTags, setEditingTags] = useState(false);
  const [localTags, setLocalTags] = useState<string[]>(detail?.tags ?? []);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(detail?.title ?? '');

  // MinerU 解析状态
  const [mineruRunning, setMineruRunning] = useState(false);
  const [mineruProgress, setMineruProgress] = useState(0);
  const [mineruProgressMsg, setMineruProgressMsg] = useState('');
  const [mineruMarkdown, setMineruMarkdown] = useState<string>('');
  const [mineruRetryCount, setMineruRetryCount] = useState(0);

  // AI 分析状态
  const [aiAnalyzing, setAiAnalyzing] = useState(false);

  // 元数据可编辑表单（语言/年级/学科/国家/类型 + 摘要）
  const [metaForm, setMetaForm] = useState({
    language:    material?.metadata?.language || '',
    grade:       material?.metadata?.grade || '',
    subject:     material?.metadata?.subject || '',
    country:     material?.metadata?.country || '',
    type:        material?.metadata?.type || '',
    summary:     material?.metadata?.summary || '',
  });

  useEffect(() => {
    if (!material) return;
    setMetaForm({
      language: material.metadata?.language || '',
      grade: material.metadata?.grade || '',
      subject: material.metadata?.subject || '',
      country: material.metadata?.country || '',
      type: material.metadata?.type || '',
      summary: material.metadata?.summary || '',
    });
  }, [
    material?.id,
    material?.metadata?.language,
    material?.metadata?.grade,
    material?.metadata?.subject,
    material?.metadata?.country,
    material?.metadata?.type,
    material?.metadata?.summary,
  ]);

  useEffect(() => {
    setTitleDraft(detail?.title ?? '');
  }, [detail?.title]);

  const updateMeta = (key: keyof typeof metaForm, val: string) =>
    setMetaForm((prev) => ({ ...prev, [key]: val }));

  const isDirty = !!material && (
    metaForm.language !== (material.metadata?.language || '')
    || metaForm.grade !== (material.metadata?.grade || '')
    || metaForm.subject !== (material.metadata?.subject || '')
    || metaForm.country !== (material.metadata?.country || '')
    || metaForm.type !== (material.metadata?.type || '')
    || metaForm.summary !== (material.metadata?.summary || '')
  );

  useEffect(() => {
    if (!isDirty) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty]);

  const handleBackToList = () => {
    if (isDirty && !window.confirm('当前元数据尚未保存，确定离开此页面吗？')) {
      return;
    }
    navigate('/source-materials');
  };

  const handleSaveTitle = () => {
    const nextTitle = titleDraft.trim();
    if (!material) return;
    if (!nextTitle) {
      setTitleDraft(detail?.title ?? '');
      setEditingTitle(false);
      toast.error('标题不能为空');
      return;
    }
    if (nextTitle === detail?.title) {
      setEditingTitle(false);
      return;
    }
    dispatch({
      type: 'UPDATE_MATERIAL',
      payload: {
        id: numId,
        updates: { title: nextTitle },
      },
    });
    setEditingTitle(false);
    toast.success('标题已更新');
  };

  const handleCopyMarkdown = async () => {
    if (!mineruMarkdown) return;
    try {
      await navigator.clipboard.writeText(mineruMarkdown);
      toast.success('Markdown 已复制');
    } catch (error) {
      toast.error(`复制失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const handleDownloadMarkdown = () => {
    if (!mineruMarkdown) return;
    const blob = new Blob([mineruMarkdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(material?.title || 'material').replace(/[\\/:*?"<>|]+/g, '_')}-full.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  if (!detail) {
    return (
      <div className="p-6">
        <button
          onClick={handleBackToList}
          className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 mb-4"
        >
          <ArrowLeft size={16} /> 返回资料库
        </button>
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          资产 #{id} 不存在或已被删除
        </div>
      </div>
    );
  }

  const handleStartProcessing = () => {
    if (!detail) return;
    const newTask: ProcessTask = {
      id: Date.now(),
      name: detail.title,
      type: 'rawcode生成',
      status: 'processing',
      stage: '启动中',
      progress: 0,
      input: detail.assetId,
      output: '-',
      assignee: '系统',
      startTime: new Date().toLocaleString('zh-CN'),
      estimatedTime: '预计 30 分钟',
      logs: [{ time: new Date().toLocaleTimeString('zh-CN'), level: 'info', msg: '任务已创建，开始处理' }],
      materialId: numId,
    };
    dispatch({ type: 'ADD_PROCESS_TASK', payload: newTask });
    dispatch({ type: 'UPDATE_MATERIAL_AI_STATUS', payload: { id: numId, aiStatus: 'analyzing', status: 'processing' } });
    toast.success('处理任务已创建，正在处理中');
  };

  const handleMineruParse = async () => {
    if (!material) { toast.error('找不到资料信息'); return; }
    if (state.mineruConfig.engine === 'cloud' && !state.mineruConfig.apiKey?.trim()) {
      toast.error('请先在「系统设置」中配置 MinerU API Key');
      return;
    }

    const objectName = material.metadata?.objectName;
    const fileUrl = material.metadata?.fileUrl;

    if (!objectName && !fileUrl) {
      toast.error('文件尚未上传或缺少访问地址');
      return;
    }

    setMineruRunning(true);
    setMineruProgress(0);
    setMineruMarkdown('');
    setMineruRetryCount(0);
    dispatch({
      type: 'UPDATE_MATERIAL',
      payload: {
        id: numId,
        updates: {
          mineruZipUrl: undefined,
          metadata: {
            ...material.metadata,
            markdownObjectName: undefined,
            markdownUrl: undefined,
            parsedFilesCount: undefined,
            parsedAt: undefined,
          },
        },
      },
    });
    dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: numId, mineruStatus: 'processing' } });

    try {
      const handleProgress = (pct: number, msg: string) => {
        setMineruProgress(pct);
        setMineruProgressMsg(msg);
        const retryMatch = msg.match(/第\s*(\d+)\s*\/\s*\d+\s*次/);
        if (retryMatch) setMineruRetryCount(Number(retryMatch[1]) - 1);
      };

      let result: Awaited<ReturnType<typeof runMinerUPipeline>>;

      if (objectName) {
        // MinIO 存储：通过后端代理接口下载文件为 Blob，走模式 B（无需公网访问 MinIO）
        setMineruProgressMsg('从存储下载文件...');
        // 使用后端代理接口下载，避免浏览器直接访问 MinIO 内网地址（CORS/网络不通）
        const proxyUrl = `/__proxy/upload/proxy-file?objectName=${encodeURIComponent(objectName)}`;
        const blob = await fetch(proxyUrl).then((r) => {
          if (!r.ok) throw new Error(`下载文件失败: HTTP ${r.status}`);
          return r.blob();
        });
        const fileName = `${material.title}.${material.type.toLowerCase()}`;
        const file = new File([blob], fileName, { type: blob.type || 'application/octet-stream' });

        result = await runMinerUPipeline(file, state.mineruConfig, handleProgress, numId);
      } else {
        // tmpfiles 等公网可访问 URL：走模式 A（URL 直接提交）
        if (!fileUrl) throw new Error('无法获取文件访问地址');
        result = await runMinerUPipeline(fileUrl, `${material.title}.${material.type.toLowerCase()}`, state.mineruConfig, handleProgress, numId);
      }

      if (result.markdown) {
        setMineruMarkdown(result.markdown);
      }

      if (result.markdownObjectName || result.markdownUrl) {
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: numId,
            updates: {
              metadata: {
                ...material.metadata,
                ...(result.markdownObjectName ? { markdownObjectName: result.markdownObjectName } : {}),
                ...(result.markdownUrl ? { markdownUrl: result.markdownUrl } : {}),
                ...(result.parsedFilesCount != null ? { parsedFilesCount: String(result.parsedFilesCount) } : {}),
                parsedAt: new Date().toISOString(),
              },
            },
          },
        });
      }

      if (result.zipUrl) {
        dispatch({ type: 'UPDATE_MATERIAL_MINERU_ZIP_URL', payload: { id: numId, mineruZipUrl: result.zipUrl } });

        setMineruProgressMsg('保存解析结果到文件库...');
        try {
          const downloadRes = await fetch('/__proxy/upload/parse/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ zipUrl: result.zipUrl, materialId: numId }),
          });

          if (downloadRes.ok) {
            const downloadData = await downloadRes.json();
            if (downloadData.markdownContent) setMineruMarkdown(downloadData.markdownContent);
            if (downloadData.markdownObjectName || downloadData.markdownUrl) {
              dispatch({
                type: 'UPDATE_MATERIAL',
                payload: {
                  id: numId,
                  updates: {
                    metadata: {
                      ...material.metadata,
                      ...(downloadData.markdownObjectName ? { markdownObjectName: downloadData.markdownObjectName } : {}),
                      ...(downloadData.markdownUrl ? { markdownUrl: downloadData.markdownUrl } : {}),
                      parsedFilesCount: String(downloadData.totalFiles ?? '?'),
                      parsedAt: new Date().toISOString(),
                    },
                  },
                },
              });
              if (!downloadData.markdownContent && downloadData.markdownUrl) {
                const mdRes = await fetch(downloadData.markdownUrl);
                if (mdRes.ok) setMineruMarkdown(await mdRes.text());
              }
            }
          }
        } catch (downloadErr) {
          console.warn('[MinerU] 解析物回存失败:', downloadErr);
        }
      }

      dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: numId, mineruStatus: 'completed', mineruCompletedAt: Date.now() } });
      toast.success('MinerU 解析完成！');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误';
      dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: numId, mineruStatus: 'failed' } });
      toast.error(`MinerU 解析失败: ${msg}`);
    } finally {
      setMineruRunning(false);
    }
  };

  const handleAiAnalyze = async () => {
    if (!material) { toast.error('找不到资料信息'); return; }

    let markdownObjectName = material.metadata?.markdownObjectName;
    let markdownUrl = material.metadata?.markdownUrl;
    const inlineMarkdownContent = mineruMarkdown || undefined;

    if (!markdownObjectName && !markdownUrl && !inlineMarkdownContent && material.mineruZipUrl) {
      setAiAnalyzing(true);
      try {
        const downloadRes = await fetch('/__proxy/upload/parse/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ zipUrl: material.mineruZipUrl, materialId: numId }),
        });
        if (downloadRes.ok) {
          const downloadData = await downloadRes.json();
          if (downloadData.markdownObjectName) markdownObjectName = downloadData.markdownObjectName;
          if (downloadData.markdownUrl) markdownUrl = downloadData.markdownUrl;
          if (downloadData.markdownContent) setMineruMarkdown(downloadData.markdownContent);
          if (downloadData.markdownObjectName || downloadData.markdownUrl) {
            dispatch({
              type: 'UPDATE_MATERIAL',
              payload: {
                id: numId,
                updates: {
                  metadata: {
                    ...material.metadata,
                    ...(downloadData.markdownObjectName ? { markdownObjectName: downloadData.markdownObjectName } : {}),
                    ...(downloadData.markdownUrl ? { markdownUrl: downloadData.markdownUrl } : {}),
                    parsedFilesCount: String(downloadData.totalFiles ?? '?'),
                  },
                },
              },
            });
          }
        }
      } catch (e) {
        console.warn('[AI] download before analyze failed:', e);
      }
    }

    const finalInlineContent = markdownObjectName || markdownUrl
      ? undefined
      : (inlineMarkdownContent || mineruMarkdown || undefined);

    if (!markdownObjectName && !markdownUrl && !finalInlineContent) {
      toast.error('请先完成 MinerU 解析，生成 full.md 后再运行 AI 分析');
      setAiAnalyzing(false);
      return;
    }

    const { apiEndpoint, apiKey, model } = state.aiConfig;
    if (!apiEndpoint?.trim() || !apiKey?.trim() || !model?.trim()) {
      toast.error('请先在「系统设置」中配置 AI API（接口地址 / Key / 模型名）');
      return;
    }

    if (!aiAnalyzing) setAiAnalyzing(true);
    dispatch({ type: 'UPDATE_MATERIAL_AI_STATUS', payload: { id: numId, aiStatus: 'analyzing' } });

    try {
      const resp = await fetch('/__proxy/upload/parse/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          markdownObjectName,
          markdownUrl,
          ...(finalInlineContent ? { markdownContent: finalInlineContent } : {}),
          materialId: numId,
          aiApiEndpoint: apiEndpoint.replace(/\/$/, ''),
          aiApiKey: apiKey,
          aiModel: model,
          prompts: state.aiConfig.prompts,
        }),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
        throw new Error(errData.error || `HTTP ${resp.status}`);
      }

      const data = await resp.json();

      // AI 识别结果回写到 store 的 metadata（保留 format/pages/fileUrl 等上传字段）
      const newMetadata = {
        subject:      data.subject || '',
        grade:        data.grade || '',
        type:         data.materialType || '',
        language:     data.language || '',
        country:      data.country || '',
        summary:      data.summary || '',
        aiConfidence: String(data.confidence ?? ''),
        aiAnalyzedAt: data.analyzedAt || new Date().toISOString(),
      };

      dispatch({
        type: 'UPDATE_MATERIAL_AI_STATUS',
        payload: {
          id: numId,
          aiStatus: 'analyzed',
          status: 'completed',
          ...(data.title ? { title: data.title } : {}),
          tags: data.tags?.length ? data.tags : material.tags,
          metadata: newMetadata,
        },
      });

      // 同步更新本地表单（AI 结果自动填入）
      setMetaForm({
        language: data.language || '',
        grade:    data.grade || '',
        subject:  data.subject || '',
        country:  data.country || '',
        type:     data.materialType || '',
        summary:  data.summary || '',
      });

      toast.success(
        `AI 分析完成！置信度 ${data.confidence}%` +
        (data.subject ? `，学科：${data.subject}` : '') +
        (data.grade ? `，年级：${data.grade}` : ''),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      dispatch({ type: 'UPDATE_MATERIAL_AI_STATUS', payload: { id: numId, aiStatus: 'failed' } });
      toast.error(`AI 分析失败: ${msg}`);
    } finally {
      setAiAnalyzing(false);
    }
  };

  /** 保存元数据表单到 store（合并到 material.metadata） */
  const handleSaveMeta = () => {
    if (!material) return;
    dispatch({
      type: 'UPDATE_MATERIAL',
      payload: {
        id: numId,
        updates: {
          metadata: {
            ...material.metadata,
            ...metaForm,
          },
        },
      },
    });
    toast.success('元数据已保存');
  };

  const handleSaveTags = () => {
    dispatch({ type: 'UPDATE_ASSET_TAGS', payload: { id: numId, tags: localTags } });
    dispatch({ type: 'UPDATE_MATERIAL_TAGS', payload: { id: numId, tags: localTags } });
    setEditingTags(false);
    toast.success('标签已保存');
  };

  const addTag = () => {
    const t = tagInput.trim();
    if (t && !localTags.includes(t)) setLocalTags((prev) => [...prev, t]);
    setTagInput('');
  };

  const removeTag = (tag: string) => setLocalTags((prev) => prev.filter((t) => t !== tag));

  // 右侧元数据卡片展示字段（上传自动填入 + AI 识别）
  const displayMeta: Record<string, string> = {};
  const META_DISPLAY_ORDER = ['language', 'grade', 'subject', 'country', 'type', 'format', 'size', 'pages', 'aiConfidence'];
  for (const key of META_DISPLAY_ORDER) {
    const val = material?.metadata?.[key];
    if (val != null && val !== '') displayMeta[key] = String(val);
  }

  return (
    <div className="p-6 space-y-5">
      {/* 返回 + 标题 */}
      <div>
        <button
          onClick={handleBackToList}
          className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 mb-3"
        >
          <ArrowLeft size={15} /> 返回资料库
        </button>
        <div className="flex items-start justify-between gap-4">
          <div>
            {editingTitle ? (
              <input
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={handleSaveTitle}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveTitle();
                  if (e.key === 'Escape') {
                    setTitleDraft(detail.title);
                    setEditingTitle(false);
                  }
                }}
                autoFocus
                className="w-full max-w-xl text-xl font-bold text-gray-900 border border-blue-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-300"
              />
            ) : (
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold text-gray-900">{detail.title}</h1>
                <button
                  onClick={() => setEditingTitle(true)}
                  className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg"
                  title="编辑标题"
                >
                  <Pencil size={14} />
                </button>
              </div>
            )}
            <p className="text-xs text-gray-400 mt-1">资产 ID：{detail.assetId}</p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={detail.status} />
            {detail.status === 'pending' && (
              <button
                onClick={handleStartProcessing}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-orange-50 text-orange-700 border border-orange-200 rounded-lg hover:bg-orange-100"
              >
                <Play size={12} /> 开始处理
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* 左主列 */}
        <div className="lg:col-span-2 space-y-5">

          {/* MinerU 解析面板 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-gray-800 flex items-center gap-2">
                <Cpu size={16} className="text-orange-500" /> MinerU 解析
              </h2>
              <div className="flex items-center gap-3">
                <span className={`px-2 py-1 rounded-full text-[11px] font-medium ${state.mineruConfig.engine === 'local' ? 'bg-green-50 text-green-700' : 'bg-blue-50 text-blue-700'}`}>
                  {state.mineruConfig.engine === 'local' ? '本地 Gradio' : '官方 API'}
                </span>
                {material?.mineruStatus === 'completed' && (
                  <span className="flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle size={13} /> 解析完成
                  </span>
                )}
                {material?.mineruStatus === 'failed' && (
                  <span className="flex items-center gap-1 text-xs text-red-500">
                    <XCircle size={13} /> 解析失败
                  </span>
                )}
                {material?.mineruStatus === 'processing' && (
                  <span className="flex items-center gap-1 text-xs text-blue-500">
                    <Loader size={13} className="animate-spin" /> 解析中
                  </span>
                )}
                <button
                  onClick={handleMineruParse}
                  disabled={mineruRunning}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {mineruRunning
                    ? <><Loader size={12} className="animate-spin" /> 解析中...</>
                    : <><Play size={12} /> {material?.mineruStatus === 'completed' ? '重新解析' : '开始解析'}</>
                  }
                </button>
              </div>
            </div>

            {/* 进度条 */}
            {mineruRunning && (
              <div className="mb-4">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span className="flex items-center gap-1.5">
                    {mineruProgressMsg}
                    {mineruRetryCount > 0 && (
                      <span className="px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded text-xs font-medium">
                        重试 {mineruRetryCount}/3
                      </span>
                    )}
                  </span>
                  <span>{mineruProgress}%</span>
                </div>
                <div className="w-full bg-gray-100 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all duration-500 ${mineruRetryCount > 0 ? 'bg-yellow-500' : 'bg-orange-500'}`}
                    style={{ width: `${mineruProgress}%` }}
                  />
                </div>
              </div>
            )}

            {/* 文件信息 */}
            <div className="text-xs text-gray-500 space-y-1">
              {(material?.metadata?.fileUrl || material?.metadata?.objectName) ? (
                <p>文件已上传：<span className="text-gray-700 font-medium">{material.title}.{material.type.toLowerCase()}</span>
                  {material?.metadata?.provider && (
                    <span className="ml-2 px-1.5 py-0.5 bg-gray-100 rounded text-gray-500">
                      {String(material.metadata.provider) === 'minio' ? 'MinIO' : 'tmpfiles'}
                    </span>
                  )}
                  {material?.metadata?.pages && (
                    <span className="ml-2 text-gray-400">{material.metadata.pages} 页</span>
                  )}
                </p>
              ) : (
                <p className="text-yellow-600">⚠ 文件尚未上传，请先在资料库上传文件</p>
              )}
              {material?.metadata?.objectName && (
                <p className="text-gray-400 break-all font-mono">
                  存储路径：{material.metadata.objectName}
                </p>
              )}
              {material?.metadata?.markdownObjectName && (
                <p className="text-green-600">
                  ✓ 解析物已存入 MinIO（{material.metadata.parsedFilesCount || '?'} 个文件）
                </p>
              )}
              {material?.mineruZipUrl && (
                <a
                  href={material.mineruZipUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-blue-600 hover:underline mt-1"
                >
                  <FileText size={12} /> 下载解析结果 ZIP
                </a>
              )}
            </div>

            {/* Markdown 预览 */}
            {mineruMarkdown && (
              <div className="mt-4">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-semibold text-gray-600">解析内容预览（Markdown）</p>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleCopyMarkdown}
                      className="flex items-center gap-1 text-xs px-2.5 py-1.5 border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
                    >
                      <Copy size={12} /> 复制全文
                    </button>
                    <button
                      onClick={handleDownloadMarkdown}
                      className="flex items-center gap-1 text-xs px-2.5 py-1.5 border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
                    >
                      <Download size={12} /> 下载 .md
                    </button>
                  </div>
                </div>
                <pre className="bg-gray-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto max-h-64 whitespace-pre-wrap">
                  {mineruMarkdown.slice(0, 3000)}{mineruMarkdown.length > 3000 ? '\n\n...(内容已截断)' : ''}
                </pre>
              </div>
            )}
          </div>

          {/* AI 元数据分析面板 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-gray-800 flex items-center gap-2">
                <Cpu size={16} className="text-purple-500" /> AI 元数据分析
              </h2>
              <div className="flex items-center gap-3">
                {material?.aiStatus === 'analyzed' && (
                  <span className="flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle size={13} /> 已分析
                    {material.metadata?.aiConfidence && (
                      <span className="text-gray-400 ml-1">({material.metadata.aiConfidence}%)</span>
                    )}
                  </span>
                )}
                {material?.aiStatus === 'failed' && (
                  <span className="flex items-center gap-1 text-xs text-red-500">
                    <XCircle size={13} /> 分析失败
                  </span>
                )}
                {material?.aiStatus === 'analyzing' && (
                  <span className="flex items-center gap-1 text-xs text-purple-500">
                    <Loader size={13} className="animate-spin" /> 分析中
                  </span>
                )}
                <button
                  onClick={handleAiAnalyze}
                  disabled={aiAnalyzing || (!material?.metadata?.markdownObjectName && !material?.metadata?.markdownUrl && !material?.mineruZipUrl && !mineruMarkdown)}
                  title={(!material?.metadata?.markdownObjectName && !material?.metadata?.markdownUrl && !material?.mineruZipUrl && !mineruMarkdown) ? '请先完成 MinerU 解析' : ''}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {aiAnalyzing
                    ? <><Loader size={12} className="animate-spin" /> 分析中...</>
                    : <><Play size={12} /> {material?.aiStatus === 'analyzed' ? '重新分析' : '开始 AI 分析'}</>
                  }
                </button>
              </div>
            </div>

            {/* 元数据可编辑表单（AI 分析完成后自动填充，用户随时可修改） */}
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <MetaSelect
                  label="语言"
                  value={metaForm.language}
                  options={LANGUAGE_OPTIONS}
                  onChange={(v) => updateMeta('language', v)}
                />
                <MetaSelect
                  label="年级"
                  value={metaForm.grade}
                  options={GRADE_OPTIONS}
                  onChange={(v) => updateMeta('grade', v)}
                />
                <MetaSelect
                  label="学科"
                  value={metaForm.subject}
                  options={SUBJECT_OPTIONS}
                  onChange={(v) => updateMeta('subject', v)}
                />
                <MetaSelect
                  label="国家/地区"
                  value={metaForm.country}
                  options={COUNTRY_OPTIONS}
                  onChange={(v) => updateMeta('country', v)}
                />
                <MetaSelect
                  label="资料类型"
                  value={metaForm.type}
                  options={MATERIAL_TYPE_OPTIONS}
                  onChange={(v) => updateMeta('type', v)}
                />
                {/* 只读字段：格式（上传时自动填入） */}
                <div>
                  <label className="block text-xs text-gray-400 mb-1">格式</label>
                  <div className="text-xs text-gray-500 px-2 py-1.5 bg-gray-50 rounded-lg border border-gray-200">
                    {material?.metadata?.format || '—'}
                  </div>
                </div>
              </div>

              {/* 只读字段行：文件大小 + 页数 */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">文件大小</label>
                  <div className="text-xs text-gray-500 px-2 py-1.5 bg-gray-50 rounded-lg border border-gray-200">
                    {material?.size || '—'}
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">页数</label>
                  <div className="text-xs text-gray-500 px-2 py-1.5 bg-gray-50 rounded-lg border border-gray-200">
                    {material?.metadata?.pages || '—'}
                  </div>
                </div>
              </div>

              {/* 摘要（多行文本） */}
              <div>
                <label className="block text-xs text-gray-400 mb-1">内容摘要</label>
                <textarea
                  value={metaForm.summary}
                  onChange={(e) => updateMeta('summary', e.target.value)}
                  rows={3}
                  placeholder="AI 分析后自动填入，或手动输入摘要..."
                  className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-purple-300 resize-none text-gray-700 placeholder:text-gray-300"
                />
              </div>
            </div>

            {/* 保存按钮 */}
            <div className="flex items-center justify-between mt-4 pt-3 border-t border-gray-100">
              {!material?.metadata?.markdownObjectName && !material?.metadata?.markdownUrl && !material?.mineruZipUrl && !mineruMarkdown && (
                <p className="text-xs text-yellow-600">⚠ 请先完成 MinerU 解析，AI 分析将基于解析出的 Markdown 内容</p>
              )}
              <div className="ml-auto">
                <button
                  onClick={handleSaveMeta}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
                >
                  <Save size={12} /> 保存元数据
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* 右侧列 */}
        <div className="space-y-5">
          {/* 文件溯源卡片 */}
          {material && <FileLineageCard material={material} />}

          {/* 元数据概览（上传自动填入 + AI 识别已保存值） */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-4">元数据</h2>
            {Object.keys(displayMeta).length > 0 ? (
              <dl className="space-y-2">
                {Object.entries(displayMeta).map(([k, v]) => (
                  <div key={k} className="flex justify-between text-sm">
                    <dt className="text-gray-500">{META_LABELS[k] ?? k}</dt>
                    <dd className="text-gray-800 font-medium text-right max-w-32 truncate">{v}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <div className="text-center py-2 text-gray-400 text-sm">暂无元数据</div>
            )}
            {material?.metadata?.summary && (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <p className="text-xs text-gray-400 mb-1">摘要</p>
                <p className="text-xs text-gray-600 leading-relaxed">{material.metadata.summary}</p>
              </div>
            )}
          </div>

          {/* 标签 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-gray-800 flex items-center gap-2">
                <Tag size={15} className="text-green-500" /> 标签
              </h2>
              {!editingTags ? (
                <button onClick={() => { setEditingTags(true); setLocalTags(detail.tags); }} className="text-xs text-blue-600">
                  编辑
                </button>
              ) : (
                <div className="flex gap-2">
                  <button onClick={() => setEditingTags(false)} className="text-xs text-gray-400">取消</button>
                  <button onClick={handleSaveTags} className="text-xs text-blue-600 font-medium">保存</button>
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(editingTags ? localTags : detail.tags).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded-full"
                >
                  {tag}
                  {editingTags && (
                    <button onClick={() => removeTag(tag)} className="text-blue-400 hover:text-red-500">×</button>
                  )}
                </span>
              ))}
            </div>
            {editingTags && (
              <div className="flex gap-2 mt-3">
                <input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addTag()}
                  placeholder="输入新标签..."
                  className="flex-1 text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-300"
                />
                <button onClick={addTag} className="text-xs px-2 py-1.5 bg-blue-600 text-white rounded">
                  添加
                </button>
              </div>
            )}
          </div>

          {/* 相关资产 */}
          {detail.relatedAssets.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="font-semibold text-gray-800 mb-3">相关资产</h2>
              <div className="space-y-2">
                {detail.relatedAssets.map((ra) => (
                  <div
                    key={ra.id}
                    onClick={() => navigate(`/asset/${ra.id}`)}
                    className="flex items-center justify-between p-2 rounded-lg hover:bg-gray-50 cursor-pointer"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-700 truncate">{ra.title}</p>
                      <p className="text-xs text-gray-400">{ra.type}</p>
                    </div>
                    <StatusBadge status={ra.status} className="ml-2 flex-shrink-0" />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
