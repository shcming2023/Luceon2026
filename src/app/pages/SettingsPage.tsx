import { useState, useEffect, useRef } from 'react';
import { Save, Eye, EyeOff, Bot, ScanLine, Database, CheckCircle, XCircle, Loader, Download, Upload, HardDrive, AlertTriangle, Plus, Trash2, ChevronUp, ChevronDown, ToggleLeft, ToggleRight } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { AiConfig, AiProvider, MinerUConfig, MinioConfig } from '../../store/types';
import { checkLocalMinerUHealth } from '../../utils/mineruLocalApi';

type ActiveTab = 'ai' | 'mineru' | 'storage' | 'backup';

// ─── 提示词字段中文标签映射 ──────────────────────────────────
const PROMPT_LABELS: Record<string, string> = {
  title:        '资料名称',
  subject:      '学科识别',
  grade:        '年级识别',
  materialType: '资料类型',
  language:     '语言识别',
  country:      '国家/地区',
  tags:         '标签提取',
  summary:      '内容摘要',
};

const LOCAL_STORAGE_KEYS = [
  'app_ai_config',
  'app_mineru_config',
  'app_minio_config',
  'app_materials',
  'app_process_tasks',
  'app_tasks',
  'app_products',
  'app_batch_processing',
  'app_asset_details',
  'app_flexible_tags',
  'app_ai_rules',
  'app_ai_rule_settings',
];

const DEFAULT_CAPACITY_LIMITS = {
  dbSoftLimitMB: 100,
  storageSoftLimitGB: 20,
};

type CapacityLimits = typeof DEFAULT_CAPACITY_LIMITS;

