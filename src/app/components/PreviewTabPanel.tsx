import { useMemo, useState } from 'react';
import { Code, Database, FileText } from 'lucide-react';
import type { Material } from '../../store/types';
import { renderMarkdown } from '../utils/markdown';
import { JsonPreviewPanel } from './JsonPreviewPanel';
import { MetadataTab } from './MetadataTab';
 
type TabKey = 'markdown' | 'json' | 'metadata';
 
type MetaForm = {
  language: string;
  grade: string;
  subject: string;
  country: string;
  type: string;
  summary: string;
};
 
export function MarkdownTab({
  content,
  loading,
  error,
}: {
  content: string;
  loading: boolean;
  error: string;
}) {
  const hasContent = content.trim() !== '';
  const html = useMemo(() => renderMarkdown(content), [content]);
 
  if (loading) return <div className="flex items-center justify-center h-full text-gray-400 text-xs">加载中...</div>;
  if (error) return <div className="p-4 text-red-500 text-xs">{error}</div>;
  if (!hasContent) return <div className="flex items-center justify-center h-full text-gray-300 text-xs">暂无 Markdown 内容</div>;
  return <div className="p-4 overflow-auto h-full bg-gray-50" dangerouslySetInnerHTML={{ __html: html }} />;
}
 
export function PreviewTabPanel({
  materialId,
  material,
  markdownContent,
  mdLoading,
  mdError,
  metaForm,
  updateMeta,
  isDirty,
  onSaveMeta,
}: {
  materialId: string | number;
  material?: Material;
  markdownContent: string;
  mdLoading: boolean;
  mdError: string;
  metaForm: MetaForm;
  updateMeta: (key: keyof MetaForm, val: string) => void;
  isDirty: boolean;
  onSaveMeta: () => void;
}) {
  const [activeTab, setActiveTab] = useState<TabKey>('markdown');
 
  const tabs = [
    { key: 'markdown' as const, label: 'Markdown', icon: FileText },
    { key: 'json' as const, label: 'JSON', icon: Code },
    { key: 'metadata' as const, label: '元数据', icon: Database },
  ];
 
  return (
    <div className="bg-white rounded-xl border border-gray-200 flex flex-col h-full overflow-hidden">
      <div className="flex border-b border-gray-200 flex-shrink-0">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-xs font-semibold border-b-2 transition-colors ${
              activeTab === t.key
                ? 'border-blue-600 text-blue-700 bg-blue-50/50'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            }`}
            type="button"
          >
            <t.icon size={13} /> {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'markdown' && (
          <MarkdownTab content={markdownContent} loading={mdLoading} error={mdError} />
        )}
        {activeTab === 'json' && (
          <JsonPreviewPanel materialId={materialId} />
        )}
        {activeTab === 'metadata' && (
          <MetadataTab
            materialId={materialId}
            material={material}
            metaForm={metaForm}
            updateMeta={updateMeta}
            isDirty={isDirty}
            onSaveMeta={onSaveMeta}
          />
        )}
      </div>
    </div>
  );
}
 
