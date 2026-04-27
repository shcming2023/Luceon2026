import { readFile } from 'node:fs/promises';
import path from 'node:path';

const uploadBase = (process.env.UPLOAD_BASE_URL || 'http://localhost:8788').replace(/\/+$/, '');
const localEndpoint = (process.env.LOCAL_MINERU_ENDPOINT || 'http://127.0.0.1:8083').replace(/\/+$/, '');
const filePath = process.env.TEST_FILE;
const materialId = process.env.TEST_MATERIAL_ID || `${Date.now()}`;
const backend = process.env.LOCAL_MINERU_BACKEND || 'pipeline';
const maxPages = Number(process.env.LOCAL_MINERU_MAX_PAGES || 1000);
const ocrLanguage = process.env.LOCAL_MINERU_OCR_LANGUAGE || 'ch';
const parseMethod = process.env.LOCAL_MINERU_PARSE_METHOD || '';

if (!filePath) {
  console.error('缺少 TEST_FILE 环境变量');
  process.exit(1);
}

const buffer = await readFile(filePath);
const fileName = path.basename(filePath);

const healthResponse = await fetch(`${uploadBase}/parse/local-mineru/health`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ localEndpoint }),
});
const healthData = await healthResponse.json().catch(() => null);
if (!healthResponse.ok || !healthData?.ok) {
  console.error('本地 MinerU 健康检查失败', healthData || healthResponse.status);
  process.exit(1);
}

const formData = new FormData();
formData.append('file', new Blob([buffer]), fileName);
formData.append('materialId', String(materialId));
formData.append('localEndpoint', localEndpoint);
formData.append('localTimeout', String(process.env.LOCAL_MINERU_TIMEOUT || 300));
formData.append('backend', backend);
formData.append('maxPages', String(maxPages));
formData.append('ocrLanguage', ocrLanguage);
formData.append('language', ocrLanguage);
if (parseMethod) formData.append('parseMethod', parseMethod);
formData.append('enableOcr', String(process.env.LOCAL_MINERU_ENABLE_OCR === 'true'));
formData.append('enableFormula', String(process.env.LOCAL_MINERU_ENABLE_FORMULA !== 'false'));
formData.append('enableTable', String(process.env.LOCAL_MINERU_ENABLE_TABLE !== 'false'));

const parseResponse = await fetch(`${uploadBase}/parse/local-mineru`, {
  method: 'POST',
  body: formData,
});
const parseData = await parseResponse.json().catch(() => null);
if (!parseResponse.ok) {
  console.error('本地 MinerU 解析失败', parseData || parseResponse.status);
  process.exit(1);
}
if (!parseData?.markdown) {
  console.error('未返回 markdown 内容', parseData);
  process.exit(1);
}

console.log(JSON.stringify({
  health: healthData,
  result: {
    taskId: parseData.taskId,
    state: parseData.state,
    markdownLength: String(parseData.markdown).length,
    markdownObjectName: parseData.markdownObjectName || '',
    markdownUrl: parseData.markdownUrl || '',
    parsedFilesCount: parseData.parsedFilesCount || 0,
  },
}, null, 2));