type BackupConfirmState =
  | { kind: 'json'; fileName: string; data: unknown }
  | { kind: 'full'; fileName: string; file: File; mode: 'replace' | 'merge' };

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4">
      <div className="w-36 flex-shrink-0 mt-2">
        <label className="text-sm text-gray-500">{label}</label>
        {hint && <p className="text-xs text-gray-400 mt-0.5">{hint}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function Input({
  value,
  onChange,
  type = 'text',
  placeholder,
  className = '',
  disabled = false,
}: {
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className={`w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:bg-gray-50 disabled:text-gray-400 ${className}`}
    />
  );
}

function Select({
  value,
  onChange,
  options,
  disabled = false,
}: {
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:bg-gray-50 disabled:text-gray-400"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function Textarea({
  value,
  onChange,
  rows = 3,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
      placeholder={placeholder}
      className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
    />
  );
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function getUsageTone(ratio: number) {
  if (ratio >= 0.9) return 'bg-red-500';
  if (ratio >= 0.7) return 'bg-yellow-500';
  return 'bg-green-500';
}

function getUsageTextTone(ratio: number) {
  if (ratio >= 0.9) return 'text-red-600';
  if (ratio >= 0.7) return 'text-yellow-600';
  return 'text-green-600';
}

export function SettingsPage() {
  const { state, dispatch } = useAppStore();
  const [activeTab, setActiveTab] = useState<ActiveTab>('ai');
  const [showKey, setShowKey] = useState(false);
  const [showMinioKeys, setShowMinioKeys] = useState({ access: false, secret: false });
  const jsonImportInputRef = useRef<HTMLInputElement>(null);
  const fullImportInputRef = useRef<HTMLInputElement>(null);

  // 本地副本（保存前不 dispatch）
  const [aiForm, setAiForm] = useState<AiConfig>({ ...state.aiConfig });
  const [mineruForm, setMineruForm] = useState<MinerUConfig>({ ...state.mineruConfig });
  const [minioForm, setMinioForm] = useState<MinioConfig>({ ...state.minioConfig });

  // 测试连接状态
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [localTesting, setLocalTesting] = useState(false);
  const [localTestResult, setLocalTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  // 保存中状态
  const [savingMinio, setSavingMinio] = useState(false);
  const [backupLoading, setBackupLoading] = useState(false);
  const [dbStats, setDbStats] = useState<{
    fileSize: number;
    counts: Record<string, number>;
    materialsTotalSizeBytes: number;
    materialsByStatus: Record<string, number>;
    materialsBySubject: Record<string, number>;
  } | null>(null);
  const [storageStats, setStorageStats] = useState<{
    backend: string;
    totalObjects: number;
    totalSize: number;
    buckets: { name: string; objectCount: number; totalSize: number }[];
  } | null>(null);
  const [capacityLimits, setCapacityLimits] = useState<CapacityLimits>(DEFAULT_CAPACITY_LIMITS);
  const [savingCapacity, setSavingCapacity] = useState(false);
  const [fullImportMode, setFullImportMode] = useState<'replace' | 'merge'>('replace');
  const [confirmState, setConfirmState] = useState<BackupConfirmState | null>(null);
  const [exportingFull, setExportingFull] = useState(false);

  // 切换到 storage tab 时从 upload-server 读取最新配置
  useEffect(() => {
    if (activeTab !== 'storage') return;
    setTestResult(null);
    fetch('/__proxy/upload/settings/storage')
      .then((r) => r.json())
      .then((data) => {
        setMinioForm((prev) => ({
          ...prev,
          storageBackend:  data.storageBackend  ?? prev.storageBackend,
          endpoint:        data.endpoint        ?? prev.endpoint,
          port:            data.port            ?? prev.port,
          useSSL:          data.useSSL          ?? prev.useSSL,
          // 密钥返回 *** 时保留本地 store 中保存的值（若有）
          accessKey:       data.accessKey === '***' ? (state.minioConfig.accessKey || '') : (data.accessKey || ''),
          secretKey:       data.secretKey === '***' ? (state.minioConfig.secretKey || '') : (data.secretKey || ''),
          bucket:          data.bucket          ?? prev.bucket,
          parsedBucket:    data.parsedBucket    ?? prev.parsedBucket,
          presignedExpiry: data.presignedExpiry  ?? prev.presignedExpiry,
        }));
      })
      .catch(() => {/* upload-server 不可用时静默，使用 store 默认值 */});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  useEffect(() => {
    if (activeTab !== 'backup') return;
    void refreshBackupStats();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const updateAi = (patch: Partial<AiConfig>) => setAiForm((prev) => ({ ...prev, ...patch }));
  const updateAiPrompt = (key: keyof AiConfig['prompts'], val: string) =>
    setAiForm((prev) => ({ ...prev, prompts: { ...prev.prompts, [key]: val } }));
  const updateMineru = (patch: Partial<MinerUConfig>) => setMineruForm((prev) => ({ ...prev, ...patch }));
  const updateMinio = (patch: Partial<MinioConfig>) => {
    setMinioForm((prev) => ({ ...prev, ...patch }));
    setTestResult(null);
  };

  const refreshBackupStats = async () => {
    setBackupLoading(true);
    try {
      const [dbResp, storageResp, settingsResp] = await Promise.all([
        fetch('/__proxy/db/stats'),
        fetch('/__proxy/upload/storage-stats'),
        fetch('/__proxy/db/settings'),
      ]);
      const [dbData, storageData, settingsData] = await Promise.all([
        dbResp.json(),
        storageResp.json(),
        settingsResp.json(),
      ]);
      if (!dbResp.ok) throw new Error(dbData.error || `DB HTTP ${dbResp.status}`);
      if (!storageResp.ok) throw new Error(storageData.error || `Storage HTTP ${storageResp.status}`);
      if (!settingsResp.ok) throw new Error(settingsData.error || `Settings HTTP ${settingsResp.status}`);
      setDbStats({
        fileSize: Number(dbData.fileSize || 0),
        counts: dbData.counts || {},
        materialsTotalSizeBytes: Number(dbData.materialsTotalSizeBytes || 0),
        materialsByStatus: dbData.materialsByStatus || {},
        materialsBySubject: dbData.materialsBySubject || {},
      });
      setStorageStats({
        backend: String(storageData.backend || 'unknown'),
        totalObjects: Number(storageData.totalObjects || 0),
        totalSize: Number(storageData.totalSize || 0),
        buckets: Array.isArray(storageData.buckets) ? storageData.buckets : [],
      });
      setCapacityLimits({
        dbSoftLimitMB: Number(settingsData?.capacityLimits?.dbSoftLimitMB || DEFAULT_CAPACITY_LIMITS.dbSoftLimitMB),
        storageSoftLimitGB: Number(settingsData?.capacityLimits?.storageSoftLimitGB || DEFAULT_CAPACITY_LIMITS.storageSoftLimitGB),
      });
    } catch (error) {
      toast.error(`刷新监控失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBackupLoading(false);
    }
  };

  const handleSaveAi = () => {
    dispatch({ type: 'UPDATE_AI_CONFIG', payload: aiForm });
    toast.success('AI 配置已保存');
  };

  const handleSaveMineru = () => {
    dispatch({ type: 'UPDATE_MINERU_CONFIG', payload: mineruForm });
    toast.success('MinerU 配置已保存');
  };

  const handleTestLocalMineru = async () => {
    setLocalTesting(true);
    setLocalTestResult(null);
    try {
      const result = await checkLocalMinerUHealth(mineruForm.localEndpoint);
      setLocalTestResult(result);
    } finally {
      setLocalTesting(false);
    }
  };

  const handleTestMinio = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await fetch('/__proxy/upload/settings/storage/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(minioForm),
      });
      const data = await resp.json();
      setTestResult({ ok: data.ok, message: data.message });
    } catch (e) {
      setTestResult({ ok: false, message: `请求失败：${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setTesting(false);
    }
  };

  const handleSaveMinio = async () => {
    setSavingMinio(true);
    try {
      const resp = await fetch('/__proxy/upload/settings/storage', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(minioForm),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ message: `HTTP ${resp.status}` }));
        throw new Error(err.message || `HTTP ${resp.status}`);
      }
      // 同步到 store（含 localStorage + db-server）
      dispatch({ type: 'UPDATE_MINIO_CONFIG', payload: minioForm });
      toast.success('存储配置已保存');
    } catch (e) {
      toast.error(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSavingMinio(false);
    }
  };

  const handleSaveCapacity = async () => {
    setSavingCapacity(true);
    try {
      const payload = {
        dbSoftLimitMB: Math.max(1, Number(capacityLimits.dbSoftLimitMB || DEFAULT_CAPACITY_LIMITS.dbSoftLimitMB)),
        storageSoftLimitGB: Math.max(1, Number(capacityLimits.storageSoftLimitGB || DEFAULT_CAPACITY_LIMITS.storageSoftLimitGB)),
      };
      const response = await fetch('/__proxy/db/settings/capacityLimits', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      setCapacityLimits(payload);
      toast.success('容量阈值已保存');
    } catch (error) {
      toast.error(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSavingCapacity(false);
    }
  };

  const clearLocalStateAndReload = () => {
    for (const key of LOCAL_STORAGE_KEYS) {
      localStorage.removeItem(key);
    }
    toast.success('导入成功，页面即将刷新以加载最新数据');
    window.setTimeout(() => window.location.reload(), 1200);
  };

  const handleExportBackup = async () => {
    try {
      const response = await fetch('/__proxy/db/backup/export');
      if (!response.ok) {
        const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `db-metadata-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('元数据备份导出成功');
    } catch (error) {
      toast.error(`导出失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const handleImportBackup = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      const data = JSON.parse(text);
      setConfirmState({ kind: 'json', fileName: file.name, data });
    } catch (error) {
      toast.error(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      event.target.value = '';
    }
  };

  const handleFullExportBackup = async () => {
    if (exportingFull) return;

    setExportingFull(true);
    try {
      const response = await fetch('/__proxy/upload/backup/full-export', { method: 'POST' });
      if (!response.ok) {
        const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `luceon2026-full-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('完整资产备份导出成功');
    } catch (error) {
      toast.error(`导出失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setExportingFull(false);
    }
  };

  const handleSelectFullImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setConfirmState({ kind: 'full', fileName: file.name, file, mode: fullImportMode });
    event.target.value = '';
  };

  const handleConfirmImport = async () => {
    if (!confirmState) return;

    try {
      if (confirmState.kind === 'json') {
        const response = await fetch('/__proxy/db/backup/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true, data: confirmState.data }),
        });
        const result = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(result?.error || `HTTP ${response.status}`);
        }
      } else {
        const formData = new FormData();
        formData.append('file', confirmState.file);
        formData.append('mode', confirmState.mode);
        const response = await fetch('/__proxy/upload/backup/full-import', {
          method: 'POST',
          body: formData,
        });
        const result = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(result?.error || `HTTP ${response.status}`);
        }
      }
      setConfirmState(null);
      clearLocalStateAndReload();
    } catch (error) {
      toast.error(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const totalUsage = (dbStats?.fileSize || 0) + (storageStats?.totalSize || 0);
  const dbLimitBytes = capacityLimits.dbSoftLimitMB * 1024 * 1024;
  const storageLimitBytes = capacityLimits.storageSoftLimitGB * 1024 * 1024 * 1024;
  const dbUsageRatio = dbLimitBytes > 0 ? (dbStats?.fileSize || 0) / dbLimitBytes : 0;
  const storageUsageRatio = storageLimitBytes > 0 ? (storageStats?.totalSize || 0) / storageLimitBytes : 0;

  return (
    <div className="p-6 space-y-5 max-w-3xl">
      {/* 头部 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">系统设置</h1>
        <p className="text-sm text-gray-500 mt-0.5">配置 AI 识别、MinerU 解析接口及存储后端</p>
      </div>

      {/* Tab */}
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => setActiveTab('ai')}
          className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'ai'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-800'
          }`}
        >
          <span className="flex items-center gap-1.5"><Bot size={15} /> AI 识别配置</span>
        </button>
        <button
          onClick={() => setActiveTab('mineru')}
          className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'mineru'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-800'
          }`}
        >
          <span className="flex items-center gap-1.5"><ScanLine size={15} /> MinerU 配置</span>
        </button>
        <button
          onClick={() => setActiveTab('storage')}
          className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'storage'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-800'
          }`}
        >
          <span className="flex items-center gap-1.5"><Database size={15} /> 存储配置</span>
        </button>
        <button
          onClick={() => setActiveTab('backup')}
          className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'backup'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-800'
          }`}
        >
          <span className="flex items-center gap-1.5"><HardDrive size={15} /> 备份与监控</span>
        </button>
      </div>

      {/* ===== AI 配置 ===== */}
      {activeTab === 'ai' && (
        <div className="space-y-5">
          {/* 多提供商列表 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-gray-800">AI 提供商（按优先级依次尝试）</h2>
              <button
                type="button"
                onClick={() => {
                  const newProvider: AiProvider = {
                    id: `custom_${Date.now()}`,
                    name: '自定义',
                    enabled: true,
                    apiEndpoint: 'https://api.example.com/v1/chat/completions',
                    apiKey: '',
                    model: 'gpt-4o-mini',
                    timeout: 120,
                    priority: (aiForm.providers?.length ?? 0) + 1,
                  };
                  updateAi({ providers: [...(aiForm.providers ?? []), newProvider] });
                }}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 border border-blue-200"
              >
                <Plus size={13} /> 新增提供商
              </button>
            </div>

            {(!aiForm.providers || aiForm.providers.length === 0) && (
              <p className="text-xs text-gray-400 py-2">暂无提供商，点击「新增提供商」添加</p>
            )}

            {(aiForm.providers ?? []).map((provider, idx) => {
              const providers = aiForm.providers ?? [];
              const updateProvider = (patch: Partial<AiProvider>) => {
                const next = providers.map((p, i) => i === idx ? { ...p, ...patch } : p);
                updateAi({ providers: next });
              };
              const moveUp = () => {
                if (idx === 0) return;
                const next = [...providers];
                [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                next.forEach((p, i) => { p.priority = i + 1; });
                updateAi({ providers: next });
              };
              const moveDown = () => {
                if (idx === providers.length - 1) return;
                const next = [...providers];
                [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
                next.forEach((p, i) => { p.priority = i + 1; });
                updateAi({ providers: next });
              };
              const remove = () => {
                const next = providers.filter((_, i) => i !== idx);
                next.forEach((p, i) => { p.priority = i + 1; });
                updateAi({ providers: next });
              };

              return (
                <div
                  key={provider.id}
                  className={`rounded-lg border p-4 space-y-3 transition-colors ${
                    provider.enabled ? 'border-blue-200 bg-blue-50/30' : 'border-gray-200 bg-gray-50 opacity-60'
                  }`}
                >
                  {/* 标题行 */}
                  <div className="flex items-center gap-2">
                    <span className="w-5 h-5 rounded-full bg-blue-100 text-blue-600 text-[10px] font-bold flex items-center justify-center flex-shrink-0">
                      {idx + 1}
                    </span>
                    <input
                      className="flex-1 text-sm font-medium bg-transparent border-b border-transparent focus:border-blue-300 outline-none text-gray-800 min-w-0"
                      value={provider.name}
                      onChange={(e) => updateProvider({ name: e.target.value })}
                      placeholder="提供商名称"
                    />
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <button type="button" onClick={moveUp} disabled={idx === 0} className="p-1 text-gray-400 hover:text-gray-600 disabled:opacity-30" title="上移">
                        <ChevronUp size={14} />
                      </button>
                      <button type="button" onClick={moveDown} disabled={idx === providers.length - 1} className="p-1 text-gray-400 hover:text-gray-600 disabled:opacity-30" title="下移">
                        <ChevronDown size={14} />
                      </button>
                      <button
                        type="button"
                        onClick={() => updateProvider({ enabled: !provider.enabled })}
                        className={`p-1 ${provider.enabled ? 'text-blue-500 hover:text-blue-700' : 'text-gray-400 hover:text-gray-600'}`}
                        title={provider.enabled ? '禁用' : '启用'}
                      >
                        {provider.enabled ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                      </button>
                      <button type="button" onClick={remove} className="p-1 text-red-400 hover:text-red-600" title="删除">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>

                  {/* 字段 */}
                  <div className="grid grid-cols-1 gap-2">
                    <FieldRow label="API 地址">
                      <Input
                        value={provider.apiEndpoint}
                        onChange={(v) => updateProvider({ apiEndpoint: v })}
                        placeholder="https://api.example.com/v1/chat/completions"
                      />
                    </FieldRow>
                    <div className="grid grid-cols-2 gap-2">
                      <FieldRow label="API Key">
                        <div className="relative">
                          <Input
                            type="password"
                            value={provider.apiKey}
                            onChange={(v) => updateProvider({ apiKey: v })}
                            placeholder={provider.id === 'ollama' ? '本地无需填写' : 'sk-...'}
                          />
                        </div>
                      </FieldRow>
                      <FieldRow label="模型名称">
                        <Input
                          value={provider.model}
                          onChange={(v) => updateProvider({ model: v })}
                          placeholder="moonshot-v1-32k"
                        />
                      </FieldRow>
                    </div>
                    <FieldRow label="超时（秒）">
                      <Input
                        type="number"
                        value={provider.timeout}
                        onChange={(v) => updateProvider({ timeout: Number(v) })}
                      />
                    </FieldRow>
                  </div>
                </div>
              );
            })}

            <p className="text-xs text-gray-400">
              * AI 分析时按优先级顺序依次尝试各提供商，第一个成功即返回结果。429/401 等错误自动跳过到下一个。Ollama 作为本地兜底无需 API Key。
            </p>
          </div>

          {/* 提示词配置 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">识别提示词</h2>
            {(Object.keys(aiForm.prompts) as (keyof AiConfig['prompts'])[]).map((key) => (
              <FieldRow key={key} label={PROMPT_LABELS[key] ?? key}>
                <Textarea
                  value={aiForm.prompts[key]}
                  onChange={(v) => updateAiPrompt(key, v)}
                  rows={2}
                />
              </FieldRow>
            ))}
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleSaveAi}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              <Save size={15} /> 保存 AI 配置
            </button>
          </div>
        </div>
      )}

      {/* ===== MinerU 配置 ===== */}
      {activeTab === 'mineru' && (
        <div className="space-y-5">
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">接口参数</h2>
            <FieldRow label="解析引擎" hint="本地模式同步返回，官方模式走轮询任务">
              <div className="space-y-3">
                <label className="flex items-center justify-between p-3 rounded-lg border border-green-200 bg-green-50 cursor-pointer">
                  <div>
                    <p className="text-sm font-medium text-green-800">本地 Gradio（推荐，无额度限制）</p>
                    <p className="text-xs text-green-700 mt-0.5">适合内网部署，解析结果可直接回存当前文件库</p>
                  </div>
                  <input
                    type="radio"
                    name="mineruEngine"
                    checked={mineruForm.engine === 'local'}
                    onChange={() => updateMineru({ engine: 'local' })}
                  />
                </label>
                <label className="flex items-center justify-between p-3 rounded-lg border border-blue-200 bg-blue-50 cursor-pointer">
                  <div>
                    <p className="text-sm font-medium text-blue-800">官方 API（需 API Key）</p>
                    <p className="text-xs text-blue-700 mt-0.5">兼容现有云端解析流程，保留 ZIP 回存链路</p>
                  </div>
                  <input
                    type="radio"
                    name="mineruEngine"
                    checked={mineruForm.engine === 'cloud'}
                    onChange={() => updateMineru({ engine: 'cloud' })}
                  />
                </label>
              </div>
            </FieldRow>
            {mineruForm.engine === 'local' ? (
              <>
                <FieldRow label="本地地址">
                  <Input
                    value={mineruForm.localEndpoint}
                    onChange={(v) => updateMineru({ localEndpoint: v })}
                    placeholder="http://192.168.31.33:8083"
                  />
                </FieldRow>
                <FieldRow label="本地超时（秒）">
                  <Input
                    type="number"
                    value={mineruForm.localTimeout}
                    onChange={(v) => updateMineru({ localTimeout: Number(v) })}
                  />
                </FieldRow>
                <FieldRow label="解析引擎选择">
                  <Select
                    value={mineruForm.localBackend || 'hybrid-auto-engine'}
                    onChange={(v) => updateMineru({ localBackend: v })}
                    options={[
                      { value: 'hybrid-auto-engine', label: 'hybrid-auto-engine（推荐）' },
                      { value: 'vlm-auto-engine', label: 'vlm-auto-engine（高精度/耗时）' },
                      { value: 'pipeline', label: 'pipeline（更快）' },
                    ]}
                  />
                </FieldRow>
                <FieldRow label="max_pages">
                  <Input
                    type="number"
                    value={mineruForm.localMaxPages}
                    onChange={(v) => updateMineru({ localMaxPages: Number(v) })}
                  />
                </FieldRow>
                <FieldRow label="OCR 语言">
                  <Input
                    value={mineruForm.localOcrLanguage}
                    onChange={(v) => updateMineru({ localOcrLanguage: v })}
                    placeholder="ch"
                  />
                </FieldRow>
                {localTestResult && (
                  <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${localTestResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                    {localTestResult.ok
                      ? <CheckCircle size={16} className="mt-0.5 flex-shrink-0" />
                      : <XCircle size={16} className="mt-0.5 flex-shrink-0" />}
                    <span>{localTestResult.message}</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <FieldRow label="API 地址">
                  <Input
                    value={mineruForm.apiEndpoint}
                    onChange={(v) => updateMineru({ apiEndpoint: v })}
                  />
                </FieldRow>
                <FieldRow label="API Key">
                  <div className="relative">
                    <Input
                      type={showKey ? 'text' : 'password'}
                      value={mineruForm.apiKey}
                      onChange={(v) => updateMineru({ apiKey: v })}
                      className="pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowKey(!showKey)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </FieldRow>
                <FieldRow label="超时（秒）">
                  <Input
                    type="number"
                    value={mineruForm.timeout}
                    onChange={(v) => updateMineru({ timeout: Number(v) })}
                  />
                </FieldRow>
                <FieldRow label="API 模式">
                  <div className="flex gap-3">
                    {(['precise', 'agent'] as const).map((mode) => (
                      <label key={mode} className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="radio"
                          name="apiMode"
                          value={mode}
                          checked={mineruForm.apiMode === mode}
                          onChange={() => updateMineru({ apiMode: mode })}
                        />
                        <span className="text-sm text-gray-700 capitalize">{mode}</span>
                      </label>
                    ))}
                  </div>
                </FieldRow>
              </>
            )}
            <FieldRow label="模型版本">
              <div className="flex gap-3">
                {(['pipeline', 'vlm'] as const).map((v) => (
                  <label key={v} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="modelVersion"
                      value={v}
                      checked={mineruForm.modelVersion === v}
                      onChange={() => updateMineru({ modelVersion: v })}
                    />
                    <span className="text-sm text-gray-700 uppercase">{v}</span>
                  </label>
                ))}
              </div>
            </FieldRow>
          </div>

          {/* 功能开关 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
            <h2 className="font-semibold text-gray-800">功能开关</h2>
            {[
              { key: 'enableOcr',     label: 'OCR 识别' },
              { key: 'enableFormula', label: '公式识别' },
              { key: 'enableTable',   label: '表格识别' },
            ].map((item) => {
              const val = mineruForm[item.key as keyof MinerUConfig] as boolean;
              return (
                <label key={item.key} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg cursor-pointer">
                  <span className="text-sm text-gray-700">{item.label}</span>
                  <input
                    type="checkbox"
                    checked={val}
                    onChange={(e) => updateMineru({ [item.key]: e.target.checked })}
                    className="w-4 h-4 rounded"
                  />
                </label>
              );
            })}
            <FieldRow label="识别语言">
              <Input
                value={mineruForm.language}
                onChange={(v) => updateMineru({ language: v })}
                placeholder="ch"
              />
            </FieldRow>
          </div>

          <div className="flex justify-end">
            {mineruForm.engine === 'local' && (
              <button
                onClick={handleTestLocalMineru}
                disabled={localTesting}
                className="mr-3 flex items-center gap-2 px-4 py-2 text-sm border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                {localTesting ? <Loader size={14} className="animate-spin" /> : <ScanLine size={14} />}
                {localTesting ? '测试中...' : '测试本地连接'}
              </button>
            )}
            <button
              onClick={handleSaveMineru}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              <Save size={15} /> 保存 MinerU 配置
            </button>
          </div>
        </div>
      )}

      {/* ===== 存储配置 ===== */}
      {activeTab === 'storage' && (
        <div className="space-y-5">
          {/* 存储后端 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">存储后端</h2>
            <FieldRow label="存储后端" hint="minio：私有对象存储；tmpfiles：临时公开存储">
              <div className="flex gap-4">
                {(['minio', 'tmpfiles'] as const).map((b) => (
                  <label key={b} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="storageBackend"
                      value={b}
                      checked={minioForm.storageBackend === b}
                      onChange={() => updateMinio({ storageBackend: b })}
                    />
                    <span className="text-sm text-gray-700">{b === 'minio' ? 'MinIO（推荐）' : 'tmpfiles（临时）'}</span>
                  </label>
                ))}
              </div>
            </FieldRow>
          </div>

          {/* MinIO 连接参数 */}
          <div className={`bg-white rounded-xl border p-5 space-y-4 transition-opacity ${minioForm.storageBackend !== 'minio' ? 'opacity-50 pointer-events-none border-gray-200' : 'border-gray-200'}`}>
            <h2 className="font-semibold text-gray-800">MinIO 连接参数</h2>
            <FieldRow label="端点地址" hint="主机名或 IP，不含协议">
              <Input
                value={minioForm.endpoint}
                onChange={(v) => updateMinio({ endpoint: v })}
                placeholder="minio 或 192.168.1.100"
                disabled={minioForm.storageBackend !== 'minio'}
              />
            </FieldRow>
            <FieldRow label="端口">
              <Input
                type="number"
                value={minioForm.port}
                onChange={(v) => updateMinio({ port: Number(v) })}
                placeholder="9000"
                disabled={minioForm.storageBackend !== 'minio'}
              />
            </FieldRow>
            <FieldRow label="使用 SSL">
              <label className="flex items-center gap-2 cursor-pointer mt-2">
                <input
                  type="checkbox"
                  checked={minioForm.useSSL}
                  onChange={(e) => updateMinio({ useSSL: e.target.checked })}
                  disabled={minioForm.storageBackend !== 'minio'}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm text-gray-700">启用 HTTPS</span>
              </label>
            </FieldRow>
            <FieldRow label="Access Key">
              <div className="relative">
                <Input
                  type={showMinioKeys.access ? 'text' : 'password'}
                  value={minioForm.accessKey}
                  onChange={(v) => updateMinio({ accessKey: v })}
                  placeholder="minioadmin"
                  className="pr-10"
                  disabled={minioForm.storageBackend !== 'minio'}
                />
                <button
                  type="button"
                  onClick={() => setShowMinioKeys((p) => ({ ...p, access: !p.access }))}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showMinioKeys.access ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </FieldRow>
            <FieldRow label="Secret Key">
              <div className="relative">
                <Input
                  type={showMinioKeys.secret ? 'text' : 'password'}
                  value={minioForm.secretKey}
                  onChange={(v) => updateMinio({ secretKey: v })}
                  placeholder="minioadmin"
                  className="pr-10"
                  disabled={minioForm.storageBackend !== 'minio'}
                />
                <button
                  type="button"
                  onClick={() => setShowMinioKeys((p) => ({ ...p, secret: !p.secret }))}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showMinioKeys.secret ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </FieldRow>
            <FieldRow label="原始资料 Bucket" hint="存放上传原始文件的存储桶">
              <Input
                value={minioForm.bucket}
                onChange={(v) => updateMinio({ bucket: v })}
                placeholder="eduassets"
                disabled={minioForm.storageBackend !== 'minio'}
              />
            </FieldRow>
            <FieldRow label="解析产物 Bucket" hint="存放 MinerU 解析输出物的存储桶">
              <Input
                value={minioForm.parsedBucket}
                onChange={(v) => updateMinio({ parsedBucket: v })}
                placeholder="eduassets-parsed"
                disabled={minioForm.storageBackend !== 'minio'}
              />
            </FieldRow>
            <FieldRow label="URL 有效期" hint="预签名 URL 有效秒数">
              <Input
                type="number"
                value={minioForm.presignedExpiry}
                onChange={(v) => updateMinio({ presignedExpiry: Number(v) })}
                placeholder="3600"
                disabled={minioForm.storageBackend !== 'minio'}
              />
            </FieldRow>
          </div>

          {/* 测试结果 */}
          {testResult && (
            <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${testResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {testResult.ok
                ? <CheckCircle size={16} className="mt-0.5 flex-shrink-0" />
                : <XCircle size={16} className="mt-0.5 flex-shrink-0" />}
              <span>{testResult.message}</span>
            </div>
          )}

          {/* 操作按钮 */}
          <div className="flex items-center justify-between">
            <button
              onClick={handleTestMinio}
              disabled={testing || minioForm.storageBackend !== 'minio'}
              className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {testing ? <Loader size={14} className="animate-spin" /> : <Database size={14} />}
              {testing ? '测试中...' : '测试连接'}
            </button>
            <button
              onClick={handleSaveMinio}
              disabled={savingMinio}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {savingMinio ? <Loader size={15} className="animate-spin" /> : <Save size={15} />}
              {savingMinio ? '保存中...' : '保存存储配置'}
            </button>
          </div>
        </div>
      )}

      {activeTab === 'backup' && (
        <div className="space-y-5">
          <input
            ref={jsonImportInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleImportBackup}
          />
          <input
            ref={fullImportInputRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={handleSelectFullImport}
          />

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <p className="text-xs text-gray-500">JSON 数据库大小</p>
              <p className="mt-1 text-2xl font-semibold text-gray-900">{formatBytes(dbStats?.fileSize || 0)}</p>
              <p className="mt-1 text-xs text-gray-400">materials {dbStats?.counts?.materials || 0} · settings {dbStats?.counts?.settings || 0}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <p className="text-xs text-gray-500">对象存储占用</p>
              <p className="mt-1 text-2xl font-semibold text-gray-900">{formatBytes(storageStats?.totalSize || 0)}</p>
              <p className="mt-1 text-xs text-gray-400">{storageStats?.backend || 'unknown'} · {storageStats?.totalObjects || 0} 个对象</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <p className="text-xs text-gray-500">总占用</p>
              <p className="mt-1 text-2xl font-semibold text-gray-900">{formatBytes(totalUsage)}</p>
              <p className="mt-1 text-xs text-gray-400">数据库 + 文件存储</p>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-gray-800">容量管理</h2>
              <button
                onClick={refreshBackupStats}
                disabled={backupLoading}
                className="flex items-center gap-2 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {backupLoading ? <Loader size={14} className="animate-spin" /> : <HardDrive size={14} />}
                {backupLoading ? '刷新中...' : '刷新统计'}
              </button>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <FieldRow label="数据库软上限">
                <Input
                  type="number"
                  value={capacityLimits.dbSoftLimitMB}
                  onChange={(v) => setCapacityLimits((prev) => ({ ...prev, dbSoftLimitMB: Number(v) }))}
                />
              </FieldRow>
              <FieldRow label="对象存储软上限">
                <Input
                  type="number"
                  value={capacityLimits.storageSoftLimitGB}
                  onChange={(v) => setCapacityLimits((prev) => ({ ...prev, storageSoftLimitGB: Number(v) }))}
                />
              </FieldRow>
            </div>
            <div className="space-y-3">
              <div>
                <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
                  <span>JSON 数据库</span>
                  <span className={getUsageTextTone(dbUsageRatio)}>
                    {formatBytes(dbStats?.fileSize || 0)} / {formatBytes(dbLimitBytes)}
                  </span>
                </div>
                <div className="h-2 rounded-full bg-gray-100 overflow-hidden">
                  <div className={`h-full ${getUsageTone(dbUsageRatio)}`} style={{ width: `${Math.min(dbUsageRatio * 100, 100)}%` }} />
                </div>
                <p className={`mt-1 text-xs ${getUsageTextTone(dbUsageRatio)}`}>
                  使用率 {(dbUsageRatio * 100).toFixed(1)}% · 剩余 {formatBytes(Math.max(dbLimitBytes - (dbStats?.fileSize || 0), 0))}
                </p>
              </div>
              <div>
                <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
                  <span>对象存储</span>
                  <span className={getUsageTextTone(storageUsageRatio)}>
                    {formatBytes(storageStats?.totalSize || 0)} / {formatBytes(storageLimitBytes)}
                  </span>
                </div>
                <div className="h-2 rounded-full bg-gray-100 overflow-hidden">
                  <div className={`h-full ${getUsageTone(storageUsageRatio)}`} style={{ width: `${Math.min(storageUsageRatio * 100, 100)}%` }} />
                </div>
                <p className={`mt-1 text-xs ${getUsageTextTone(storageUsageRatio)}`}>
                  使用率 {(storageUsageRatio * 100).toFixed(1)}% · 剩余 {formatBytes(Math.max(storageLimitBytes - (storageStats?.totalSize || 0), 0))}
                </p>
              </div>
            </div>
            {(dbUsageRatio >= 0.7 || storageUsageRatio >= 0.7) && (
              <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${dbUsageRatio >= 0.9 || storageUsageRatio >= 0.9 ? 'border-red-200 bg-red-50 text-red-700' : 'border-yellow-200 bg-yellow-50 text-yellow-700'}`}>
                <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                <span>接近容量上限，建议清理或扩容。</span>
              </div>
            )}
            <div className="flex justify-end">
              <button
                onClick={handleSaveCapacity}
                disabled={savingCapacity}
                className="flex items-center gap-2 px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {savingCapacity ? <Loader size={14} className="animate-spin" /> : <Save size={14} />}
                {savingCapacity ? '保存中...' : '保存容量阈值'}
              </button>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">对象存储明细</h2>
            <div className="space-y-3">
              {(storageStats?.buckets || []).map((bucket) => (
                <div key={bucket.name} className="flex items-center justify-between rounded-lg bg-gray-50 px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{bucket.name}</p>
                    <p className="text-xs text-gray-400">{bucket.objectCount} 个对象</p>
                  </div>
                  <p className="text-sm text-gray-700">{formatBytes(bucket.totalSize)}</p>
                </div>
              ))}
              {(storageStats?.buckets || []).length === 0 && (
                <div className="text-sm text-gray-400">当前没有可用的桶统计信息</div>
              )}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">容量画像</h2>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs text-gray-500">资料总字节数</p>
                <p className="mt-1 text-lg font-semibold text-gray-900">{formatBytes(dbStats?.materialsTotalSizeBytes || 0)}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs text-gray-500">学科覆盖</p>
                <p className="mt-1 text-lg font-semibold text-gray-900">{Object.keys(dbStats?.materialsBySubject || {}).length} 个学科</p>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <p className="text-sm font-medium text-gray-700">按状态分布</p>
                {Object.entries(dbStats?.materialsByStatus || {}).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700">
                    <span>{key}</span>
                    <span>{value}</span>
                  </div>
                ))}
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium text-gray-700">按学科分布</p>
                {Object.entries(dbStats?.materialsBySubject || {}).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700">
                    <span>{key}</span>
                    <span>{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">备份与恢复</h2>
            <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
              导出元数据 JSON 仅恢复数据库记录，不恢复 MinIO 文件。完整资产备份会同时包含原始资料、解析产物与数据库快照。
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="rounded-lg border border-gray-200 p-4 space-y-3">
                <p className="font-medium text-gray-800">JSON 元数据备份</p>
                <p className="text-xs text-gray-500">适合快速迁移数据库记录，不包含 MinIO 原始文件与解析产物。</p>
                <div className="flex items-center gap-3">
                  <button
                    onClick={handleExportBackup}
                    className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50"
                  >
                    <Download size={14} /> 导出元数据 JSON
                  </button>
                  <button
                    onClick={() => jsonImportInputRef.current?.click()}
                    className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50"
                  >
                    <Upload size={14} /> 导入元数据 JSON
                  </button>
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 p-4 space-y-3">
                <p className="font-medium text-gray-800">完整资产备份</p>
                <p className="text-xs text-gray-500">包含 JSON 数据库、MinIO 原始资料文件与 MinerU 解析产物。</p>
                <div className="flex items-center gap-3">
                  <button
                    onClick={handleFullExportBackup}
                    disabled={exportingFull}
                    className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {exportingFull ? <Loader size={14} className="animate-spin" /> : <Download size={14} />}
                    {exportingFull ? '正在导出...' : '导出完整资产'}
                  </button>
                  <button
                    onClick={() => fullImportInputRef.current?.click()}
                    className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50"
                  >
                    <Upload size={14} /> 导入完整资产
                  </button>
                </div>
                {exportingFull && (
                  <p className="text-xs text-amber-600">正在打包完整资产，大容量备份可能耗时较长，请勿重复点击。</p>
                )}
                <div className="flex gap-4 text-sm text-gray-600">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      checked={fullImportMode === 'replace'}
                      onChange={() => setFullImportMode('replace')}
                    />
                    <span>replace 覆盖恢复</span>
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      checked={fullImportMode === 'merge'}
                      onChange={() => setFullImportMode('merge')}
                    />
                    <span>merge 仅补缺失</span>
                  </label>
                </div>
              </div>
            </div>
          </div>

          {confirmState && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-5 space-y-4">
              <div className="flex items-start gap-3">
                <AlertTriangle size={18} className="mt-0.5 text-red-600" />
                <div className="space-y-1">
                  <p className="font-semibold text-red-700">危险操作确认</p>
                  <p className="text-sm text-red-700">
                    {confirmState.kind === 'json'
                      ? '导入元数据 JSON 会覆盖当前数据库，并自动创建 .bak 备份。MinIO 文件不会被恢复。'
                      : `导入完整资产会${confirmState.mode === 'replace' ? '覆盖数据库与 MinIO 文件' : '补充数据库与 MinIO 中缺失对象'}，导入后页面将自动刷新。`}
                  </p>
                  <p className="text-xs text-red-600">待导入文件：{confirmState.fileName}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={handleConfirmImport}
                  className="flex items-center gap-2 px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700"
                >
                  <Upload size={14} /> 确认导入
                </button>
                <button
                  onClick={() => setConfirmState(null)}
                  className="px-4 py-2 text-sm border border-red-200 text-red-700 rounded-lg hover:bg-white"
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
