import assert from 'node:assert';
import JSZip from 'jszip';
import { parsedZipHandler, _testHooks } from '../upload-server.mjs';
import { Writable } from 'node:stream';

async function runTest() {
  console.log('--- Parsed ZIP Export Modes Smoke Test (Real) ---');

  // 1. Create a fake memory MinerU ZIP
  const fakeMineruZip = new JSZip();
  fakeMineruZip.file('auto/full.md', Buffer.from('# I am inside zip', 'utf-8'));
  fakeMineruZip.file('images/1.jpg', Buffer.from('fake image', 'utf-8'));
  fakeMineruZip.file('mineru-result.json', Buffer.from('{"ok":true}', 'utf-8'));
  const fakeZipBuffer = await fakeMineruZip.generateAsync({ type: 'nodebuffer' });

  // 2. Mock fake MinIO list and objects
  _testHooks.mockListAllObjects = async (bucket, prefix) => {
    const list = [
      { name: `${prefix}mineru-result.zip`, size: fakeZipBuffer.length },
      { name: `${prefix}full.md`, size: 100 },
      { name: `${prefix}images/1.jpg`, size: 10 }, // legacy expanded duplicate
      { name: `${prefix}images/2.jpg`, size: 10 }  // legacy expanded unique
    ];
    if (!prefix.includes('test-mat-fallback') && !prefix.includes('test-mat-dedup')) {
      list.push({ name: `${prefix}artifact-manifest.json`, size: 200 });
    }
    return list;
  };

  const fakeManifest = {
    version: 'artifact-manifest.v0.1',
    artifactStorageMode: 'legacy-mixed',
    primaryMarkdownPath: 'auto/full.md',
    artifacts: []
  };

  const mockMinioClient = {
    getObject: async (bucket, name) => {
      const stream = new (await import('stream')).PassThrough();
      if (name.endsWith('mineru-result.zip')) {
        stream.end(fakeZipBuffer);
      } else if (name.endsWith('artifact-manifest.json')) {
        if (name.includes('test-mat-fallback') || name.includes('test-mat-dedup')) {
          stream.emit('error', new Error('Not found'));
          return stream;
        }
        stream.end(Buffer.from(JSON.stringify(fakeManifest)));
      } else if (name.endsWith('full.md')) {
        stream.end(Buffer.from('# I am inside zip', 'utf-8'));
      } else {
        stream.end(Buffer.from('fake content for ' + name));
      }
      return stream;
    }
  };
  _testHooks.setMockMinioClient(mockMinioClient);

  // Helper to run handler and capture zip output
  async function runHandlerWithMode(mode, matId = 'test-mat-1') {
    const req = { body: { materialId: matId, mode } };
    let statusCode = 200;
    let jsonBody = null;
    let headers = {};
    const chunks = [];
    let isEnded = false;
    
    return new Promise((resolve) => {
      const res = {
        status: (code) => { statusCode = code; return res; },
        json: (body) => { jsonBody = body; resolve({ status: statusCode, body: jsonBody }); },
        setHeader: (k, v) => { headers[k] = v; },
        write: (chunk) => { chunks.push(chunk); },
        end: (chunk) => { if (chunk) chunks.push(chunk); resolve({ status: statusCode, headers, buffer: Buffer.concat(chunks) }); },
        on: (event, handler) => { 
           // mock stream events
           if (event === 'drain' || event === 'error' || event === 'finish' || event === 'close') {
             // simplified
           }
           return res;
        },
        once: () => res,
        emit: () => {},
        removeListener: () => {}
      };
      // Node Streams `.pipe(res)` expects a Writable stream
      // We will proxy chunks pushed by JSZip
      const proxyStream = new Writable({
        write(chunk, encoding, callback) {
          chunks.push(chunk);
          callback();
        },
        final(callback) {
          resolve({ status: statusCode, headers, buffer: Buffer.concat(chunks) });
          callback();
        }
      });
      proxyStream.setHeader = res.setHeader;
      proxyStream.status = res.status;
      proxyStream.json = res.json;
      
      parsedZipHandler(req, proxyStream).catch(e => resolve({ status: 500, error: e.message }));
    });
  }

  console.log('Testing invalid mode...');
  const resInvalid = await runHandlerWithMode('invalid-mode');
  assert.equal(resInvalid.status, 400);
  assert.ok(resInvalid.body.error.includes('非法的导出模式'));
  console.log('✅ Invalid mode rejected');

  console.log('Testing user mode...');
  const resUser = await runHandlerWithMode('user');
  assert.equal(resUser.status, 200);
  assert.equal(resUser.headers['Content-Type'], 'application/zip');
  
  const userZip = await JSZip.loadAsync(resUser.buffer);
  const userFiles = Object.keys(userZip.files).filter(k => !userZip.files[k].dir);
  // mineru-result.zip should NOT be present
  assert.ok(!userFiles.includes('mineru-result.zip'));
  // primary md inside zip is filtered, but MinIO full.md is kept
  assert.ok(userFiles.includes('full.md'));
  assert.ok(userFiles.includes('images/1.jpg'));
  assert.ok(userFiles.includes('images/2.jpg'));
  
  const filesCount = Number(resUser.headers['X-Parsed-Files-Count']);
  assert.equal(filesCount, userFiles.length, 'X-Parsed-Files-Count header match');
  console.log('✅ mode=user deduped correctly and avoided duplicating full.md');

  console.log('Testing mineru-raw mode...');
  const resRaw = await runHandlerWithMode('mineru-raw');
  assert.equal(resRaw.status, 200);
  // raw directly returns zip
  const rawZip = await JSZip.loadAsync(resRaw.buffer);
  assert.ok(Object.keys(rawZip.files).includes('images/1.jpg'));
  assert.ok(!Object.keys(rawZip.files).includes('images/2.jpg')); // 2.jpg is outside
  console.log('✅ mode=mineru-raw streamed correctly');

  console.log('Testing diagnostic mode...');
  const resDiag = await runHandlerWithMode('diagnostic');
  const diagZip = await JSZip.loadAsync(resDiag.buffer);
  const diagFiles = Object.keys(diagZip.files);
  assert.ok(diagFiles.includes('mineru-result.zip'), 'diagnostic should include raw zip');
  assert.ok(diagFiles.includes('full.md'), 'diagnostic includes extracted');
  console.log('✅ mode=diagnostic included all files');

  console.log('Testing manifest fallback...');
  const resFallback = await runHandlerWithMode('user', 'test-mat-fallback');
  assert.equal(resFallback.status, 200);
  console.log('✅ Fallback completed without ReferenceError');

  console.log('Testing dynamic deduplication for legacy data...');
  const resDedup = await runHandlerWithMode('user', 'test-mat-dedup');
  assert.equal(resDedup.status, 200);
  const dedupZip = await JSZip.loadAsync(resDedup.buffer);
  const dedupFiles = Object.keys(dedupZip.files).filter(k => !dedupZip.files[k].dir);
  assert.ok(dedupFiles.includes('full.md'), 'root full.md should be kept');
  assert.ok(!dedupFiles.includes('auto/full.md'), 'inner zip primary should be deduped');
  console.log('✅ Dynamic deduplication completed');

  console.log('Pass ✅');
  process.exit(0);
}

runTest().catch(e => {
  console.error(e);
  process.exit(1);
});
