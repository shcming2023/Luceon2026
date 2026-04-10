import { useState, useEffect, useRef } from 'react';
import { Save, Eye, EyeOff, Bot, ScanLine, Database, CheckCircle, XCircle, Loader, Download, Upload, HardDrive } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { AiConfig, MinerUConfig, MinioConfig } from '../../store/types';
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

export function SettingsPage() {
  const { state, dispatch } = useAppStore();
  const [activeTab, setActiveTab] = useState<ActiveTab>('ai');
  const [showKey, setShowKey] = useState(false);
  const [showMinioKeys, setShowMinioKeys] = useState({ access: false, secret: false });
  const importInputRef = useRef<HTMLInputElement>(null);

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
  } | null>(null);
  const [storageStats, setStorageStats] = useState<{
    backend: string;
    totalObjects: number;
    totalSize: number;
    buckets: { name: string; objectCount: number; totalSize: number }[];
  } | null>(null);

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
      const [dbResp, storageResp] = await Promise.all([
        fetch('/__proxy/db/stats'),
        fetch('/__proxy/upload/storage-stats'),
      ]);
      const [dbData, storageData] = await Promise.all([
        dbResp.json(),
        storageResp.json(),
      ]);
      if (!dbResp.ok) throw new Error(dbData.error || `DB HTTP ${dbResp.status}`);
      if (!storageResp.ok) throw new Error(storageData.error || `Storage HTTP ${storageResp.status}`);
      setDbStats({
        fileSize: Number(dbData.fileSize || 0),
        counts: dbData.counts || {},
      });
      setStorageStats({
        backend: String(storageData.backend || 'unknown'),
        totalObjects: Number(storageData.totalObjects || 0),
        totalSize: Number(storageData.totalSize || 0),
        buckets: Array.isArray(storageData.buckets) ? storageData.buckets : [],
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
      link.download = `db-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('备份导出成功');
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
      if (!window.confirm('导入会覆盖当前 JSON 数据库，并在服务端自动创建 .bak 备份，确定继续吗？')) {
        event.target.value = '';
        return;
      }

      const response = await fetch('/__proxy/db/backup/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true, data }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(result?.error || `HTTP ${response.status}`);
      }
      toast.success('备份导入成功');
      await refreshBackupStats();
    } catch (error) {
      toast.error(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      event.target.value = '';
    }
  };

  const totalUsage = (dbStats?.fileSize || 0) + (storageStats?.totalSize || 0);

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
          {/* 接口参数 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">接口参数</h2>
            <FieldRow label="API 地址">
              <Input
                value={aiForm.apiEndpoint}
                onChange={(v) => updateAi({ apiEndpoint: v })}
                placeholder="https://api.example.com/v1/chat/completions"
              />
            </FieldRow>
            <FieldRow label="API Key">
              <div className="relative">
                <Input
                  type={showKey ? 'text' : 'password'}
                  value={aiForm.apiKey}
                  onChange={(v) => updateAi({ apiKey: v })}
                  placeholder="sk-..."
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
            <FieldRow label="模型">
              <Input
                value={aiForm.model}
                onChange={(v) => updateAi({ model: v })}
                placeholder="moonshot-v1-32k"
              />
            </FieldRow>
            <FieldRow label="超时（秒）">
              <Input
                type="number"
                value={aiForm.timeout}
                onChange={(v) => updateAi({ timeout: Number(v) })}
              />
            </FieldRow>
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
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleImportBackup}
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
              <h2 className="font-semibold text-gray-800">容量可视化</h2>
              <button
                onClick={refreshBackupStats}
                disabled={backupLoading}
                className="flex items-center gap-2 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {backupLoading ? <Loader size={14} className="animate-spin" /> : <HardDrive size={14} />}
                {backupLoading ? '刷新中...' : '刷新统计'}
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
                  <span>JSON 数据库</span>
                  <span>{formatBytes(dbStats?.fileSize || 0)}</span>
                </div>
                <div className="h-2 rounded-full bg-gray-100 overflow-hidden">
                  <div className="h-full bg-blue-500" style={{ width: `${totalUsage > 0 ? ((dbStats?.fileSize || 0) / totalUsage) * 100 : 0}%` }} />
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
                  <span>对象存储</span>
                  <span>{formatBytes(storageStats?.totalSize || 0)}</span>
                </div>
                <div className="h-2 rounded-full bg-gray-100 overflow-hidden">
                  <div className="h-full bg-green-500" style={{ width: `${totalUsage > 0 ? ((storageStats?.totalSize || 0) / totalUsage) * 100 : 0}%` }} />
                </div>
              </div>
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

          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-4">备份与恢复</h2>
            <div className="flex items-center gap-3">
              <button
                onClick={handleExportBackup}
                className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50"
              >
                <Download size={14} /> 导出 JSON
              </button>
              <button
                onClick={() => importInputRef.current?.click()}
                className="flex items-center gap-2 px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50"
              >
                <Upload size={14} /> 导入 JSON
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
