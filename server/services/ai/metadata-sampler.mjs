import crypto from 'crypto';

/**
 * 抽样 Markdown 内容，组合多个部分，并生成 Hash
 * @param {string} markdownContent - 原始 Markdown 内容
 * @param {object} metadata - ParseTask 的元数据或附加信息，包含 filename, objectName, parsedPrefix, parsedFilesCount 等
 * @param {number} maxChars - 最大允许字符数，默认 80000
 * @returns {{ sampledContent: string, inputHash: string }}
 */
export function sampleMarkdown(markdownContent, metadata = {}, maxChars = 80000) {
  if (!markdownContent) {
    return { sampledContent: '', inputHash: '' };
  }

  // 预留元数据头部的空间
  const headerContent = `
=== FILE METADATA ===
Filename: ${metadata.filename || metadata.inputMarkdownObjectName || 'unknown'}
Parsed Prefix: ${metadata.parsedPrefix || 'unknown'}
Parsed Files Count: ${metadata.parsedFilesCount || 0}
=== END METADATA ===
`.trim() + '\n\n';

  const remainingChars = maxChars - headerContent.length - 150;
  if (remainingChars <= 0) {
    // 极端情况
    return generateResult(headerContent);
  }

  // 抽样策略：
  // Head: 20%
  // Tail: 10%
  // TOC: 10%
  // Middle: 剩余部分 (大约 60%)

  const totalLength = markdownContent.length;
  
  if (totalLength <= remainingChars) {
    // 如果全文长度小于限制，直接返回
    return generateResult(headerContent + markdownContent);
  }

  const headSize = Math.floor(remainingChars * 0.2);
  const tailSize = Math.floor(remainingChars * 0.1);
  const tocSize = Math.floor(remainingChars * 0.1);
  const middleSize = remainingChars - headSize - tailSize - tocSize;

  const headContent = markdownContent.substring(0, headSize);
  const tailContent = markdownContent.substring(totalLength - tailSize);

  // 寻找 TOC (目录) 关键词附近
  let tocContent = '';
  const tocMatch = markdownContent.match(/(目录|TOC|Table of Contents|CONTENTS)/i);
  if (tocMatch) {
    const tocIndex = tocMatch.index;
    const start = Math.max(0, tocIndex - Math.floor(tocSize / 4));
    const end = Math.min(totalLength, tocIndex + Math.floor(tocSize * 0.75));
    tocContent = markdownContent.substring(start, end);
  } else {
    // 如果没有找到 TOC，从约 10% 处截取
    const start = Math.floor(totalLength * 0.1);
    tocContent = markdownContent.substring(start, Math.min(totalLength, start + tocSize));
  }

  // 截取中间部分
  const middleStart = Math.floor(totalLength / 2 - middleSize / 2);
  const middleContent = markdownContent.substring(middleStart, Math.min(totalLength, middleStart + middleSize));

  const sampledContent = [
    headerContent,
    "=== HEAD ===",
    headContent,
    "\n=== TOC/NEAR HEAD ===",
    tocContent,
    "\n=== MIDDLE ===",
    middleContent,
    "\n=== TAIL ===",
    tailContent,
    "\n=== ARTIFACT SIGNAL ===\n[sampled representation]"
  ].join('\n');

  return generateResult(sampledContent);
}

function generateResult(content) {
  const hash = crypto.createHash('sha256').update(content).digest('hex');
  return {
    sampledContent: content,
    inputHash: `sha256:${hash}`
  };
}
