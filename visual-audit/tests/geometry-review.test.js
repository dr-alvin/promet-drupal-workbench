'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { PNG } = require('pngjs');
const { review, provenIdentical } = require('../scripts/geometry-review');

function makeWork(pairs) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'geometry-review-'));
  fs.mkdirSync(path.join(work, 'html_report'), { recursive: true });
  fs.mkdirSync(path.join(work, 'bitmaps_reference'), { recursive: true });
  fs.mkdirSync(path.join(work, 'bitmaps_test'), { recursive: true });
  const tests = pairs.map((p, i) => {
    const name = `pair${i}.png`;
    writePage(path.join(work, 'bitmaps_reference', name), p.height || 2000, p.ref);
    writePage(path.join(work, 'bitmaps_test', name), p.testHeight || p.height || 2000, p.test);
    return {
      pair: {
        reference: `../bitmaps_reference/${name}`,
        test: `../bitmaps_test/${name}`,
        label: p.label || `scenario-${i}`,
        viewportLabel: '1440x900',
        diff: p.diff === undefined ? { misMatchPercentage: '0.12', rawMisMatchPercentage: 0.12 } : p.diff,
      },
      status: p.status || 'pass',
    };
  });
  fs.writeFileSync(
    path.join(work, 'html_report', 'config.js'),
    `report(${JSON.stringify({ testSuite: 'BackstopJS', tests }, null, 2)});`
  );
  return work;
}

function writePage(file, height, paint) {
  const width = 1440;
  const png = new PNG({ width, height });
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (width * y + x) << 2;
      png.data[i] = 255; png.data[i + 1] = 255; png.data[i + 2] = 255; png.data[i + 3] = 255;
      if (paint) paint(png.data, i, x, y);
    }
  }
  fs.writeFileSync(file, PNG.sync.write(png));
}

const control = (colour) => (data, i, x, y) => {
  if (x >= 100 && x < 300 && y >= 400 && y < 450) {
    data[i] = colour[0]; data[i + 1] = colour[1]; data[i + 2] = colour[2];
  }
};

test('escalates a localized break that Backstop passed under its flat threshold', () => {
  const work = makeWork([
    { label: 'home', height: 6000, ref: control([20, 80, 200]), test: control([255, 255, 255]), status: 'pass' },
  ]);
  const result = review(work, {});
  assert.strictEqual(result.escalations, 1);
  assert.strictEqual(result.pairs[0].verdict, 'localized');
  assert.strictEqual(result.pairs[0].action, 'escalated');
  assert.deepStrictEqual(result.escalatedLabels, ['home (1440x900)']);
  fs.rmSync(work, { recursive: true, force: true });
});

test('does not escalate genuinely identical captures', () => {
  const paint = control([20, 80, 200]);
  const work = makeWork([{ label: 'home', ref: paint, test: paint, status: 'pass' }]);
  const result = review(work, {});
  assert.strictEqual(result.escalations, 0);
  fs.rmSync(work, { recursive: true, force: true });
});

test('skips decoding when Backstop proved byte equality via its hash fast path', () => {
  // Backstop already short-circuits identical files; re-decoding them is waste.
  assert.strictEqual(provenIdentical({ misMatchPercentage: '0.00' }), true);
  assert.strictEqual(provenIdentical({ misMatchPercentage: '0.00', rawMisMatchPercentage: 0 }), false);
  const paint = control([20, 80, 200]);
  const work = makeWork([
    { label: 'home', ref: paint, test: paint, status: 'pass', diff: { misMatchPercentage: '0.00' } },
  ]);
  const result = review(work, {});
  assert.strictEqual(result.pairs[0].verdict, 'identical');
  fs.rmSync(work, { recursive: true, force: true });
});

test('a failing pair with only diffuse change is flagged, not silently downgraded', () => {
  const work = makeWork([
    {
      label: 'noisy',
      height: 3000,
      ref: null,
      test: (data, i, x, y) => {
        if ((x * 31 + y * 17) % 101 === 0) { data[i] -= 60; data[i + 1] -= 60; data[i + 2] -= 60; }
      },
      status: 'fail',
    },
  ]);
  const held = review(work, {});
  assert.strictEqual(held.pairs[0].action, 'flagged_noise');
  assert.strictEqual(held.downgrades, 0);

  const permitted = review(work, { diffGeometry: { allowDiffuseDowngrade: true } });
  assert.strictEqual(permitted.pairs[0].action, 'downgraded');
  assert.strictEqual(permitted.downgrades, 1);
  fs.rmSync(work, { recursive: true, force: true });
});

test('a height change is escalated because the extra content was never compared', () => {
  const work = makeWork([
    { label: 'grown', height: 2000, testHeight: 2600, ref: null, test: null, status: 'pass' },
  ]);
  const result = review(work, {});
  assert.strictEqual(result.pairs[0].verdict, 'dimension');
  assert.strictEqual(result.escalations, 1);
  assert.strictEqual(result.pairs[0].heightDelta, 600);
  fs.rmSync(work, { recursive: true, force: true });
});

test('missing bitmaps are recorded as missing evidence rather than ignored', () => {
  const work = makeWork([{ label: 'gone', ref: null, test: null, status: 'pass' }]);
  fs.rmSync(path.join(work, 'bitmaps_test', 'pair0.png'));
  const result = review(work, {});
  assert.strictEqual(result.pairs[0].verdict, 'unavailable');
  assert.strictEqual(result.skipped, 1);
  fs.rmSync(work, { recursive: true, force: true });
});

test('review can be disabled and tolerates a missing report', () => {
  const work = makeWork([{ label: 'home', ref: null, test: null }]);
  assert.strictEqual(review(work, { diffGeometry: { enabled: false } }), null);
  fs.rmSync(path.join(work, 'html_report', 'config.js'));
  assert.strictEqual(review(work, {}), null);
  fs.rmSync(work, { recursive: true, force: true });
});
