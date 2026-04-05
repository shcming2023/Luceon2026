/**
 * MinerU + Kimi AI 端到端测试脚本
 *
 * 流程：
 *   1. 上传 PDF 到 tmpfiles.org 获取公开 URL
 *   2. 提交 MinerU 任务（URL 模式）
 *   3. 轮询任务状态直到完成
 *   4. 下载 zip，提取 full.md
 *   5. （可选）调用 Kimi AI 分析 markdown
 *
 * 用法：
 *   MINERU_API_KEY=<key> KIMI_API_KEY=<key> node server/test-mineru.mjs [pdf路径]
 *
 * 默认测试文件：test/companion_materials_final.pdf
 */

import { readFileSync, existsSync } from 'node:fs';
import { resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';

const __dir = fileURLToPath(new URL('.', import.meta.url));
const ROOT = resolve(__dir, '..');

// ── 配置 ─────────────────────────────────────────────────────────────────────

const MINERU_KEY = process.env.MINERU_API_KEY || '';
const KIMI_KEY   = process.env.KIMI_API_KEY   || '';
const TEST_FILE  = process.env.MINERU_TEST_FILE
  || resolve(ROOT, 'test/companion_materials_final.pdf');

const MINERU_CREATE_URL = 'https://mineru.net/api/v4/extract/task';
const MINERU_TASK_BASE  = 'https://mineru.net/api/v4/extract/task';
const TMPFILES_UPLOAD   = 'https://tmpfiles.org/api/v1/upload';
const KIMI_URL          = 'https://api.moonshot.cn/v1/chat/completions';

const POLL_INTERVAL_MS  = 3_000;
const POLL_TIMEOUT_MS   = 120_000;
const MODEL_VERSION     = 'vlm';

// ── 颜色工具 ──────────────────────────────────────────────────────────────────

const c = {
  cyan:   (s) => `\x1b[36m${s}\x1b[0m`,
  green:  (s) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  red:    (s) => `\x1b[31m${s}\x1b[0m`,
  bold:   (s) => `\x1b[1m${s}\x1b[0m`,
  dim:    (s) => `\x1b[2m${s}\x1b[0m`,
};

function log(label, msg = '') { console.log(`${c.bold(label)} ${msg}`); }
function logJson(label, obj)  { console.log(`${c.bold(label)}\n${JSON.stringify(obj, null, 2)}`); }
function sleep(ms)            { return new Promise(r => setTimeout(r, ms)); }

function hr(char = '─', len = 60) { return char.repeat(len); }

// ── Step 1: 上传文件到 tmpfiles.org ──────────────────────────────────────────

async function uploadFile(filePath) {
  const fileBytes = readFileSync(filePath);
  const fileName  = basename(filePath);

  log(c.cyan('[ 1/5 ]'), `上传文件: ${fileName}（${(fileBytes.length / 1024).toFixed(1)} KB）`);

  // 使用 curl 上传（Node.js fetch 对 tmpfiles.org 可能有 SSL/网络限制）
  let uploadOutput;
  try {
    uploadOutput = execSync(
      `curl -sf -F "file=@${filePath}" https://tmpfiles.org/api/v1/upload`,
      { encoding: 'utf-8', timeout: 60_000 },
    );
  } catch (err) {
    throw new Error(`curl 上传失败：${err.message}`);
  }

  log(c.yellow('  tmpfiles 响应'), uploadOutput.trim().slice(0, 200));

  const json = JSON.parse(uploadOutput.trim());
  const rawUrl = json.data?.url || '';
  const fileUrl = rawUrl.replace(
    /^https?:\/\/tmpfiles\.org\/(?:dl\/)?(\d+)\//,
    'https://tmpfiles.org/dl/$1/',
  );
  if (!fileUrl) throw new Error('上传成功但未返回 URL');

  log(c.green('  URL'), fileUrl);
  return fileUrl;
}

// ── Step 2: 创建 MinerU 任务 ──────────────────────────────────────────────────

async function createMineruTask(fileUrl) {
  log(c.cyan('[ 2/5 ]'), `提交 MinerU 任务`);

  const body = JSON.stringify({
    url: fileUrl,
    model_version: MODEL_VERSION,
    language: 'ch',
    features: ['formula', 'table'],
  });

  logJson('  请求体:', JSON.parse(body));

  let output;
  try {
    output = execSync(
      `curl -sf -X POST "${MINERU_CREATE_URL}" -H "Content-Type: application/json" -H "Authorization: Bearer ${MINERU_KEY}" -d '${body.replace(/'/g, "'\\''")}'`,
      { encoding: 'utf-8', timeout: 30_000 },
    );
  } catch (err) {
    throw new Error(`任务创建 curl 失败：${err.stderr || err.message}`);
  }

  log(c.yellow('  响应'), output.trim().slice(0, 300));
  const json = JSON.parse(output.trim());
  const taskId = json?.data?.task_id || json?.task_id || json?.id;
  if (!taskId) {
    logJson('  完整响应:', json);
    throw new Error('响应中未找到 task_id');
  }

  log(c.green('  task_id'), taskId);
  return taskId;
}

// ── Step 3: 轮询任务状态 ──────────────────────────────────────────────────────

async function pollMineruTask(taskId) {
  log(c.cyan('[ 3/5 ]'), `轮询任务状态（每 ${POLL_INTERVAL_MS/1000}s，最长 ${POLL_TIMEOUT_MS/1000}s）`);

  const pollUrl = `${MINERU_TASK_BASE}/${taskId}`;
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let count = 0;

  while (Date.now() < deadline) {
    await sleep(POLL_INTERVAL_MS);
    count += 1;
    const elapsed = Math.round((POLL_TIMEOUT_MS - (deadline - Date.now())) / 1000);

    let output;
    try {
      output = execSync(
        `curl -sf "${pollUrl}" -H "Authorization: Bearer ${MINERU_KEY}"`,
        { encoding: 'utf-8', timeout: 15_000 },
      );
    } catch (err) {
      log(c.yellow(`  #${count} (${elapsed}s)`), c.yellow(`curl 失败，跳过: ${err.message}`));
      continue;
    }

    const json = JSON.parse(output.trim());
    const state = String(json?.data?.state || json?.data?.status || json?.state || '').toLowerCase();
    const progress = json?.data?.extract_progress
      ? `(${json.data.extract_progress.extracted_pages}/${json.data.extract_progress.total_pages} 页)`
      : '';

    log(c.yellow(`  #${count} (${elapsed}s)`), `state="${state}" ${progress}`);

    if (['failed', 'error', 'aborted', 'cancelled'].includes(state)) {
      const errMsg = json?.data?.err_msg || json?.message || '未知';
      throw new Error(`MinerU 解析失败: ${errMsg}`);
    }

    if (['done', 'success', 'succeeded', 'completed', 'finished'].includes(state)) {
      const zipUrl = json?.data?.full_zip_url || '';
      log(c.green('  zip_url'), zipUrl);
      return zipUrl;
    }
  }

  throw new Error(`解析超时（${POLL_TIMEOUT_MS/1000}s，共轮询 ${count} 次）`);
}

// ── Step 4: 下载 zip，提取 markdown ──────────────────────────────────────────

async function extractMarkdownFromZip(zipUrl) {
  log(c.cyan('[ 4/5 ]'), `下载并解压 zip`);

  // 使用 curl 下载（避免 Node fetch 的 SSL 问题）
  let zipBuffer;
  try {
    zipBuffer = execSync(`curl -sf "${zipUrl}"`, { timeout: 60_000, maxBuffer: 100 * 1024 * 1024 });
  } catch (err) {
    throw new Error(`下载 zip 失败：${err.message}`);
  }

  log(c.yellow('  zip 大小'), `${(zipBuffer.length / 1024).toFixed(1)} KB`);

  const require = createRequire(import.meta.url);
  const JSZip = require('jszip');
  const zip = await JSZip.loadAsync(zipBuffer);

  const names = Object.keys(zip.files);
  log(c.dim('  zip 文件列表:'), names.join(', '));

  const mdFile = zip.file('full.md') || names.filter(n => n.endsWith('.md')).map(n => zip.file(n))[0];
  if (!mdFile) throw new Error('zip 中未找到 markdown 文件');

  const markdown = await mdFile.async('string');
  log(c.green('  markdown 长度'), `${markdown.length} 字符`);
  return markdown;
}

// ── Step 5: Kimi AI 分析（可选）──────────────────────────────────────────────

async function analyzeWithKimi(markdown) {
  if (!KIMI_KEY) {
    log(c.yellow('[ 5/5 ]'), '未设置 KIMI_API_KEY，跳过 AI 分析');
    return null;
  }

  log(c.cyan('[ 5/5 ]'), 'Kimi AI 分析 markdown…');

  const body = JSON.stringify({
    model: 'moonshot-v1-32k',
    temperature: 0,
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: '你是教育资料结构化助手。仅输出一个 JSON 对象，不要输出额外文本。' },
      {
        role: 'user',
        content: [
          '请基于以下 Markdown 内容，输出 JSON：',
          '{"title":"","subject":"","grade":"","materialType":"","standard":"","summary":"","tags":[]}',
          '要求：subject/grade/materialType/standard 尽量从文档推断；tags 返回 3-8 个中文标签。',
          `Markdown:\n${markdown.slice(0, 8000)}`,
        ].join('\n\n'),
      },
    ],
  });

  let output;
  try {
    output = execSync(
      `curl -sf -X POST "${KIMI_URL}" -H "Content-Type: application/json" -H "Authorization: Bearer ${KIMI_KEY}" -d @-`,
      { input: body, encoding: 'utf-8', timeout: 60_000 },
    );
  } catch (err) {
    throw new Error(`Kimi curl 失败：${err.stderr || err.message}`);
  }

  const json = JSON.parse(output.trim());
  const content = json.choices?.[0]?.message?.content?.trim() || '';
  if (!content) throw new Error('Kimi 响应为空');

  const parsed = JSON.parse(content);
  logJson('  AI 分析结果:', parsed);
  return parsed;
}

