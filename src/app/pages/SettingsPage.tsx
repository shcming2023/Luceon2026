import { useState, useEffect } from 'react';
import { Save, Eye, EyeOff, Bot, ScanLine, Link } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { AiConfig, MinerUConfig } from '../../store/types';
import { backupFetch } from '../../utils/backupApi';

type ActiveTab = 'ai' | 'mineru' | 'connection';

interface OverleafSettings {
  overleafBaseUrl?: string;
  adminEmail?: string;
  adminPassword?: string;
  hostBackupPath?: string;
  concurrency?: number;
  accessToken?: string;
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4">
      <label className="w-32 text-sm text-gray-500 mt-2 flex-shrink-0">{label}</label>
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
}: {
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  className?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={`w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 ${className}`}
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

export function SettingsPage() {
  const { state, dispatch } = useAppStore();
  const [activeTab, setActiveTab] = useState<ActiveTab>('ai');
  const [showKey, setShowKey] = useState(false);

  // 本地副本（保存前不 dispatch）
  const [aiForm, setAiForm] = useState<AiConfig>({ ...state.aiConfig });
  const [mineruForm, setMineruForm] = useState<MinerUConfig>({ ...state.mineruConfig });

  // 连接设置
  const [connSettings, setConnSettings] = useState<OverleafSettings>({});
  const [connConcurrency, setConnConcurrency] = useState(3);
  const [connLoading, setConnLoading] = useState(false);

  useEffect(() => {
    if (activeTab === 'connection') {
      backupFetch<OverleafSettings>('/settings').then((cfg) => {
        setConnSettings(cfg);
        setConnConcurrency(cfg.concurrency ?? 3);
      }).catch(() => {});
    }
  }, [activeTab]);

  const handleSaveConnection = async () => {
    setConnLoading(true);
    try {
      await backupFetch('/settings', {
        method: 'POST',
        body: JSON.stringify({ concurrency: connConcurrency }),
      });
      toast.success('连接设置已保存');
    } catch (e: unknown) {
      toast.error((e as Error).message || '保存失败');
    } finally {
      setConnLoading(false);
    }
  };

  const updateAi = (patch: Partial<AiConfig>) => setAiForm((prev) => ({ ...prev, ...patch }));
  const updateAiPrompt = (key: keyof AiConfig['prompts'], val: string) =>
    setAiForm((prev) => ({ ...prev, prompts: { ...prev.prompts, [key]: val } }));
  const updateMineru = (patch: Partial<MinerUConfig>) => setMineruForm((prev) => ({ ...prev, ...patch }));

  const handleSaveAi = () => {
    dispatch({ type: 'UPDATE_AI_CONFIG', payload: aiForm });
    toast.success('AI 配置已保存');
  };

  const handleSaveMineru = () => {
    dispatch({ type: 'UPDATE_MINERU_CONFIG', payload: mineruForm });
    toast.success('MinerU 配置已保存');
  };

  return (
    <div className="p-6 space-y-5 max-w-3xl">
      {/* 头部 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">系统设置</h1>
        <p className="text-sm text-gray-500 mt-0.5">配置 AI 识别与 MinerU 解析接口</p>
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
          onClick={() => setActiveTab('connection')}
          className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'connection'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-800'
          }`}
        >
          <span className="flex items-center gap-1.5"><Link size={15} /> 连接设置</span>
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
            <h2 className="font-semibold text-gray-800">提示词配置</h2>
            {(Object.keys(aiForm.prompts) as (keyof AiConfig['prompts'])[]).map((key) => (
              <FieldRow key={key} label={key}>
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
            <button
              onClick={handleSaveMineru}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              <Save size={15} /> 保存 MinerU 配置
            </button>
          </div>
        </div>
      )}

      {/* ===== 连接设置 ===== */}
      {activeTab === 'connection' && (
        <div className="space-y-5">
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="font-semibold text-gray-800">Overleaf 连接配置</h2>
            <FieldRow label="Overleaf 地址">
              <Input value={connSettings.overleafBaseUrl ?? ''} onChange={() => {}} className="bg-gray-50 cursor-default" />
            </FieldRow>
            <FieldRow label="管理员邮箱">
              <Input value={connSettings.adminEmail ?? ''} onChange={() => {}} className="bg-gray-50 cursor-default" />
            </FieldRow>
            <FieldRow label="管理员密码">
              <Input type="password" value={connSettings.adminPassword ? '***' : ''} onChange={() => {}} className="bg-gray-50 cursor-default" />
            </FieldRow>
            <FieldRow label="备份目录">
              <Input value={connSettings.hostBackupPath ?? ''} onChange={() => {}} className="bg-gray-50 cursor-default" />
            </FieldRow>
            <FieldRow label="并发下载数">
              <Input
                type="number"
                value={connConcurrency}
                onChange={(v) => setConnConcurrency(Number(v))}
                placeholder="3"
              />
            </FieldRow>
            <FieldRow label="API Token">
              <Input value={connSettings.accessToken ?? ''} onChange={() => {}} className="bg-gray-50 cursor-default font-mono text-xs" />
            </FieldRow>
          </div>
          <div className="flex justify-end">
            <button
              onClick={handleSaveConnection}
              disabled={connLoading}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              <Save size={15} /> 保存连接设置
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
