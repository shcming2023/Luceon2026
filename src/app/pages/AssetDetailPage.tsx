import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Tag, FileText, Play, Cpu, CheckCircle, XCircle, Loader, Save, Database, ExternalLink, RefreshCw, ChevronDown, ChevronRight, Pencil, Copy, Download, Folder, FolderOpen } from 'lucide-react';
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

// ─── 文件大小格式化 ────────────────────────────────────────────
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

// ─── 文件名编码修复函数 ────────────────────────────────────────────
/**
 * 修复文件名编码（处理 UTF-8 字节被当作 Latin-1 解析的情况）
 *
 * 问题描述：当文件名包含中文字符时，如果 UTF-8 编码的字节被错误地当作 Latin-1 解析，
 * 会出现类似 "2025_2026å­¦å¹´å¯åè¯¾ç¨IGCSE_English__0500__Extract.pdf" 的情况。
 *
 * 例如："学年" 的 UTF-8 编码是 \xE5\xAD\xA6，被当作 Latin-1 解析后变成 "å­¦"
 *
 * @param filename - 可能存在编码问题的文件名
 * @returns 修复后的文件名
 */
function fixFilenameEncoding(filename: string | undefined): string {
  if (!filename) return '';

  // 检测是否包含典型的编码错误字符（连续的 Latin-1 扩展字符）
  const hasMojiChars = /[\u00C0-\u00FF]{3,}/.test(filename);
  if (!hasMojiChars) return filename;

  try {
    // 将 Latin-1 解析的字符串重新编码为 UTF-8
    const latin1Buffer = new TextEncoder().encode(filename);
    const utf8String = new TextDecoder('latin1').decode(latin1Buffer);

    // 验证修复后的字符串是否包含中文字符（确认修复成功）
    if (/[\u4E00-\u9FFF]/.test(utf8String)) {
      return utf8String;
    }
  } catch (error) {
    console.warn('Failed to fix filename encoding:', error);
  }

  return filename;
}

// ─── MinIO 对象类型 ────────────────────────────────────────────
interface MinioObject {
  objectName: string;
  name: string;
  size: number;
  lastModified: string;
  presignedUrl: string;
}

// ─── 工具：将扁平文件列表按第一层子目录分组 ──────────────────
function groupByDirectory(files: MinioObject[], prefix: string): {
  root: MinioObject[];
  dirs: Map<string, MinioObject[]>;
} {
  const root: MinioObject[] = [];
  const dirs = new Map<string, MinioObject[]>();
  for (const f of files) {
    // 去掉 prefix 前缀，得到相对路径
    const rel = f.name.startsWith(prefix) ? f.name.slice(prefix.length) : f.name;
    const slashIdx = rel.indexOf('/');
    if (slashIdx === -1) {
      root.push(f);
    } else {
      const dirName = rel.slice(0, slashIdx);
      if (!dirs.has(dirName)) dirs.set(dirName, []);
      dirs.get(dirName)!.push(f);
    }
  }
  return { root, dirs };
}

