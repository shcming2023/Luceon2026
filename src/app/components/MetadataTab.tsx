import { useEffect, useMemo, useState } from 'react';
import { Save, Tag, ShieldAlert, CheckCircle2, AlertTriangle, Search, Info, Database } from 'lucide-react';
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
  materialId: string | number;
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
      pages: String(material?.metadata?.pages ?? '—'),
      provider: material?.metadata?.provider === 'minio' ? 'MinIO' : material?.metadata?.provider || '—',
    };
  }, [material]);
 
  const aiModel = useMemo(() => {
    const p = state.aiConfig?.providers?.find((x) => x.enabled);
    return p?.model || p?.id || '—';
  }, [state.aiConfig?.providers]);
 
  const handleSaveTags = () => {
    dispatch({ type: 'UPDATE_MATERIAL_TAGS', payload: { id: materialId as any, tags: localTags } });
    setEditingTags(false);
    toast.success('标签已保存');
  };
 
  const addTag = () => {
    const t = tagInput.trim();
    if (t && !localTags.includes(t)) setLocalTags((prev) => [...prev, t]);
    setTagInput('');
  };
 
  const removeTag = (tag: string) => setLocalTags((prev) => prev.filter((t) => t !== tag));
  
  const isAiSkeletonFallback = 
    material?.metadata?.aiClassificationDegraded === true ||
    material?.metadata?.aiClassificationProvider === 'skeleton' ||
    material?.metadata?.aiClassificationModel === 'skeleton' ||
    material?.metadata?.aiClassificationV02?.governance?.risk_flags?.includes('skeleton_fallback') ||
    ['fields_missing', 'schema_invalid', 'ai-metadata-schema-invalid'].includes(material?.metadata?.aiClassificationV02?.governance?.human_review_reason);

  const fallbackReason = 
    material?.metadata?.aiClassificationDegradedReason ||
    material?.metadata?.aiClassificationV02?.governance?.human_review_reason ||
    'AI Provider 返回异常，系统已降级为 skeleton 占位结果';

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
          <dd className="text-gray-700">{fileInfo.pages}</dd>
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

      <section className="space-y-3 pb-4 border-b border-gray-100">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1">
          <Search size={12} className="text-blue-500" /> AI 元数据识别 v0.2 (只读)
        </h3>
        
        {!material?.metadata?.aiClassificationV02 ? (
          <div className="text-xs text-gray-400 bg-gray-50 p-3 rounded-lg border border-gray-100 text-center">
            尚未执行 AI Metadata v0.2 识别
          </div>
        ) : (
          <div className={`space-y-3 text-xs p-3 rounded-lg border ${
            isAiSkeletonFallback ? 'bg-red-50/50 border-red-200' : 'bg-slate-50 border-slate-200'
          }`}>
            {isAiSkeletonFallback && (
              <div className="bg-red-100 text-red-700 p-2 rounded border border-red-200 flex items-start gap-1.5 mb-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <div className="flex-1">
                  <div className="font-bold">AI 元数据识别未产出合规 v0.2 结构，当前为 skeleton 降级结果</div>
                  <div className="text-[10px] mt-0.5">原因: {fallbackReason}</div>
                  {material?.metadata?.aiClassificationErrorSource && (
                    <div className="text-[10px] font-mono mt-0.5 text-red-600">
                      来源: {material.metadata.aiClassificationErrorSource}
                    </div>
                  )}
                  {material.metadata.aiClassificationRepairProviderDetails?.timeoutKind === 'headers-timeout' && (
                    <div className="text-[10px] mt-0.5">附加信息: Ollama repair 请求超时，未取得模型输出</div>
                  )}
                  <div className="text-[10px] mt-0.5 font-semibold">（由于 AI 提供商输出异常，系统已自动拦截并生成骨架占位，需人工复核）</div>
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <div className="text-slate-500">标准版本</div>
              <div className="text-slate-800 font-mono">{material.metadata.aiClassificationStandardVersion || 'v0.2'}</div>
              
              <div className="text-slate-500">Provider / Model</div>
              <div className="text-slate-800">{material.metadata.aiClassificationProvider || '—'} / {material.metadata.aiClassificationModel || '—'}</div>
              
              <div className="text-slate-500">分析时间</div>
              <div className="text-slate-800">{material.metadata.aiClassificationAnalyzedAt ? new Date(material.metadata.aiClassificationAnalyzedAt).toLocaleString('zh-CN') : '—'}</div>
              
              <div className="text-slate-500">输入 Hash</div>
              <div className="text-slate-800 font-mono truncate" title={material.metadata.aiClassificationInputHash}>{material.metadata.aiClassificationInputHash ? material.metadata.aiClassificationInputHash.slice(0, 16) + '...' : '—'}</div>
            </div>

            <div className="pt-2 border-t border-slate-200">
              <h4 className="font-semibold text-slate-600 mb-1.5">Source</h4>
              <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                <dt className="text-slate-400">File Name</dt><dd className="text-slate-700 truncate" title={material.metadata.aiClassificationV02.source?.file_name}>{material.metadata.aiClassificationV02.source?.file_name || '—'}</dd>
                <dt className="text-slate-400">Raw Object</dt><dd className="text-slate-700 truncate font-mono" title={material.metadata.aiClassificationV02.source?.raw_object_name}>{material.metadata.aiClassificationV02.source?.raw_object_name || '—'}</dd>
                <dt className="text-slate-400">Parsed Prefix</dt><dd className="text-slate-700 truncate font-mono" title={material.metadata.aiClassificationV02.source?.parsed_prefix}>{material.metadata.aiClassificationV02.source?.parsed_prefix || '—'}</dd>
                <dt className="text-slate-400">MD Object</dt><dd className="text-slate-700 truncate font-mono" title={material.metadata.aiClassificationV02.source?.markdown_object_name}>{material.metadata.aiClassificationV02.source?.markdown_object_name || '—'}</dd>
              </dl>
            </div>

            <div className="pt-2 border-t border-slate-200">
              <h4 className="font-semibold text-slate-600 mb-1.5 flex items-center gap-1"><Tag size={12} className="text-blue-500" /> 受控分类 (Controlled Classification)</h4>
              <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                {['domain', 'collection', 'curriculum', 'stage', 'level', 'subject', 'resource_type', 'component_role'].map(facet => {
                  const controlled = material.metadata.aiClassificationV02.controlled_classification?.[facet];
                  const raw = material.metadata.aiClassificationV02.primary_facets?.[facet];
                  const rawLabel = raw?.zh || raw?.en || raw;
                  const displayValue = controlled ? controlled.zh : (rawLabel ? <span className="text-amber-600 italic" title={String(rawLabel)}>未命中标准 / 待复核</span> : '—');
                  const labelMap: Record<string, string> = { domain: 'Domain', collection: 'Collection', curriculum: 'Curriculum', stage: 'Stage', level: 'Level', subject: 'Subject', resource_type: 'Resource Type', component_role: 'Role' };
                  return (
                    <div key={facet} className="contents">
                      <dt className="text-slate-400 capitalize">{labelMap[facet]}</dt>
                      <dd className="text-slate-700">{displayValue}</dd>
                    </div>
                  );
                })}
              </dl>
            </div>

            <div className="pt-2 border-t border-slate-200">
              <h4 className="font-semibold text-slate-600 mb-1.5">描述性元数据 (Descriptive Metadata)</h4>
              <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                <dt className="text-slate-400">Series</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.series_name || '—'}</dd>
                <dt className="text-slate-400">Edition</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.edition || '—'}</dd>
                <dt className="text-slate-400">Year</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.year || '—'}</dd>
                <dt className="text-slate-400">Publisher</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.publisher || '—'}</dd>
                <dt className="text-slate-400">Language</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.language || '—'}</dd>
                <dt className="text-slate-400">Exam Board</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.exam_board || '—'}</dd>
                <dt className="text-slate-400">Paper Code</dt><dd className="text-slate-700">{material.metadata.aiClassificationV02.descriptive_metadata?.paper_code || '—'}</dd>
              </dl>
            </div>

            {(material.metadata.aiClassificationV02.normalized_tags?.topic_tags?.length > 0 || material.metadata.aiClassificationV02.normalized_tags?.skill_tags?.length > 0) && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1.5 flex items-center gap-1"><CheckCircle2 size={12} className="text-green-500" /> 规范标签 (Normalized Tags)</h4>
                <div className="flex flex-col gap-1.5">
                  {material.metadata.aiClassificationV02.normalized_tags.topic_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      <span className="text-[10px] text-slate-500 mr-1">Topic:</span>
                      {material.metadata.aiClassificationV02.normalized_tags.topic_tags.map((t: any) => (
                        <span key={`topic-${t.id}`} className="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded text-[10px]">{t.zh || t.en}</span>
                      ))}
                    </div>
                  )}
                  {material.metadata.aiClassificationV02.normalized_tags.skill_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      <span className="text-[10px] text-slate-500 mr-1">Skill:</span>
                      {material.metadata.aiClassificationV02.normalized_tags.skill_tags.map((t: any) => (
                        <span key={`skill-${t.id}`} className="px-1.5 py-0.5 bg-purple-50 text-purple-700 rounded text-[10px]">{t.zh || t.en}</span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {material.metadata.aiClassificationV02.system_tags && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1.5">系统标签 (System Tags)</h4>
                <div className="flex flex-col gap-1.5">
                  {material.metadata.aiClassificationV02.system_tags.format_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      <span className="text-[10px] text-slate-500 mr-1">Format:</span>
                      {material.metadata.aiClassificationV02.system_tags.format_tags.map((t: any, idx: number) => (
                        <span key={`fmt-${idx}`} className="px-1.5 py-0.5 bg-slate-100 text-slate-700 rounded text-[10px]">{t.en}</span>
                      ))}
                    </div>
                  )}
                  {material.metadata.aiClassificationV02.system_tags.artifact_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      <span className="text-[10px] text-slate-500 mr-1">Artifact:</span>
                      {material.metadata.aiClassificationV02.system_tags.artifact_tags.map((t: any, idx: number) => (
                        <span key={`art-${idx}`} className="px-1.5 py-0.5 bg-slate-100 text-slate-700 rounded text-[10px]">{t.en}</span>
                      ))}
                    </div>
                  )}
                  {material.metadata.aiClassificationV02.system_tags.engine_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      <span className="text-[10px] text-slate-500 mr-1">Engine:</span>
                      {material.metadata.aiClassificationV02.system_tags.engine_tags.map((t: any, idx: number) => (
                        <span key={`eng-${idx}`} className="px-1.5 py-0.5 bg-slate-100 text-slate-700 rounded text-[10px]">{t.en}</span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {material.metadata.aiClassificationV02.proposed_new_tags?.length > 0 && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1.5 flex items-center gap-1"><AlertTriangle size={12} className="text-amber-500" /> 候选新标签 (Proposed New Tags)</h4>
                <div className="text-[10px] text-amber-600 mb-1">以下标签未进入正式标签，等待人工审核确认：</div>
                <div className="flex flex-wrap gap-1">
                  {material.metadata.aiClassificationV02.proposed_new_tags.map((t: any, idx: number) => (
                    <span key={idx} className="px-1.5 py-0.5 bg-amber-50 text-amber-700 border border-amber-200 rounded text-[10px]">{t.value} ({t.group})</span>
                  ))}
                </div>
              </div>
            )}

            {material.metadata.aiClassificationV02.classification_review?.required && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1.5 flex items-center gap-1"><ShieldAlert size={12} className="text-red-500" /> 分类复核 (Classification Review)</h4>
                <div className="bg-red-50 p-2 rounded border border-red-100 text-[10px] space-y-1">
                  <div className="flex items-start gap-1">
                    <span className="font-semibold text-red-700 shrink-0">触发原因:</span>
                    <span className="text-red-600 break-words">{material.metadata.aiClassificationV02.classification_review.reasons?.join(', ') || '—'}</span>
                  </div>
                  {Object.keys(material.metadata.aiClassificationV02.classification_review.unmatched_facets || {}).length > 0 && (
                    <div className="flex flex-col gap-0.5 pt-1 mt-1 border-t border-red-100/50">
                      <span className="font-semibold text-red-700">未归一原始值:</span>
                      <ul className="list-disc list-inside text-red-600 ml-1">
                        {Object.entries(material.metadata.aiClassificationV02.classification_review.unmatched_facets).map(([k, v]) => (
                          <li key={k} className="break-all">{k}: {String(v)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="pt-2 border-t border-slate-200">
              <h4 className="font-semibold text-slate-600 mb-1.5">Governance Signals & Status</h4>
              <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                <dt className="text-slate-400">Confidence</dt>
                <dd className="font-semibold">
                  <span className={material.metadata.aiClassificationV02.governance?.confidence === 'high' ? 'text-green-600' : material.metadata.aiClassificationV02.governance?.confidence === 'medium' ? 'text-amber-600' : 'text-red-600'}>
                    {material.metadata.aiClassificationV02.governance?.confidence || '—'}
                  </span>
                </dd>
                <dt className="text-slate-400">Human Review</dt>
                <dd>
                  <span className={material.metadata.aiClassificationV02.governance?.human_review_required ? 'text-red-600 font-semibold flex items-center gap-1' : 'text-green-600 flex items-center gap-1'}>
                    {material.metadata.aiClassificationV02.governance?.human_review_required ? <><AlertTriangle size={10} /> Required</> : <><CheckCircle2 size={10} /> Passed</>}
                  </span>
                </dd>
                {material.metadata.aiClassificationV02.governance?.human_review_required && (
                  <>
                    <dt className="text-slate-400">Review Reason</dt>
                    <dd className="text-red-600 truncate" title={material.metadata.aiClassificationV02.governance?.human_review_reason}>{material.metadata.aiClassificationV02.governance?.human_review_reason}</dd>
                  </>
                )}
                
                {material.metadata.aiClassificationV02.governance_signals && (
                  <>
                    <dt className="text-slate-400">Quality</dt>
                    <dd className="text-slate-700">{material.metadata.aiClassificationV02.governance_signals.quality?.join(', ') || '—'}</dd>
                    <dt className="text-slate-400">Relationship</dt>
                    <dd className="text-slate-700">{material.metadata.aiClassificationV02.governance_signals.relationship?.join(', ') || '—'}</dd>
                    <dt className="text-slate-400">Retention</dt>
                    <dd className="text-slate-700">{material.metadata.aiClassificationV02.governance_signals.retention?.join(', ') || '—'}</dd>
                    <dt className="text-slate-400">Risk</dt>
                    <dd className="text-slate-700">{(material.metadata.aiClassificationV02.governance_signals.risk && material.metadata.aiClassificationV02.governance_signals.risk.length > 0) ? material.metadata.aiClassificationV02.governance_signals.risk.join(', ') : '—'}</dd>
                  </>
                )}
                
                {material.metadata.aiClassificationV02.governance?.risk_flags?.length > 0 && (
                  <>
                    <dt className="text-slate-400 flex items-center gap-1"><ShieldAlert size={10} className="text-red-500" /> Risk Flags</dt>
                    <dd className="text-red-600">{material.metadata.aiClassificationV02.governance.risk_flags.join(', ')}</dd>
                  </>
                )}
              </dl>
            </div>

            {material.metadata.aiClassificationV02.recommended_catalog_path && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1">Recommended Path</h4>
                <div className="bg-white p-1.5 rounded border border-slate-200 font-mono text-[10px] text-slate-600 break-all">
                  {material.metadata.aiClassificationV02.recommended_catalog_path}
                </div>
              </div>
            )}

            {material.metadata.aiClassificationV02.evidence && material.metadata.aiClassificationV02.evidence.length > 0 && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1.5 flex items-center gap-1">Evidence <span className="text-[9px] text-slate-400 font-normal">({material.metadata.aiClassificationV02.evidence.length})</span></h4>
                <div className="space-y-1.5">
                  {material.metadata.aiClassificationV02.evidence.slice(0, 8).map((ev: any, idx: number) => (
                    <div key={idx} className="bg-white p-1.5 rounded border border-slate-200 text-[10px]">
                      <div className="flex justify-between items-start mb-0.5">
                        <span className="font-semibold text-slate-600 uppercase tracking-wider text-[9px]">{ev.type}</span>
                        {ev.supports && <span className="text-blue-500 italic text-[9px]">{ev.supports.join(', ')}</span>}
                      </div>
                      <div className="text-slate-600 italic line-clamp-2" title={ev.quote_or_summary}>
                        "{ev.quote_or_summary}"
                      </div>
                    </div>
                  ))}
                  {material.metadata.aiClassificationV02.evidence.length > 8 && (
                    <div className="text-[10px] text-slate-400 text-center">...及其他 {material.metadata.aiClassificationV02.evidence.length - 8} 条证据</div>
                  )}
                </div>
              </div>
            )}

            {(material.metadata.aiClassificationRawTrace || material.metadata.aiClassificationRawObjectName) && (
              <div className="pt-2 border-t border-slate-200">
                <h4 className="font-semibold text-slate-600 mb-1.5 flex items-center gap-1">
                  <Database size={10} className="text-slate-400" /> 原始输出留痕
                </h4>
                <div className="space-y-2">
                  {[
                    { phase: 'First Pass', trace: material.metadata.aiClassificationRawTrace?.firstPass || { objectName: material.metadata.aiClassificationRawObjectName, contentHash: material.metadata.aiClassificationRawContentHash } },
                    { phase: 'Repair Pass', trace: material.metadata.aiClassificationRawTrace?.repairPass || (material.metadata.aiClassificationRepairRawObjectName ? { objectName: material.metadata.aiClassificationRepairRawObjectName } : null) },
                    { phase: 'Repair Retry', trace: material.metadata.aiClassificationRawTrace?.repairRetryPass || (material.metadata.aiClassificationRepairRetryRawObjectName ? { objectName: material.metadata.aiClassificationRepairRetryRawObjectName } : null) }
                  ].filter(p => p.trace && p.trace.objectName).map((p, idx) => (
                    <div key={idx} className="bg-slate-50 p-2 rounded border border-slate-200 text-[10px] space-y-1">
                      <div className="flex justify-between">
                        <span className="font-semibold text-slate-600">{p.phase}</span>
                        <span className="font-mono text-slate-700">{p.trace.objectName.split('/').pop()?.replace('.txt', '')}</span>
                      </div>
                      {p.trace.contentLength && (
                        <div className="flex justify-between">
                          <span className="text-slate-500">长度</span>
                          <span className="text-slate-700">{p.trace.contentLength} 字符</span>
                        </div>
                      )}
                      <div className="flex justify-between">
                        <span className="text-slate-500">Hash (前12位)</span>
                        <span className="font-mono text-slate-700">{p.trace.contentHash?.slice(0, 12) || '—'}</span>
                      </div>
                      {p.trace.containsThinkTag !== undefined && (
                        <div className="flex justify-between">
                          <span className="text-slate-500">含 Think 标签</span>
                          <span className={p.trace.containsThinkTag ? "text-amber-600 font-semibold" : "text-slate-700"}>
                            {p.trace.containsThinkTag ? '是' : '否'}
                          </span>
                        </div>
                      )}
                      {p.trace.looksTruncated !== undefined && (
                        <div className="flex justify-between">
                          <span className="text-slate-500">疑似截断</span>
                          <span className={p.trace.looksTruncated ? "text-red-600 font-semibold" : "text-slate-700"}>
                            {p.trace.looksTruncated ? '是' : '否'}
                          </span>
                        </div>
                      )}
                      <div className="flex flex-col gap-0.5 pt-1 mt-1 border-t border-slate-100">
                        <span className="text-slate-500">存储路径</span>
                        <span className="font-mono text-[9px] text-slate-600 break-all">{p.trace.objectName}</span>
                      </div>
                      {p.trace.parseErrorMessage && (
                        <div className="flex flex-col gap-0.5 pt-1 mt-1 border-t border-slate-100">
                          <span className="text-slate-500">解析异常摘要</span>
                          <span className="font-mono text-[9px] text-red-600">{p.trace.parseErrorMessage}</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-2 p-2 bg-blue-50 rounded border border-blue-100 flex items-start gap-1.5">
              <Info size={12} className="text-blue-500 mt-0.5 shrink-0" />
              <div className="text-[10px] text-blue-700 leading-relaxed">
                <strong>提示：</strong> 推荐目录只是资料管理建议，不代表 MinIO 对象已被移动。低置信度（Low）的识别结果需要人工二次确认。
              </div>
            </div>
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
