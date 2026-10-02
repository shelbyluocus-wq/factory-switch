// The desktop shell inlines this script into <head>, before <body> exists.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '..', 'ui', 'app.js'), 'utf8');
for (const [search, dataset] of [['', {}], ['?preview=1', {}], ['', {preview: 'true'}]]) {
  const events = [];
  const document = {
    body: null,
    documentElement: {dataset},
    readyState: 'loading',
    addEventListener: (...args) => events.push(args),
  };
  vm.runInNewContext(code, {document, location: {search}, URLSearchParams});
  assert.equal(events.length, 1);
  assert.equal(events[0][0], 'DOMContentLoaded');
  assert.equal(typeof events[0][1], 'function');
}
console.log('Desktop script starts safely before body parsing in all three modes.');
