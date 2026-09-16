'use strict';
// Classifies a visual difference by WHERE it is, not just how much of it there is.
//
// A flat mismatch percentage conflates two very different outcomes: a whole page
// re-rasterising a fraction of a percent (harmless) and one component rendering
// completely wrong (a release blocker). Both can read as "0.4% different".
//
// This module reduces a pair of captures to a grid of changed cells, groups the
// cells into connected clusters, and reports the largest one. A large diffuse
// spread with no substantial cluster is rendering noise; a dense cluster is a
// broken component, however small its share of the page.
const fs = require('fs');
const { PNG } = require('pngjs');

const DEFAULTS = {
  cellSize: 16, // grid resolution in source pixels
  stride: 2, // sample every Nth pixel per axis; bounds cost on tall full-page captures
  channelTolerance: 12, // per-channel delta ignored as rasterisation noise (0-255)
  cellMinChangedRatio: 0.18, // share of sampled pixels in a cell that must differ
  maxPixels: 40e6, // refuse absurd inputs rather than exhausting container memory
};

function decode(file) {
  return PNG.sync.read(fs.readFileSync(file));
}

// 4-connected flood fill over the changed-cell grid.
function clusters(grid, cols, rows) {
  const seen = new Uint8Array(grid.length);
  const found = [];
  const stack = [];
  for (let start = 0; start < grid.length; start++) {
    if (!grid[start] || seen[start]) continue;
    stack.length = 0;
    stack.push(start);
    seen[start] = 1;
    let cells = 0;
    let minX = cols; let maxX = -1; let minY = rows; let maxY = -1;
    while (stack.length) {
      const idx = stack.pop();
      const x = idx % cols; const y = (idx - x) / cols;
      cells++;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      const neighbours = [
        x > 0 ? idx - 1 : -1,
        x < cols - 1 ? idx + 1 : -1,
        y > 0 ? idx - cols : -1,
        y < rows - 1 ? idx + cols : -1,
      ];
      for (const n of neighbours) {
        if (n >= 0 && grid[n] && !seen[n]) { seen[n] = 1; stack.push(n); }
      }
    }
    found.push({ cells, minX, maxX, minY, maxY });
  }
  return found.sort((a, b) => b.cells - a.cells);
}

/**
 * Compare two PNG captures and describe the geometry of their differences.
 * Only the overlapping region is compared; a height or width change is reported
 * separately via `dimensions` because cropped-away content is not evidence of
 * sameness — it is simply uncompared.
 */
function analyse(referencePath, testPath, options = {}) {
  const opt = { ...DEFAULTS, ...options };
  const ref = decode(referencePath);
  const test = decode(testPath);
  const width = Math.min(ref.width, test.width);
  const height = Math.min(ref.height, test.height);
  if (width * height > opt.maxPixels) {
    return { skipped: 'image_too_large', dimensions: dimensionDelta(ref, test) };
  }
  const cols = Math.max(1, Math.ceil(width / opt.cellSize));
  const rows = Math.max(1, Math.ceil(height / opt.cellSize));
  const changedPerCell = new Uint32Array(cols * rows);
  const sampledPerCell = new Uint32Array(cols * rows);
  const tol = opt.channelTolerance;
  let changed = 0; let sampled = 0;

  for (let y = 0; y < height; y += opt.stride) {
    const cellRow = ((y / opt.cellSize) | 0) * cols;
    for (let x = 0; x < width; x += opt.stride) {
      const ri = (ref.width * y + x) << 2;
      const ti = (test.width * y + x) << 2;
      // Alpha is compared too: a disappearing element can preserve RGB while
      // dropping opacity, which must not read as unchanged.
      const diff = Math.abs(ref.data[ri] - test.data[ti]) > tol
        || Math.abs(ref.data[ri + 1] - test.data[ti + 1]) > tol
        || Math.abs(ref.data[ri + 2] - test.data[ti + 2]) > tol
        || Math.abs(ref.data[ri + 3] - test.data[ti + 3]) > tol;
      const cell = cellRow + ((x / opt.cellSize) | 0);
      sampledPerCell[cell]++;
      sampled++;
      if (diff) { changedPerCell[cell]++; changed++; }
    }
  }

  const grid = new Uint8Array(cols * rows);
  let changedCells = 0;
  for (let i = 0; i < grid.length; i++) {
    if (sampledPerCell[i] && changedPerCell[i] / sampledPerCell[i] >= opt.cellMinChangedRatio) {
      grid[i] = 1;
      changedCells++;
    }
  }

  const groups = clusters(grid, cols, rows);
  const largest = groups[0] || null;
  const cellArea = opt.cellSize * opt.cellSize;
  const pageArea = width * height;
  return {
    dimensions: dimensionDelta(ref, test),
    comparedWidth: width,
    comparedHeight: height,
    changedRatio: sampled ? changed / sampled : 0,
    changedCells,
    totalCells: cols * rows,
    clusterCount: groups.length,
    largestClusterPx: largest ? largest.cells * cellArea : 0,
    largestClusterRatio: largest ? (largest.cells * cellArea) / pageArea : 0,
    largestClusterBox: largest
      ? {
        x: largest.minX * opt.cellSize,
        y: largest.minY * opt.cellSize,
        width: (largest.maxX - largest.minX + 1) * opt.cellSize,
        height: (largest.maxY - largest.minY + 1) * opt.cellSize,
      }
      : null,
  };
}

