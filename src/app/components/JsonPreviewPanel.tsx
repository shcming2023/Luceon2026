import { useEffect, useState } from 'react';
 
type ListedObject = { name: string; presignedUrl?: string };
 
function pickJson(objects: ListedObject[]) {
  const names = objects.map((o) => o.name);
  const prefer = ['content_list.json', 'middle.json', 'full.json'];
  for (const p of prefer) {
    const i = names.findIndex((n) => n === p);
    if (i >= 0) return objects[i];
  }
  return objects.find((o) => o.name.endsWith('.json')) ?? null;
}
 
export function JsonPreviewPanel({ materialId }: { materialId: string | number }) {
  const [loading, setLoading] = useState(true);
  const [content, setContent] = useState<string>('');
  const [error, setError] = useState('');
 
  useEffect(() => {
    let cancelled = false;
    const ac = new AbortController();
    setLoading(true);
    setError('');
    setContent('');
 
    (async () => {
      try {
        const prefix = `parsed/${materialId}/`;
        const listRes = await fetch(`/__proxy/upload/list?prefix=${encodeURIComponent(prefix)}`, { cache: 'no-store', signal: ac.signal });
        if (!listRes.ok) throw new Error(`HTTP ${listRes.status}`);
        const listData = await listRes.json();
        const objects: ListedObject[] = Array.isArray(listData?.objects) ? listData.objects : [];
        const jsonFile = pickJson(objects);
        if (!jsonFile?.presignedUrl) {
          setContent('暂无 JSON 产物');
          return;
        }
        const r = await fetch(jsonFile.presignedUrl, { cache: 'no-store', signal: ac.signal });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const text = await r.text();
        try {
          setContent(JSON.stringify(JSON.parse(text), null, 2));
        } catch {
          setContent(text);
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
 
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [materialId]);
 
  if (loading) return <div className="flex items-center justify-center h-full text-gray-400 text-xs">加载中...</div>;
  if (error) return <div className="p-4 text-red-500 text-xs">{error}</div>;
  return (
    <pre className="p-4 text-[11px] font-mono text-gray-700 overflow-auto h-full whitespace-pre-wrap bg-gray-50">
      {content}
    </pre>
  );
}
 
