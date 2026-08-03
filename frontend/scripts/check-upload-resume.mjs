import assert from 'node:assert/strict'
import fs from 'node:fs'

const assetView = fs.readFileSync(new URL('../src/views/PdfAssets.vue', import.meta.url), 'utf8')
const legacyView = fs.readFileSync(new URL('../src/views/Files.vue', import.meta.url), 'utf8')

for (const [name, source] of Object.entries({ PdfAssets: assetView, Files: legacyView })) {
  assert.match(source, /readUploadResumeContext\(\)/, `${name} must restore the interrupted upload context on mount`)
  assert.match(source, /saveUploadResumeContext\(.*0\)/, `${name} must persist context before upload starts`)
  assert.match(source, /SHA-256 去重复用/, `${name} must explain safe reselection and deduplication`)
  assert.match(source, /clearUploadResumeContext\(\)/, `${name} must clear context only after terminal success or dismissal`)
}

console.log('Interrupted upload recovery UI contract passed')