function dimensionDelta(ref, test) {
  return {
    reference: { width: ref.width, height: ref.height },
    test: { width: test.width, height: test.height },
    widthDelta: test.width - ref.width,
    heightDelta: test.height - ref.height,
    sameDimensions: ref.width === test.width && ref.height === test.height,
  };
}

/**
 * Turn geometry into a verdict.
 *
 * `localized`  a dense cluster exceeding localizedClusterRatio — a component is
 *              broken. Fails even when the overall percentage is small, which is
 *              exactly the case a flat threshold misses.
 * `diffuse`    change is spread with no substantial cluster — consistent with
 *              rasterisation noise rather than a structural break.
 * `dimension`  the capture heights diverged beyond tolerance, so part of the page
 *              was never compared at all.
 */
function classify(geometry, thresholds = {}) {
  // Absolute area is the primary trigger, deliberately. A broken 200x50 button is
  // 0.23% of a short page but 0.09% of a long one; a ratio-only rule would catch
  // the same defect on one page and miss it on another. 4096px is a 64x64 block —
  // about the smallest change that can hide a broken control or icon.
  const t = {
    localizedClusterPx: 4096,
    localizedClusterRatio: 0.0025, // escalate on very large contiguous regions too
    diffuseCeilingRatio: 0.02, // above this, diffuse change is not dismissible
    heightTolerancePx: 24,
    ...thresholds,
  };
  if (geometry.skipped) return { verdict: 'unknown', reason: geometry.skipped };
  const reasons = [];
  const dim = geometry.dimensions || {};
  const heightDrift = Math.abs(dim.heightDelta || 0);
  if (heightDrift > t.heightTolerancePx) {
    reasons.push(`capture height changed by ${dim.heightDelta}px; content beyond the shorter capture was not compared`);
  }
  if (geometry.largestClusterPx >= t.localizedClusterPx
      || geometry.largestClusterRatio >= t.localizedClusterRatio) {
    reasons.push(`localized change of ${geometry.largestClusterPx}px² (${(geometry.largestClusterRatio * 100).toFixed(2)}% of the page) in a single contiguous region`);
    return { verdict: 'localized', reasons, thresholds: t };
  }
  if (geometry.changedRatio >= t.diffuseCeilingRatio) {
    reasons.push(`widespread change across ${(geometry.changedRatio * 100).toFixed(2)}% of sampled pixels`);
    return { verdict: 'widespread', reasons, thresholds: t };
  }
  if (heightDrift > t.heightTolerancePx) return { verdict: 'dimension', reasons, thresholds: t };
  return { verdict: 'diffuse', reasons, thresholds: t };
}

module.exports = { analyse, classify, clusters, DEFAULTS };
