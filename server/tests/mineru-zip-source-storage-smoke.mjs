import assert from 'node:assert';
import JSZip from 'jszip';
import { processWithLocalMinerU } from '../services/mineru/local-adapter.mjs';

async function runTest() {
  console.log('--- MinerU Zip Source Storage Smoke Test (Real) ---');

  const fakeMineruZip = new JSZip();
  fakeMineruZip.file('auto/full.md', Buffer.from('# I am inside zip', 'utf-8'));
  fakeMineruZip.file('images/1.jpg', Buffer.from('fake image', 'utf-8'));
  fakeMineruZip.file('mineru-result.json', Buffer.from('{"ok":true}', 'utf-8'));
  const fakeZipBuffer = await fakeMineruZip.generateAsync({ type: 'nodebuffer' });

  // Mock globalThis.fetch to simulate MinerU health check and upload responses
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const u = url.toString();
    if (u.includes('/health')) return { ok: true, json: async () => ({ status: 'healthy' }) };
    if (u.includes('/submit')) return { ok: true, json: async () => ({ task_id: 'mineru-123' }) };
    if (u.includes('/status')) return { ok: true, json: async () => ({ status: 'done' }) };
    if (u.includes('/result')) {
      return { 
        ok: true, 
        headers: new Headers({ 'content-type': 'application/zip' }),
        arrayBuffer: async () => fakeZipBuffer.buffer.slice(fakeZipBuffer.byteOffset, fakeZipBuffer.byteOffset + fakeZipBuffer.byteLength)
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  let savedObjects = [];
  const mockMinioContext = {
    saveObject: async (objName, buf) => {
       savedObjects.push(objName);
       assert.ok(!objName.includes('images/1.jpg'), 'Inner objects should NOT be saved to MinIO directly');
       return true;
    },
    saveMarkdown: async (objName, text) => {
       savedObjects.push(objName);
       assert.equal(text, '# I am inside zip', 'Extracted markdown correct');
       return true;
    },
    getFileStream: async () => {
      const stream = new (await import('stream')).PassThrough();
      stream.end(Buffer.from('fake pdf'));
      return stream;
    }
  };

  const mockTask = {
    id: 'test-task',
    materialId: 'test-mat-1',
    optionsSnapshot: { localEndpoint: 'http://fake-mineru:8080', responseFormatZip: true },
    material: {
       fileName: 'test.pdf',
       mimeType: 'application/pdf',
       metadata: { objectName: 'originals/test.pdf' }
    }
  };

  let progressCalled = false;
  const updateProgress = async () => { progressCalled = true; };

  try {
    const result = await processWithLocalMinerU({ 
      task: mockTask, 
      material: mockTask.material, 
      mineruTaskId: 'test-mineru-task',
      minioContext: mockMinioContext, 
      timeoutMs: 1000, 
      updateProgress 
    });

    assert.equal(result.artifactStorageMode, 'zip-source');
    assert.deepEqual(result.artifactExportModes, ['user', 'mineru-raw', 'diagnostic']);
    assert.equal(result.primaryMarkdownPath, 'auto/full.md');

    // Check parsedArtifacts
    const zipEntries = result.parsedArtifacts.filter(a => a.source === 'zip-entry');
    assert.ok(zipEntries.length > 0, 'Should have zip entries for images and json');
    
    const imgEntry = zipEntries.find(a => a.relativePath === 'images/1.jpg');
    assert.ok(imgEntry, 'Has image entry');
    assert.equal(imgEntry.zipObjectName, 'parsed/test-mat-1/mineru-result.zip');
    assert.ok(imgEntry.size > 0, 'Size is populated');
    assert.equal(imgEntry.objectName, undefined, 'zip entries should NOT have objectName');

    assert.ok(savedObjects.includes('parsed/test-mat-1/mineru-result.zip'));
    assert.ok(savedObjects.includes('parsed/test-mat-1/full.md'));

    console.log('✅ processWithLocalMinerU returns zip-source mode and zip-entry artifacts correctly');
    console.log('Pass ✅');
  } finally {
    globalThis.fetch = originalFetch;
  }
}

runTest().catch(e => {
  console.error(e);
  process.exit(1);
});