// ─── 文件溯源卡片 ──────────────────────────────────────────────
function FileLineageCard({
  material,
  originalUrl,
  onOriginalUrlReady,
  onRefreshUrl,
  onMdLoaded,
}: {
  material: NonNullable<ReturnType<typeof useAppStore>['state']['materials'][0]>;
  originalUrl: string | null;
  onOriginalUrlReady: (url: string) => void;
  onRefreshUrl: () => Promise<void>;
  onMdLoaded?: (content: string) => void;
}) {
  const objectName = material.metadata?.objectName;
  const originalFileName = material.metadata?.fileName;
  const provider   = material.metadata?.provider;
  const markdownObjectName = material.metadata?.markdownObjectName;
  const parsedFilesCount   = material.metadata?.parsedFilesCount;
  const parsedAt           = material.metadata?.parsedAt;
  const aiConfidence       = material.metadata?.aiConfidence;
  const aiAnalyzedAt       = material.metadata?.aiAnalyzedAt;

  const [refreshing, setRefreshing]     = useState(false);
  const [parsedFiles, setParsedFiles]   = useState<MinioObject[]>([]);
  const [listLoading, setListLoading]   = useState(false);
  const [listExpanded, setListExpanded] = useState(false);
  const [mdPreview, setMdPreview]       = useState<string | null>(null);
  const [mdLoading, setMdLoading]       = useState(false);
  const [showObjectPath, setShowObjectPath] = useState(false);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [zipDownloading, setZipDownloading] = useState(false);
  const hasFetched = useRef(false);

  // 挂载时刷新原始文件预签名 URL（结果提升给父组件）
  useEffect(() => {
    if (!objectName) return;
    fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(objectName)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.url) onOriginalUrlReady(d.url); })
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

  // 手动刷新原始文件 URL（委托父组件处理）
  const handleRefreshOriginal = async () => {
    setRefreshing(true);
    try {
      await onRefreshUrl();
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
      if (r.ok) {
        const text = await r.text();
        setMdPreview(text);
        onMdLoaded?.(text);
      } else toast.error('无法读取 Markdown 内容');
    } catch {
      toast.error('读取失败');
    } finally {
      setMdLoading(false);
    }
  };

  // 下载全部解析产物（ZIP）
  const handleDownloadZip = async () => {
    if (!material.id) return;
    setZipDownloading(true);
    try {
      const r = await fetch('/__proxy/upload/parsed-zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialId: material.id }),
      });
      if (!r.ok) {
        const errData = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
        throw new Error(errData.error || `HTTP ${r.status}`);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `parsed-${material.id}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('解析产物已打包下载');
    } catch (err) {
      toast.error(`下载失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setZipDownloading(false);
    }
  };

  const toggleDir = (dir: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(dir)) next.delete(dir); else next.add(dir);
      return next;
    });
  };

  const hasOriginal = !!(objectName || material.metadata?.fileUrl);
  const hasParsed   = !!(markdownObjectName || (parsedFilesCount && parsedFilesCount !== '0'));
  const hasAi       = material.aiStatus === 'analyzed';

  if (!hasOriginal && !hasParsed && !hasAi) return null;

  // 解析产物目录树分组
  const parsedPrefix = `parsed/${material.id}/`;
  const { root: rootFiles, dirs: subDirs } = groupByDirectory(parsedFiles, parsedPrefix);

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
              {/* 主显示：原始文件名 */}
              {originalFileName ? (
                <p className="flex items-center gap-1.5 text-gray-700 font-medium">
                  <FileText size={12} className="text-blue-400 flex-shrink-0" />
                  <span className="break-all">{originalFileName}</span>
                </p>
              ) : objectName ? (
                <p className="flex items-center gap-1.5 text-gray-700 font-medium">
                  <FileText size={12} className="text-blue-400 flex-shrink-0" />
                  <span className="break-all">{objectName.split('/').pop() ?? objectName}</span>
                </p>
              ) : null}
              {/* 可折叠的 MinIO 路径 */}
              {objectName && (
                <div>
                  <button
                    type="button"
                    onClick={() => setShowObjectPath((v) => !v)}
                    className="text-[10px] text-gray-400 hover:text-gray-600 flex items-center gap-0.5"
                  >
                    {showObjectPath ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                    {showObjectPath ? '收起路径' : '展开存储路径'}
                  </button>
                  {showObjectPath && (
                    <p className="break-all font-mono text-[10px] text-gray-400 mt-1 pl-3">
                      {objectName}
                    </p>
                  )}
                </div>
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
            <div className="flex items-center justify-between gap-2">
              <button
                onClick={handleExpandParsed}
                className="flex-1 text-left flex items-center gap-1.5"
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

            </div>

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
                  <div className="space-y-1 max-h-72 overflow-auto">
                    {/* 根目录文件 */}
                    {rootFiles.map((f) => (
                      <div key={f.objectName} className="flex items-center justify-between text-xs py-1 border-b border-gray-100 last:border-0">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <FileText size={11} className={f.name.endsWith('.md') ? 'text-orange-400' : f.name.endsWith('.json') ? 'text-green-500' : 'text-gray-400'} />
                          <span className="truncate text-gray-700 font-mono max-w-40" title={f.name.split('/').pop()}>{f.name.split('/').pop()}</span>
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
                              <Download size={9} className="inline" /> 下载
                            </a>
                          )}
                        </div>
                      </div>
                    ))}
                    {/* 子目录（文件夹节点） */}
                    {Array.from(subDirs.entries()).map(([dirName, dirFiles]) => {
                      const isOpen = expandedDirs.has(dirName);
                      return (
                        <div key={dirName}>
                          <button
                            type="button"
                            onClick={() => toggleDir(dirName)}
                            className="w-full flex items-center gap-1.5 text-xs py-1 border-b border-gray-100 text-gray-600 hover:text-gray-800"
                          >
                            {isOpen ? <FolderOpen size={12} className="text-yellow-500 flex-shrink-0" /> : <Folder size={12} className="text-yellow-500 flex-shrink-0" />}
                            <span className="font-mono font-medium">{dirName}/</span>
                            <span className="text-gray-400 font-normal">（{dirFiles.length} 个文件）</span>
                            {isOpen ? <ChevronDown size={10} className="text-gray-400 ml-auto" /> : <ChevronRight size={10} className="text-gray-400 ml-auto" />}
                          </button>
                          {isOpen && (
                            <div className="pl-4 space-y-0.5">
                              {dirFiles.map((f) => {
                                const baseName = f.name.split('/').pop() ?? f.name;
                                const isImg = /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(baseName);
                                return (
                                  <div key={f.objectName} className="flex items-center justify-between text-xs py-1 border-b border-gray-50 last:border-0">
                                    <div className="flex items-center gap-1.5 min-w-0">
                                      <FileText size={10} className={isImg ? 'text-purple-400' : 'text-gray-300'} />
                                      <span className="truncate text-gray-600 font-mono max-w-36" title={baseName}>{baseName}</span>
                                      <span className="text-gray-400 flex-shrink-0">{fmtSize(f.size)}</span>
                                    </div>
                                    {f.presignedUrl && (
                                      <a href={f.presignedUrl} target="_blank" rel="noreferrer"
                                        className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 hover:bg-blue-100 ml-2 flex-shrink-0">
                                        <Download size={9} className="inline" /> 下载
                                      </a>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* Markdown 预览区 */}
            {mdPreview !== null && (
              <div className="mt-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[11px] text-gray-500">
                    {mdPreview.length > 2000 ? `预览（前2000字符，共${mdPreview.length}字符）` : '全文预览'}
                  </span>
                  <button
                    onClick={() => {
                      if (mdPreview) {
                        const blob = new Blob([mdPreview], { type: 'text/markdown;charset=utf-8' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `markdown-preview-${Date.now()}.md`;
                        a.click();
                        URL.revokeObjectURL(url);
                        toast.success('Markdown 已下载');
                      }
                    }}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 hover:bg-blue-100"
                  >
                    下载全文
                  </button>
                </div>
                <pre className="bg-white rounded border border-orange-100 p-2 text-[11px] text-gray-700 overflow-auto max-h-64 whitespace-pre-wrap leading-relaxed">
                  {mdPreview.slice(0, 2000)}{mdPreview.length > 2000 ? '\n\n...(内容已截断，点击「下载全文」查看完整内容)' : ''}
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

// ─── Markdown 轻量渲染工具函数 ─────────────────────────────────
function renderMarkdown(md: string): string {
  let html = md
    // 代码块（需在行内代码之前处理）
    .replace(/```[\w]*\n?([\s\S]*?)```/g, (_, code) =>
      `<pre class="bg-gray-100 rounded p-3 text-xs overflow-auto my-2 font-mono whitespace-pre-wrap"><code>${code.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`)
    // 行内代码
    .replace(/`([^`]+)`/g, '<code class="bg-gray-100 rounded px-1 py-0.5 text-xs font-mono text-red-600">$1</code>')
    // ATX 标题
    .replace(/^### (.+)$/gm, '<h3 class="text-sm font-bold text-gray-800 mt-4 mb-1">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-base font-bold text-gray-800 mt-5 mb-2 border-b border-gray-200 pb-1">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold text-gray-900 mt-6 mb-2 border-b-2 border-gray-300 pb-1">$1</h1>')
    // 粗体 / 斜体
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-gray-900">$1</strong>')
    .replace(/\*(.+?)\*/g, '<em class="italic">$1</em>')
    // GFM 表格（简单单行）
    .replace(/^\|(.+)\|$/gm, (line) => {
      if (/^\|[\s\-:|]+\|$/.test(line)) return ''; // 分隔行
      const cells = line.split('|').filter((_, i, a) => i > 0 && i < a.length - 1);
      const tds = cells.map((c) => `<td class="border border-gray-200 px-2 py-1 text-xs">${c.trim()}</td>`).join('');
      return `<tr>${tds}</tr>`;
    })
    // 包裹表格行
    .replace(/((<tr>.*<\/tr>\n?)+)/g, '<table class="border-collapse w-full my-3 text-xs">$1</table>')
    // 无序列表
    .replace(/^[-*+] (.+)$/gm, '<li class="ml-4 list-disc text-xs text-gray-700">$1</li>')
    .replace(/(<li.*<\/li>\n?)+/g, (m) => `<ul class="my-2 space-y-0.5">${m}</ul>`)
    // 有序列表
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-xs text-gray-700">$1</li>')
    // 分隔线
    .replace(/^---+$/gm, '<hr class="my-4 border-gray-200" />')
    // 段落（连续非空行）
    .replace(/\n{2,}/g, '</p><p class="text-xs text-gray-700 leading-relaxed my-1">')
    // 换行
    .replace(/\n/g, '<br />');

  return `<p class="text-xs text-gray-700 leading-relaxed">${html}</p>`;
}

// ─── PDF 内嵌预览面板 ──────────────────────────────────────────
function PDFPreviewPanel({ url }: { url: string }) {
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold text-gray-800 flex items-center gap-2">
          <FileText size={15} className="text-red-500" /> PDF 预览
        </h2>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-blue-600 hover:underline flex items-center gap-1"
        >
          <ExternalLink size={11} /> 新窗口打开
        </a>
      </div>
      <div className="rounded-lg overflow-hidden border border-gray-100 bg-gray-50" style={{ height: 620 }}>
        {loading && !failed && (
          <div className="flex items-center justify-center h-full text-gray-400 text-xs gap-2">
            <Loader size={14} className="animate-spin" /> 加载中...
          </div>
        )}
        {failed ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 text-xs gap-2">
            <XCircle size={32} className="text-red-300" />
            <p>预览加载失败</p>
            <a href={url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
              点击下载查看
            </a>
          </div>
        ) : (
          <iframe
            key={url}
            src={`${url}#toolbar=1&navpanes=0&scrollbar=1`}
            className="w-full h-full"
            style={{ display: loading ? 'none' : 'block' }}
            title="PDF Preview"
            onLoad={() => setLoading(false)}
            onError={() => { setLoading(false); setFailed(true); }}
          />
        )}
      </div>
    </div>
  );
}

// ─── Markdown 渲染预览面板 ──────────────────────────────────────
function MarkdownRenderPanel({ content }: { content: string }) {
  const [collapsed, setCollapsed] = useState(false);
  const html = renderMarkdown(content);

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold text-gray-800 flex items-center gap-2">
          <FileText size={15} className="text-orange-500" /> Markdown 预览
        </h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">{content.length.toLocaleString()} 字符</span>
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-0.5"
          >
            {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            {collapsed ? '展开' : '收起'}
          </button>
        </div>
      </div>
      {!collapsed && (
        <div
          className="overflow-auto rounded-lg border border-gray-100 bg-gray-50 p-4"
          style={{ maxHeight: 620 }}
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
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
  const [editingAiName, setEditingAiName] = useState(false);
  const [aiNameDraft, setAiNameDraft] = useState(detail?.title ?? '');

  // MinerU 解析状态
  const [mineruRunning, setMineruRunning] = useState(false);
  const [mineruProgress, setMineruProgress] = useState(0);
  const [mineruProgressMsg, setMineruProgressMsg] = useState('');
  const [mineruMarkdown, setMineruMarkdown] = useState<string>('');
  const [mineruRetryCount, setMineruRetryCount] = useState(0);

  // AI 分析状态
  const [aiAnalyzing, setAiAnalyzing] = useState(false);

  // 原始文件 presigned URL（从 FileLineageCard 提升）
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  // 从 FileLineageCard 的 md 列表预览提升的 Markdown 内容
  const [lineageMdContent, setLineageMdContent] = useState<string>('');

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

  useEffect(() => {
    setAiNameDraft(detail?.title ?? '');
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

  const handleSaveAiName = () => {
    const nextTitle = aiNameDraft.trim();
    if (!material) return;
    if (!nextTitle) {
      setAiNameDraft(detail?.title ?? '');
      setEditingAiName(false);
      toast.error('识别名称不能为空');
      return;
    }
    if (nextTitle === detail?.title) {
      setEditingAiName(false);
      return;
    }
    dispatch({
      type: 'UPDATE_MATERIAL',
      payload: {
        id: numId,
        updates: { title: nextTitle },
      },
    });
    setEditingAiName(false);
    toast.success('识别名称已更新');
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

    const { apiEndpoint, apiKey, model, providers } = state.aiConfig;

    // 优先使用新的多提供商格式
    const enabledProviders = providers?.filter((p) => p.enabled);
    if ((!enabledProviders || enabledProviders.length === 0) && (!apiEndpoint?.trim() || !model?.trim())) {
      toast.error('请先在「系统设置」中配置 AI 提供商（至少启用一个）');
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
          // 新格式：传递 providers 数组
          ...(enabledProviders && enabledProviders.length > 0
            ? { aiProviders: enabledProviders }
            : {
                // 旧格式兜底
                aiApiEndpoint: apiEndpoint?.replace(/\/$/, ''),
                aiApiKey: apiKey,
                aiModel: model,
              }),
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

  // 刷新原始文件 presigned URL（供 FileLineageCard 回调使用）
  const handleRefreshOriginalUrl = async () => {
    const objectName = material?.metadata?.objectName;
    if (!objectName) return;
    try {
      const r = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(objectName)}`);
      const d = await r.json();
      if (d?.url) { setOriginalUrl(d.url); toast.success('访问链接已刷新'); }
    } catch {
      toast.error('刷新失败，请检查 MinIO 连接');
    }
  };

  // 当前可用的 Markdown 预览内容（优先 MinerU 解析结果，其次溯源面板加载的内容）
  const previewMdContent = mineruMarkdown || lineageMdContent;

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
                  存储路径：{fixFilenameEncoding(material.metadata.objectName)}
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

            {/* B1：原文件名 → 识别名称信息条 */}
            {(material?.metadata?.fileName || material?.title) && (
              <div className="flex items-start gap-2 px-3 py-2 bg-purple-50 rounded-lg border border-purple-100 mb-4 text-xs">
                <div className="flex-1 min-w-0">
                  <span className="text-purple-400">原文件名：</span>
                  <span className="text-gray-700 break-all">{fixFilenameEncoding(material?.metadata?.fileName) || '—'}</span>
                </div>
                {material?.aiStatus === 'analyzed' && material?.title && (
                  <>
                    <span className="text-purple-300 flex-shrink-0 pt-0.5">→</span>
                    <div className="flex-1 min-w-0">
                      <span className="text-purple-400">识别名称：</span>
                      {editingAiName ? (
                        <input
                          value={aiNameDraft}
                          onChange={(e) => setAiNameDraft(e.target.value)}
                          onBlur={handleSaveAiName}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveAiName();
                            if (e.key === 'Escape') {
                              setAiNameDraft(detail.title);
                              setEditingAiName(false);
                            }
                          }}
                          autoFocus
                          className="w-full text-xs font-medium text-gray-800 border border-purple-200 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-purple-300"
                        />
                      ) : (
                        <span className="text-gray-800 font-medium break-all">{material.title}</span>
                      )}
                    </div>
                    <button
                      onClick={() => setEditingAiName(true)}
                      className="flex-shrink-0 p-0.5 text-purple-400 hover:text-purple-600 hover:bg-purple-100 rounded"
                      title="编辑识别名称"
                    >
                      <Pencil size={11} />
                    </button>
                  </>
                )}
              </div>
            )}

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
            <div className="mt-4 pt-3 border-t border-gray-100 space-y-3">
              {/* 标签区域 — 与分类并列，均为 AI 识别输出 */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs text-gray-400 flex items-center gap-1.5">
                    <Tag size={11} className="text-green-500" /> 标签
                  </label>
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
                <div className="flex flex-wrap gap-1.5 min-h-6">
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
                  {!editingTags && detail.tags.length === 0 && (
                    <span className="text-xs text-gray-300">暂无标签</span>
                  )}
                </div>
                {editingTags && (
                  <div className="flex gap-2 mt-2">
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

              {/* 保存元数据 */}
              <div className="flex items-center justify-between pt-3 border-t border-gray-100">
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
        </div>

        {/* 右侧列 */}
        <div className="space-y-5">
          {/* 文件溯源卡片 */}
          {material && (
            <FileLineageCard
              material={material}
              originalUrl={originalUrl}
              onOriginalUrlReady={setOriginalUrl}
              onRefreshUrl={handleRefreshOriginalUrl}
              onMdLoaded={(content) => setLineageMdContent(content)}
            />
          )}

          {/* PDF 内嵌预览 — 仅当原始文件为 PDF 且已获取到 presigned URL 时显示 */}
          {originalUrl && material?.type?.toUpperCase() === 'PDF' && (
            <PDFPreviewPanel url={originalUrl} />
          )}

          {/* Markdown 渲染预览 — 有解析内容时显示 */}
          {previewMdContent && (
            <MarkdownRenderPanel content={previewMdContent} />
          )}

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