// ── 主流程 ────────────────────────────────────────────────────────────────────

async function main() {
  console.log(hr());
  log(c.bold('MinerU + Kimi AI 端到端测试'));
  console.log(hr());

  if (!MINERU_KEY) {
    console.error(c.red('✗ 缺少 MINERU_API_KEY 环境变量'));
    console.error('  用法: MINERU_API_KEY=<key> [KIMI_API_KEY=<key>] node server/test-mineru.mjs');
    process.exit(1);
  }

  if (!existsSync(TEST_FILE)) {
    console.error(c.red(`✗ 文件不存在: ${TEST_FILE}`));
    process.exit(1);
  }

  const fileUrl  = await uploadFile(TEST_FILE);
  const taskId   = await createMineruTask(fileUrl);
  const zipUrl   = await pollMineruTask(taskId);
  const markdown = await extractMarkdownFromZip(zipUrl);
  const aiResult = await analyzeWithKimi(markdown, basename(TEST_FILE));

  console.log('\n' + hr());
  log(c.green('[ DONE ]'), '全流程通过 ✓');
  console.log(hr());
  console.log('\n─── Markdown 前 800 字符 ───────────────────────────────────');
  console.log(markdown.slice(0, 800));
  console.log('────────────────────────────────────────────────────────────\n');

  if (aiResult) {
    console.log('─── AI 识别结果 ─────────────────────────────────────────────');
    console.log(JSON.stringify(aiResult, null, 2));
    console.log('────────────────────────────────────────────────────────────\n');
  }
}

main().catch((err) => {
  console.error(c.red('\n✗ 测试失败:'), err.message);
  process.exit(1);
});
