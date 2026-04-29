import type { AppState } from '../../store/types';

/**
 * 将当前全局配置中的 MinerU 执行参数快照显式附加到 FormData 中。
 * 避免后端因缺失明确参数而触发 prevent-hybrid-drift-no-explicit-request 规则回退到 pipeline。
 */
export function appendMineruTaskOptions(formData: FormData, mineruConfig: AppState['mineruConfig']) {
  if (!mineruConfig) return;
  
  formData.append('backend', mineruConfig.localBackend || 'pipeline');
  formData.append('localTimeout', String(mineruConfig.localTimeout || 3600));
  formData.append('ocrLanguage', mineruConfig.localOcrLanguage || mineruConfig.language || 'ch');
  formData.append('language', mineruConfig.language || 'ch');
  formData.append('enableOcr', String(mineruConfig.enableOcr ?? false));
  formData.append('enableFormula', String(mineruConfig.enableFormula ?? true));
  formData.append('enableTable', String(mineruConfig.enableTable ?? true));
  formData.append('maxPages', String(mineruConfig.localMaxPages || mineruConfig.maxPages || 1000));
}
