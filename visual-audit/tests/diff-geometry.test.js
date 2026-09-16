'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { PNG } = require('pngjs');
const { analyse, classify } = require('../scripts/diff-geometry');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diff-geometry-'));
const WIDTH = 1440;
const TALL = 6000; // a realistic full-page Drupal capture

function page(width, height, paint) {
  const png = new PNG({ width, height });
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (width * y + x) << 2;
      // A plausible page: white body with a banded header and content rows.
      const base = y < 120 ? 240 : (Math.floor(y / 400) % 2 ? 250 : 255);
      png.data[i] = base;
      png.data[i + 1] = base;
      png.data[i + 2] = base;
      png.data[i + 3] = 255;
      if (paint) paint(png.data, i, x, y);
    }
  }
  const file = path.join(dir, `p${Math.random().toString(36).slice(2)}.png`);
  fs.writeFileSync(file, PNG.sync.write(png));
  return file;
}

const block = (x0, y0, w, h, colour) => (data, i, x, y) => {
  if (x >= x0 && x < x0 + w && y >= y0 && y < y0 + h) {
    data[i] = colour[0]; data[i + 1] = colour[1]; data[i + 2] = colour[2];
  }
};

test('identical captures report no change', () => {
  const paint = block(100, 300, 200, 50, [20, 80, 200]);
  const a = page(WIDTH, TALL, paint);
  const b = page(WIDTH, TALL, paint);
  const g = analyse(a, b);
  assert.strictEqual(g.changedRatio, 0);
  assert.strictEqual(g.largestClusterPx, 0);
  assert.strictEqual(classify(g).verdict, 'diffuse');
});

test('a broken 200x50 control is caught on a long page despite a tiny percentage', () => {
  // The regression a flat threshold misses: 10,000px of 8.64M is 0.12% of the page.
  const a = page(WIDTH, TALL, block(100, 300, 200, 50, [20, 80, 200]));
  const b = page(WIDTH, TALL, block(100, 300, 200, 50, [250, 250, 250]));
  const g = analyse(a, b);
  assert.ok(g.changedRatio < 0.005, `expected a sub-0.5% diff, got ${g.changedRatio}`);
  const verdict = classify(g);
  assert.strictEqual(verdict.verdict, 'localized');
  assert.ok(g.largestClusterPx >= 4096, `cluster too small: ${g.largestClusterPx}`);
  // The reported box should land on the changed control.
  assert.ok(g.largestClusterBox.x <= 100 && g.largestClusterBox.y <= 300);
});

test('sparse rasterisation noise is not treated as a localized break', () => {
  const a = page(WIDTH, TALL);
  // ~1% of pixels shifted past the channel tolerance but evenly scattered, with
  // no dense contiguous region: the signature of re-rasterised text.
  const b = page(WIDTH, TALL, (data, i, x, y) => {
    if ((x * 31 + y * 17) % 101 === 0) { data[i] -= 40; data[i + 1] -= 40; data[i + 2] -= 40; }
  });
  const g = analyse(a, b);
  assert.ok(g.changedRatio > 0, 'sanity: some pixels must differ');
  assert.ok(g.changedRatio < 0.02, `noise should stay under the ceiling, got ${g.changedRatio}`);
  assert.strictEqual(classify(g).verdict, 'diffuse');
  assert.ok(g.largestClusterPx < 4096, `noise formed a cluster: ${g.largestClusterPx}`);
});

test('change spread across most of the page escalates rather than being dismissed', () => {
  // A global restyle (font stack or base colour change) is diffuse but is not
  // noise, so it must not fall through the "no cluster" path silently.
  const a = page(WIDTH, TALL);
  const b = page(WIDTH, TALL, (data, i, x, y) => {
    if ((x + y) % 7 === 0) { data[i] -= 40; data[i + 1] -= 40; data[i + 2] -= 40; }
  });
  const g = analyse(a, b);
  assert.ok(g.changedRatio >= 0.02, `expected a large spread, got ${g.changedRatio}`);
  assert.strictEqual(classify(g).verdict, 'widespread');
});

test('sub-tolerance colour drift is ignored entirely', () => {
  const a = page(WIDTH, 1200);
  const b = page(WIDTH, 1200, (data, i) => { data[i] -= 5; data[i + 1] -= 5; data[i + 2] -= 5; });
  const g = analyse(a, b);
  assert.strictEqual(g.changedRatio, 0);
});

test('a height change is reported rather than silently cropped', () => {
  const paint = block(100, 300, 200, 50, [20, 80, 200]);
  const a = page(WIDTH, 3000, paint);
  const b = page(WIDTH, 3600, paint);
  const g = analyse(a, b);
  assert.strictEqual(g.dimensions.heightDelta, 600);
  assert.strictEqual(g.dimensions.sameDimensions, false);
  assert.strictEqual(g.comparedHeight, 3000);
  const verdict = classify(g);
  assert.strictEqual(verdict.verdict, 'dimension');
  assert.match(verdict.reasons.join(' '), /not compared/);
});

test('a disappearing element is caught even when RGB is unchanged', () => {
  const a = page(WIDTH, 1200, (data, i, x, y) => {
    if (x >= 200 && x < 400 && y >= 200 && y < 320) { data[i + 3] = 255; }
  });
  const b = page(WIDTH, 1200, (data, i, x, y) => {
    if (x >= 200 && x < 400 && y >= 200 && y < 320) { data[i + 3] = 0; }
  });
  const g = analyse(a, b);
  assert.ok(g.largestClusterPx >= 4096, 'alpha-only change must register');
  assert.strictEqual(classify(g).verdict, 'localized');
});

test('thresholds are overridable', () => {
  const a = page(WIDTH, 1200, block(100, 300, 200, 50, [20, 80, 200]));
  const b = page(WIDTH, 1200, block(100, 300, 200, 50, [250, 250, 250]));
  const g = analyse(a, b);
  assert.strictEqual(classify(g).verdict, 'localized');
  // Raising the trigger above the observed cluster reclassifies it.
  assert.strictEqual(classify(g, { localizedClusterPx: 1e9, localizedClusterRatio: 1 }).verdict, 'diffuse');
});

test.after(() => fs.rmSync(dir, { recursive: true, force: true }));
