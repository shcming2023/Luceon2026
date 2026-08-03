import assert from 'node:assert/strict'
import fs from 'node:fs'

const assetView = fs.readFileSync(new URL('../src/views/PdfAssets.vue', import.meta.url), 'utf8')
const legacyView = fs.readFileSync(new URL('../src/views/Files.vue', import.meta.url), 'utf8')
const materialTypes = fs.readFileSync(new URL('../src/types/material.ts', import.meta.url), 'utf8')

assert.match(materialTypes, /resource_gate\?:/)
assert.match(assetView, /超大 PDF 资源门禁通过/)
assert.match(assetView, /GPU 临时产物余量不足/)
assert.match(assetView, /提交已拦截/)
assert.match(legacyView, /超大PDF资源门禁：/)
assert.match(legacyView, /required_headroom_bytes/)

console.log('Large PDF GPU artifact headroom UI contract passed')
