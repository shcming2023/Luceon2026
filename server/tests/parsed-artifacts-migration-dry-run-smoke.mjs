import assert from 'node:assert';
import JSZip from 'jszip';
import { dryRunHandler, _testHooks } from '../upload-server.mjs';

async function runTest() {
  console.log('--- Parsed Artifacts Migration Dry-Run Smoke Test (Real) ---');
  
  const fakeMineruZip = new JSZip();
  fakeMineruZip.file('full.md', Buffer.from('# I am inside zip', 'utf-8'));
  fakeMineruZip.file('images/1.jpg', Buffer.from('fake image 1', 'utf-8'));
  fakeMineruZip.file('images/2.jpg', Buffer.from('fake image 2', 'utf-8'));
  const fakeZipBuffer = await fakeMineruZip.generateAsync({ type: 'nodebuffer' });

  // Mock fake MinIO list and objects
  _testHooks.mockListAllObjects = async (bucket, prefix) => {
    if (prefix.includes('mat-missing')) return [];
    if (prefix.includes('mat-zip-source')) {
      return [
        { name: `${prefix}mineru-result.zip`, size: fakeZipBuffer.length },
        { name: `${prefix}full.md`, size: 100 },
        { name: `${prefix}artifact-manifest.json`, size: 200 }
      ];
    }
    if (prefix.includes('mat-legacy-safe')) {
      return [
        { name: `${prefix}mineru-result.zip`, size: fakeZipBuffer.length },
        { name: `${prefix}full.md`, size: 100 },
        { name: `${prefix}artifact-manifest.json`, size: 200 },
        { name: `${prefix}images/1.jpg`, size: 12 }, 
        { name: `${prefix}images/2.jpg`, size: 12 }  
      ];
    }
    if (prefix.includes('mat-legacy-unsafe')) {
      return [
        { name: `${prefix}mineru-result.zip`, size: fakeZipBuffer.length },
        { name: `${prefix}full.md`, size: 100 },
        { name: `${prefix}images/missing-in-zip.jpg`, size: 12 } // Not in zip!
      ];
    }
    return [];
  };

  const mockMinioClient = {
    getObject: async (bucket, name) => {
      const stream = new (await import('stream')).PassThrough();
      if (name.endsWith('mineru-result.zip')) {
        stream.end(fakeZipBuffer);
      } else {
        stream.end(Buffer.from('fake content'));
      }
      return stream;
    }
  };
  _testHooks.setMockMinioClient(mockMinioClient);

  async function callDryRun(materialIds) {
    const req = { body: { materialIds } };
    let statusCode = 200;
    let jsonBody = null;
    return new Promise((resolve) => {
      const res = {
        status: (code) => { statusCode = code; return res; },
        json: (body) => { jsonBody = body; resolve({ status: statusCode, body: jsonBody }); },
        setHeader: () => {}
      };
      dryRunHandler(req, res).catch(e => resolve({ status: 500, error: e.message }));
    });
  }

  const matIds = ['mat-missing', 'mat-zip-source', 'mat-legacy-safe', 'mat-legacy-unsafe'];
  const res = await callDryRun(matIds);

  assert.equal(res.status, 200);
  const data = res.body;

  assert.equal(data.summary.materialsScanned, 4);
  assert.equal(data.summary.legacyMixed, 2);
  assert.equal(data.summary.zipSource, 1);
  assert.equal(data.summary.expandedOnly, 0);

  const missing = data.items.find(i => i.materialId === 'mat-missing');
  assert.equal(missing.status, 'missing-artifacts');
  assert.equal(missing.safeToMigrate, false);

  const zs = data.items.find(i => i.materialId === 'mat-zip-source');
  assert.equal(zs.status, 'zip-source');

  const leg = data.items.find(i => i.materialId === 'mat-legacy-safe');
  assert.equal(leg.status, 'legacy-mixed');
  assert.equal(leg.safeToMigrate, true);
  assert.equal(leg.expandedObjectCount, 2);
  assert.equal(leg.zipEntryCount, 3); // full.md, 1.jpg, 2.jpg
  assert.equal(leg.candidateRemovableObjectsCount, 2);

  const unsafe = data.items.find(i => i.materialId === 'mat-legacy-unsafe');
  assert.equal(unsafe.status, 'legacy-mixed');
  assert.equal(unsafe.safeToMigrate, false);
  assert.ok(unsafe.unsafeReasons[0].includes('missing in zip'));

  console.log('✅ dry-run correctly performs real zip check and safe guard');
  console.log('Pass ✅');
  process.exit(0);
}

runTest().catch(e => {
  console.error(e);
  process.exit(1);
});
