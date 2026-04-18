import { useEffect, useMemo, useState } from 'react';
import { Save, Tag } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { Material } from '../../store/types';
 
const LANGUAGE_OPTIONS = ['中文', '英文', '双语', '其他'];
const GRADE_OPTIONS = ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', 'G10', 'G11', 'G12', '通用'];
const SUBJECT_OPTIONS = ['语文', '英语', '数学', '物理', '化学', '生物', '历史', '地理', '政治', '科学', '综合', '其他'];
const COUNTRY_OPTIONS = ['中国', '英国', '美国', '新加坡', '澳大利亚', '加拿大', '其他'];
const MATERIAL_TYPE_OPTIONS = ['课本', '讲义', '练习册', '试卷', '答案', '教案', '课件', '大纲', '其他'];
 
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
        className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-blue-300"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    </div>
  );
}
 
type MetaForm = {
  language: string;
  grade: string;
  subject: string;
  country: string;
  type: string;
  summary: string;
};
 
export function MetadataTab({
  materialId,
  material,
  metaForm,
  updateMeta,
  isDirty,
  onSaveMeta,
}: {
  materialId: number;
  material?: Material;
  metaForm: MetaForm;
  updateMeta: (key: keyof MetaForm, val: string) => void;
  isDirty: boolean;
  onSaveMeta: () => void;
}) {
  const { state, dispatch } = useAppStore();
  const [tagInput, setTagInput] = useState('');
  const [editingTags, setEditingTags] = useState(false);
  const [localTags, setLocalTags] = useState<string[]>(material?.tags ?? []);
 
  useEffect(() => {
    setLocalTags(material?.tags ?? []);
  }, [material?.tags]);
 
  const tags = editingTags ? localTags : (material?.tags ?? []);
 
  const fileInfo = useMemo(() => {
    return {
      fileName: material?.metadata?.fileName || material?.title || '—',
      format: material?.metadata?.format || material?.type || '—',
      size: material?.size || '—',
      pages: material?.metadata?.pages || '—',
      provider: material?.metadata?.provider === 'minio' ? 'MinIO' : material?.metadata?.provider || '—',
    };
  }, [material]);
 
  const aiModel = useMemo(() => {
    const p = state.aiConfig?.providers?.find((x) => x.enabled);
    return p?.model || p?.provider || '—';
  }, [state.aiConfig?.providers]);
 
  const handleSaveTags = () => {
    dispatch({ type: 'UPDATE_ASSET_TAGS', payload: { id: materialId, tags: localTags } });
    dispatch({ type: 'UPDATE_MATERIAL_TAGS', payload: { id: materialId, tags: localTags } });
    setEditingTags(false);
    toast.success('标签已保存');
  };
 
  const addTag = () => {
    const t = tagInput.trim();
    if (t && !localTags.includes(t)) setLocalTags((prev) => [...prev, t]);
    setTagInput('');
  };
 
  const removeTag = (tag: string) => setLocalTags((prev) => prev.filter((t) => t !== tag));
 
  return (
    <div className="space-y-4 p-5 overflow-y-auto h-full">
      <section className="space-y-2 pb-4 border-b border-gray-100">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">文件信息</h3>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          <dt className="text-gray-400">文件名</dt>
          <dd className="text-gray-700 break-all">{fileInfo.fileName}</dd>
          <dt className="text-gray-400">格式</dt>
          <dd className="text-gray-700">{fileInfo.format}</dd>
          <dt className="text-gray-400">大小</dt>
          <dd className="text-gray-700">{fileInfo.size}</dd>
          <dt className="text-gray-400">页数</dt>
          <dd className="text-gray-700">{fileInfo.pages as any}</dd>
          <dt className="text-gray-400">存储后端</dt>
          <dd className="text-gray-700">{fileInfo.provider}</dd>
        </dl>
      </section>
 
      <section className="space-y-3 pb-4 border-b border-gray-100">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">AI 识别结果</h3>
          {material?.metadata?.aiConfidence && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-50 text-purple-600">
              置信度 {material.metadata.aiConfidence}%
            </span>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <MetaSelect label="学科" value={metaForm.subject} options={SUBJECT_OPTIONS} onChange={(v) => updateMeta('subject', v)} />
          <MetaSelect label="年级" value={metaForm.grade} options={GRADE_OPTIONS} onChange={(v) => updateMeta('grade', v)} />
          <MetaSelect label="语言" value={metaForm.language} options={LANGUAGE_OPTIONS} onChange={(v) => updateMeta('language', v)} />
          <MetaSelect label="国家/地区" value={metaForm.country} options={COUNTRY_OPTIONS} onChange={(v) => updateMeta('country', v)} />
          <MetaSelect label="资料类型" value={metaForm.type} options={MATERIAL_TYPE_OPTIONS} onChange={(v) => updateMeta('type', v)} />
          <div>
            <label className="block text-xs text-gray-400 mb-1">分析模型</label>
            <div className="text-xs text-gray-500 px-2 py-1.5 bg-gray-50 rounded-lg border border-gray-200">
              {aiModel}
            </div>
          </div>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">内容摘要</label>
          <textarea
            value={metaForm.summary}
            onChange={(e) => updateMeta('summary', e.target.value)}
            rows={4}
            placeholder="AI 分析后自动填入..."
            className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-purple-300 resize-none"
          />
        </div>
        {isDirty && (
          <button
            onClick={onSaveMeta}
            className="w-full flex items-center justify-center gap-1 text-xs px-3 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700"
            type="button"
          >
            <Save size={12} /> 保存修改
          </button>
        )}
      </section>
 
      <section className="space-y-2 pb-4 border-b border-gray-100">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1">
            <Tag size={12} className="text-green-500" /> 标签
          </h3>
          {!editingTags ? (
            <button
              onClick={() => { setEditingTags(true); setLocalTags(material?.tags ?? []); }}
              className="text-xs text-blue-600"
              type="button"
            >
              编辑
            </button>
          ) : (
            <div className="flex gap-2">
              <button onClick={() => setEditingTags(false)} className="text-xs text-gray-400" type="button">取消</button>
              <button onClick={handleSaveTags} className="text-xs text-blue-600 font-medium" type="button">保存</button>
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-1 min-h-6">
          {tags.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-0.5 text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded-full"
            >
              {tag}
              {editingTags && (
                <button onClick={() => removeTag(tag)} className="text-blue-400 hover:text-red-500 text-[10px]" type="button">×</button>
              )}
            </span>
          ))}
          {!editingTags && (material?.tags?.length ?? 0) === 0 && (
            <span className="text-xs text-gray-300">暂无标签</span>
          )}
        </div>
        {editingTags && (
          <div className="flex gap-2 mt-1.5">
            <input
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addTag()}
              placeholder="输入新标签..."
              className="flex-1 text-xs border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-300"
            />
            <button onClick={addTag} className="text-xs px-2 py-1 bg-blue-600 text-white rounded" type="button">
              添加
            </button>
          </div>
        )}
      </section>
 
      <section className="space-y-2">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">处理时间线</h3>
        <dl className="text-xs space-y-1 text-gray-600">
          {material?.uploadedAt && <div>上传：{new Date(material.uploadedAt).toLocaleString('zh-CN')}</div>}
          {material?.metadata?.parsedAt && <div>MinerU 解析：{new Date(material.metadata.parsedAt).toLocaleString('zh-CN')}</div>}
          {material?.metadata?.aiAnalyzedAt && <div>AI 分析：{new Date(material.metadata.aiAnalyzedAt).toLocaleString('zh-CN')}</div>}
        </dl>
      </section>
    </div>
  );
}

