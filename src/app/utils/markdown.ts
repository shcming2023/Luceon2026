export function renderMarkdown(md: string): string {
  let html = md
    .replace(/```[\w]*\n?([\s\S]*?)```/g, (_, code) =>
      `<pre class="bg-gray-100 rounded p-3 text-xs overflow-auto my-2 font-mono whitespace-pre-wrap"><code>${String(code).replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`)
    .replace(/`([^`]+)`/g, '<code class="bg-gray-100 rounded px-1 py-0.5 text-xs font-mono text-red-600">$1</code>')
    .replace(/^### (.+)$/gm, '<h3 class="text-sm font-bold text-gray-800 mt-4 mb-1">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-base font-bold text-gray-800 mt-5 mb-2 border-b border-gray-200 pb-1">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold text-gray-900 mt-6 mb-2 border-b-2 border-gray-300 pb-1">$1</h1>')
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-gray-900">$1</strong>')
    .replace(/\*(.+?)\*/g, '<em class="italic">$1</em>')
    .replace(/^\|(.+)\|$/gm, (line) => {
      if (/^\|[\s\-:|]+\|$/.test(line)) return '';
      const cells = line.split('|').filter((_, i, a) => i > 0 && i < a.length - 1);
      const tds = cells.map((c) => `<td class="border border-gray-200 px-2 py-1 text-xs">${c.trim()}</td>`).join('');
      return `<tr>${tds}</tr>`;
    })
    .replace(/((<tr>.*<\/tr>\n?)+)/g, '<table class="border-collapse w-full my-3 text-xs">$1</table>')
    .replace(/^[-*+] (.+)$/gm, '<li class="ml-4 list-disc text-xs text-gray-700">$1</li>')
    .replace(/(<li.*<\/li>\n?)+/g, (m) => `<ul class="my-2 space-y-0.5">${m}</ul>`)
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-xs text-gray-700">$1</li>')
    .replace(/^---+$/gm, '<hr class="my-4 border-gray-200" />')
    .replace(/\n{2,}/g, '</p><p class="text-xs text-gray-700 leading-relaxed my-1">')
    .replace(/\n/g, '<br />');
  return `<p class="text-xs text-gray-700 leading-relaxed">${html}</p>`;
}

