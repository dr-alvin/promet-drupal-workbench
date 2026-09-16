'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const evidence = require('../engine_scripts/evidence');

function workdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'evidence-'));
}

test('every record survives many concurrent writer processes', () => {
  // The regression this guards: with asyncCaptureLimit > 1, capture scenarios
  // run as separate processes appending to one shared .jsonl, and records were
  // silently lost. A lost valid-capture reads as a failed scenario.
  const work = workdir();
  const total = 60;
  const script = path.join(work, 'writer.js');
  fs.writeFileSync(
    script,
    `const e=require(${JSON.stringify(path.resolve(__dirname, '../engine_scripts/evidence.js'))});
     e.record(process.argv[2],'valid-captures',{id:process.argv[3],status:'passed'});`
  );
  const children = [];
  for (let i = 0; i < total; i++) {
    children.push(new Promise(resolve => {
      const { spawn } = require('child_process');
      const c = spawn(process.execPath, [script, work, `scenario-${i}`], { stdio: 'ignore' });
      c.on('exit', resolve);
    }));
  }
  return Promise.all(children).then(() => {
    const records = evidence.read(work, 'valid-captures');
    const ids = new Set(records.map(r => r.id));
    assert.strictEqual(ids.size, total, `lost records: got ${ids.size} of ${total}`);
    for (let i = 0; i < total; i++) assert.ok(ids.has(`scenario-${i}`), `missing scenario-${i}`);
    fs.rmSync(work, { recursive: true, force: true });
  });
});

test('the per-record directory is authoritative and is not double counted', () => {
  const work = workdir();
  evidence.record(work, 'valid-captures', { id: 'a', status: 'passed' });
  evidence.record(work, 'valid-captures', { id: 'b', status: 'passed' });
  // Both the directory and the legacy mirror exist; reading must not see four.
  assert.ok(fs.existsSync(path.join(work, 'valid-captures.jsonl')));
  assert.strictEqual(evidence.read(work, 'valid-captures').length, 2);
  fs.rmSync(work, { recursive: true, force: true });
});

test('repeated failures for one scenario are all preserved', () => {
  const work = workdir();
  evidence.record(work, 'functional-failures', { id: 'x', message: 'readiness' });
  evidence.record(work, 'functional-failures', { id: 'x', message: 'capture' });
  const messages = evidence.read(work, 'functional-failures').map(r => r.message).sort();
  assert.deepStrictEqual(messages, ['capture', 'readiness']);
  fs.rmSync(work, { recursive: true, force: true });
});

test('falls back to the legacy jsonl when no directory exists', () => {
  const work = workdir();
  fs.writeFileSync(
    path.join(work, 'valid-captures.jsonl'),
    '{"id":"old-1","status":"passed"}\n{"id":"old-2","status":"passed"}\n'
  );
  assert.deepStrictEqual(evidence.read(work, 'valid-captures').map(r => r.id), ['old-1', 'old-2']);
  fs.rmSync(work, { recursive: true, force: true });
});

test('reset clears both the directory and the legacy mirror', () => {
  const work = workdir();
  evidence.record(work, 'valid-captures', { id: 'a', status: 'passed' });
  evidence.record(work, 'image-coverage', { id: 'a', ready: true });
  evidence.reset(work);
  assert.strictEqual(evidence.read(work, 'valid-captures').length, 0);
  assert.strictEqual(evidence.read(work, 'image-coverage').length, 0);
  assert.ok(!fs.existsSync(path.join(work, 'valid-captures.jsonl')));
  fs.rmSync(work, { recursive: true, force: true });
});

test('scenario ids cannot escape the evidence directory', () => {
  const work = workdir();
  evidence.record(work, 'valid-captures', { id: '../../escape', status: 'passed' });
  const dir = path.join(work, 'evidence', 'valid-captures');
  const names = fs.readdirSync(dir);
  assert.strictEqual(names.length, 1);
  assert.ok(!names[0].includes('/'), `unsafe filename: ${names[0]}`);
  assert.ok(!fs.existsSync(path.join(work, '..', 'escape.json')));
  fs.rmSync(work, { recursive: true, force: true });
});

test('a malformed legacy line does not discard the rest', () => {
  const work = workdir();
  fs.writeFileSync(path.join(work, 'cleanup.jsonl'), '{"id":"a"}\nnot-json\n{"id":"b"}\n');
  assert.deepStrictEqual(evidence.read(work, 'cleanup').map(r => r.id), ['a', 'b']);
  fs.rmSync(work, { recursive: true, force: true });
});
