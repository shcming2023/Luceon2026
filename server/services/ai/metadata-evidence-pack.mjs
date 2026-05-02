import crypto from 'node:crypto';

export function buildEvidencePack(markdownContent, sourceMeta = {}, options = {}) {
  const originalLength = markdownContent.length;
  const filename = sourceMeta.filename || sourceMeta.raw_object_name || '';
  
  // 1. Filename Signals
  const filenameSignals = extractFilenameSignals(filename);
  
  // 2. Headings Outline
  const headingOutline = extractHeadings(markdownContent, 120);
  
  // 3. Representative Snippets
  const snippets = extractSnippets(markdownContent);
  
  // 4. Tail Signals
  const tailSignals = extractTailSignals(markdownContent);
  
  // 5. Title / Front Matter
  const frontMatter = markdownContent.slice(0, 3000);
  
  // 6. TOC
  const toc = extractTOC(markdownContent) || 'not_found';
  
  // Assemble Evidence Pack
  let packContent = `=== SOURCE FACTS ===
file_name: ${filename}
file_size: ${sourceMeta.fileSize || 0}
mime_type: ${sourceMeta.mimeType || ''}
parsed_files_count: ${sourceMeta.parsedFilesCount || 0}
raw_object_name: ${sourceMeta.rawObjectName || ''}
markdown_object_name: ${sourceMeta.markdownObjectName || ''}

=== FILENAME SIGNALS ===
${filenameSignals.join(' | ')}

=== DOCUMENT SHAPE ===
markdown_chars: ${originalLength}
estimated_pages: ${sourceMeta.parsedFilesCount || 0}
large_document: ${originalLength > 150000 || (sourceMeta.parsedFilesCount || 0) > 1000}
heading_count: ${headingOutline.length}
toc_detected: ${toc !== 'not_found'}

=== TITLE / FRONT MATTER ===
${frontMatter}

=== TOC / CONTENTS ===
${toc}

=== HEADING OUTLINE ===
${headingOutline.join('\n')}

=== REPRESENTATIVE BODY SNIPPETS ===
--- EARLY BODY ---
${snippets.early}

--- MIDDLE CHAPTER ---
${snippets.middle}

--- LATE CHAPTER ---
${snippets.late}

=== TAIL / APPENDIX / ANSWER SIGNALS ===
${tailSignals}

=== EVIDENCE CANDIDATES ===
${filenameSignals.join(' | ')}
`;

  // trim if needed, but normally this is around 10k - 20k chars
  if (packContent.length > 30000) {
    packContent = packContent.slice(0, 30000) + '\\n...[Evidence Pack Truncated]';
  }

  const hash = crypto.createHash('sha256').update(packContent).digest('hex');

  return {
    content: packContent,
    inputHash: `sha256:${hash}`,
    mode: 'evidence-pack-v0.3',
    originalLength,
    sampledLength: packContent.length,
    sections: {
      sourceFacts: true,
      filenameSignals: true,
      documentShape: true,
      frontMatter: true,
      toc: true,
      headingOutline: true,
      representativeBody: true,
      tailSignals: true,
      evidenceCandidates: true
    }
  };
}

function extractFilenameSignals(filename) {
  if (!filename) return [];
  // simple extraction, mostly split by separators and look for known patterns
  const clean = filename.replace(/\.(pdf|docx?|zip|pptx?|epub)$/i, '');
  const tokens = clean.split(/[_\-()[\]\s]+/);
  return tokens.filter(t => t.length > 2);
}

function extractHeadings(md, maxCount) {
  const lines = md.split('\n');
  const headings = [];
  const headingRegex = /^(#{1,6}|\d+\.)\s+(.+)/;
  for (const line of lines) {
    if (headingRegex.test(line.trim())) {
      headings.push(line.trim().slice(0, 200));
      if (headings.length >= maxCount) break;
    }
  }
  return headings;
}

function extractSnippets(md) {
  const length = md.length;
  
  const getSnippet = (start, len) => {
    if (start >= length) return '';
    return md.slice(start, Math.min(start + len, length)).trim();
  };

  return {
    early: getSnippet(Math.floor(length * 0.1), 1500),
    middle: getSnippet(Math.floor(length * 0.5), 1500),
    late: getSnippet(Math.floor(length * 0.8), 1500)
  };
}

function extractTailSignals(md) {
  if (md.length < 5000) return md.slice(-1000);
  return md.slice(-3000);
}

function extractTOC(md) {
  const tocRegex = /((?:contents|table of contents|目录)[\s\S]{100,2000})/i;
  const match = md.match(tocRegex);
  if (match) return match[1].slice(0, 2000);
  return 'not_found';
}
