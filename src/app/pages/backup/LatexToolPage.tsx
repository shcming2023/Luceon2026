import { useState, useRef } from 'react';
import { Upload, Download } from 'lucide-react';
import JSZip from 'jszip';

export function LatexToolPage() {
  const [logs, setLogs] = useState<string[]>([]);
  const [processing, setProcessing] = useState(false);
  const [resultBlob, setResultBlob] = useState<Blob | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const logsRef = useRef<HTMLPreElement>(null);

  const logMsg = (msg: string) => {
    setLogs((prev) => {
      const next = [...prev, msg];
      setTimeout(() => {
        if (logsRef.current) {
          logsRef.current.scrollTop = logsRef.current.scrollHeight;
        }
      }, 0);
      return next;
    });
  };

  const compressImage = (base64: string, maxSize: number): Promise<string> =>
    new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement('canvas');
        const scale = Math.min(1, Math.sqrt(maxSize / (img.width * img.height)));
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext('2d')!.drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(
          (blob) => {
            const reader = new FileReader();
            reader.onload = () => resolve((reader.result as string).split(',')[1]);
            reader.readAsDataURL(blob!);
          },
          'image/jpeg',
          0.85
        );
      };
      img.src = 'data:image/jpeg;base64,' + base64;
    });

  const handleProcess = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      alert('请先选择 LaTeX ZIP 文件');
      return;
    }

    setLogs([]);
    setResultBlob(null);
    setProcessing(true);

    const log = (msg: string) => logMsg(msg);

    try {
      log('正在解压 ZIP...');
      const zip = await JSZip.loadAsync(file);

      // ===== 阶段1：去冗余 =====
      log('========== 阶段1：去冗余 ==========');
      const texContents: { path: string; content: string }[] = [];
      for (const [path, entry] of Object.entries(zip.files)) {
        if (entry.dir) continue;
        if (/\.tex$/i.test(path)) {
          const content = await entry.async('string');
          texContents.push({ path, content });
          log(`解析: ${path}`);
        }
      }
      log(`共找到 ${texContents.length} 个 .tex 文件`);

      const referencedNames = new Set<string>();
      const includeRegex = /\\includegraphics(?:\[.*?\])?\{([^}]+)\}/g;
      for (const { content } of texContents) {
        let m: RegExpExecArray | null;
        const re = new RegExp(includeRegex.source, includeRegex.flags);
        while ((m = re.exec(content)) !== null) {
          const name = m[1].trim().replace(/^.*\//, '');
          referencedNames.add(name);
          referencedNames.add(name.replace(/\.[^.]+$/, ''));
        }
      }
      log(`共引用 ${referencedNames.size} 个图片文件`);

      const allImages: { path: string; entry: JSZip.JSZipObject }[] = [];
      let removedCount = 0;
      let imagesDirTotal = 0;
      for (const [path, entry] of Object.entries(zip.files)) {
        if (entry.dir) continue;
        const isImage = /\.(jpg|jpeg|png|gif|webp|bmp|tiff)$/i.test(path);
        const isInImagesDir = /(^|\/)images\//i.test(path);
        if (!isImage || !isInImagesDir) continue;

        imagesDirTotal++;
        const basename = path.replace(/^.*\//, '');
        const basenameNoExt = basename.replace(/\.[^.]+$/, '');
        const isReferenced = referencedNames.has(basename) || referencedNames.has(basenameNoExt);
        if (isReferenced) {
          allImages.push({ path, entry });
        } else {
          zip.remove(path);
          removedCount++;
          log(`[删除未引用][images目录] ${path}`);
        }
      }
      log(`阶段1完成：images目录共 ${imagesDirTotal} 张，删除 ${removedCount} 张，保留 ${allImages.length} 张\n`);

      // ===== 阶段2：压缩超大图片 =====
      log('========== 阶段2：压缩 images 目录大图 ==========');
      const ONE_MB = 1048576;
      let compressedCount = 0;
      for (const img of allImages) {
        const rawData = await img.entry.async('uint8array');
        const sizeKB = (rawData.length / 1024).toFixed(0);
        const sizeMB = (rawData.length / 1024 / 1024).toFixed(2);
        if (rawData.length > ONE_MB) {
          const base64 = await img.entry.async('base64');
          const compressed = await compressImage(base64, ONE_MB);
          zip.file(img.path, compressed, { base64: true });
          compressedCount++;
          log(`[压缩] ${img.path} (${sizeMB}MB → ≤1MB)`);
        } else {
          log(`[跳过] ${img.path} (${sizeKB}KB, 无需压缩)`);
        }
      }
      log(`阶段2完成：压缩 ${compressedCount} 张超大图片\n`);

      log('正在重新打包 ZIP...');
      const blob = await zip.generateAsync({ type: 'blob' });
      setResultBlob(blob);
      log('✅ 优化完成！可以下载了。');
    } catch (err: unknown) {
      log('❌ 错误: ' + (err instanceof Error ? err.message : String(err)));
    } finally {
      setProcessing(false);
    }
  };

  const handleDownload = () => {
    if (!resultBlob) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(resultBlob);
    a.download = 'latex-optimized.zip';
    a.click();
  };

  return (
    <div className="p-6 space-y-5 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">LaTeX 工具</h1>
        <p className="text-sm text-gray-500 mt-0.5">ZIP 压缩包去冗余 + 大图压缩（全部在浏览器本地处理）</p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h2 className="font-semibold text-gray-800">LaTeX 压缩包优化（去冗余 + 压缩）</h2>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">上传 LaTeX 项目 ZIP 包</label>
          <input
            ref={fileRef}
            type="file"
            accept=".zip"
            className="block w-full text-sm text-gray-500 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
          />
        </div>

        <div className="flex gap-3">
          <button
            onClick={handleProcess}
            disabled={processing}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <Upload size={15} /> 开始处理（先去冗余，再压缩 &gt;1MB 图片）
          </button>
          {resultBlob && (
            <button
              onClick={handleDownload}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
            >
              <Download size={15} /> 下载优化后的 ZIP
            </button>
          )}
        </div>

        {logs.length > 0 && (
          <pre
            ref={logsRef}
            className="bg-gray-900 text-gray-200 rounded-lg p-3 text-xs font-mono max-h-72 overflow-y-auto whitespace-pre-wrap"
          >
            {logs.join('\n')}
          </pre>
        )}
      </div>

      <div className="bg-blue-50 rounded-xl border border-blue-100 p-4 text-sm text-blue-700">
        <strong>说明：</strong>处理过程完全在浏览器本地执行，不上传任何文件到服务器。
        仅删除 <code className="bg-blue-100 px-1 rounded">images/</code> 目录下未被 <code className="bg-blue-100 px-1 rounded">\includegraphics</code> 引用的图片，其他目录保持不变。
      </div>
    </div>
  );
}
