'use strict';
// Race-free capture evidence.
//
// Capture scenarios run as concurrent processes that all share the bind-mounted
// work directory. Appending to one .jsonl from several processes relies on
// cross-process O_APPEND atomicity, which does not hold on the Docker Desktop
// bind mounts this toolkit runs on: raising asyncCaptureLimit above 1 silently
// dropped records, and a lost valid-capture line reads as a capture failure for
// a scenario that actually succeeded.
//
// Each record is therefore written to its own file, which cannot interleave. The
// legacy .jsonl is still appended so existing consumers and report packaging
// keep working, but the directory is authoritative whenever it exists.
const fs = require('fs');
const path = require('path');

const STREAMS = ['valid-captures', 'functional-failures', 'cleanup', 'image-coverage'];

function safeName(value) {
  return String(value === undefined || value === null ? 'unknown' : value).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function record(work, stream, data) {
  const dir = path.join(work, 'evidence', stream);
  try { fs.mkdirSync(dir, { recursive: true }); } catch { /* best effort */ }
  const base = safeName(data && data.id);
  // Keep every record: a scenario can fail in readiness and again on capture,
  // and the first one is usually the root cause.
  for (let i = 0; i < 50; i++) {
    const file = path.join(dir, i === 0 ? `${base}.json` : `${base}-${i}.json`);
    try {
      fs.writeFileSync(file, JSON.stringify(data), { flag: 'wx' });
      break;
    } catch (e) {
      if (e && e.code === 'EEXIST') continue;
      break;
    }
  }
  try {
    fs.appendFileSync(path.join(work, `${stream}.jsonl`), JSON.stringify(data) + '\n');
  } catch { /* legacy mirror is best effort */ }
}

function read(work, stream) {
  const dir = path.join(work, 'evidence', stream);
  if (fs.existsSync(dir)) {
    const out = [];
    for (const name of fs.readdirSync(dir).sort()) {
      if (!name.endsWith('.json')) continue;
      try { out.push(JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'))); } catch { /* skip */ }
    }
    return out;
  }
  const legacy = path.join(work, `${stream}.jsonl`);
  if (!fs.existsSync(legacy)) return [];
  return fs.readFileSync(legacy, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map(line => { try { return JSON.parse(line); } catch { return null; } })
    .filter(Boolean);
}

function reset(work, streams = STREAMS) {
  for (const stream of streams) {
    fs.rmSync(path.join(work, `${stream}.jsonl`), { force: true });
    fs.rmSync(path.join(work, 'evidence', stream), { recursive: true, force: true });
  }
}

module.exports = { record, read, reset, STREAMS };
