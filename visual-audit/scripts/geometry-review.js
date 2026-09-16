'use strict';
// Second-pass review of a completed Backstop comparison.
//
// Backstop reduces each pair to one number and compares it to a flat threshold.
// That number cannot distinguish a re-rasterised page from a broken component,
// so this pass re-examines every pair that actually differs and classifies the
// difference by geometry.
//
// It is allowed to ESCALATE (a dense localized change fails even when the
// percentage is small — the defect a flat threshold structurally cannot catch).
// It only DOWNGRADES when explicitly enabled, because turning a red gate green
// on heuristics is not a trade this pipeline should make by default.
const fs = require('fs');
const path = require('path');
const { analyse, classify } = require('./diff-geometry');

const ESCALATING = new Set(['localized', 'widespread', 'dimension']);

function readBackstopReport(work) {
  const file = path.join(work, 'html_report', 'config.js');
  if (!fs.existsSync(file)) return null;
  const raw = fs.readFileSync(file, 'utf8');
  const open = raw.indexOf('(');
  const close = raw.lastIndexOf(')');
  if (open < 0 || close <= open) return null;
  try { return JSON.parse(raw.slice(open + 1, close)); } catch { return null; }
}

// Backstop's own MD5 fast path returns this exact shape when the files are
// byte-identical; no geometry can differ, so skip the decode entirely.
function provenIdentical(diff) {
  return !!diff && diff.misMatchPercentage === '0.00' && diff.rawMisMatchPercentage === undefined;
}

function review(work, config = {}) {
  const options = config.diffGeometry || {};
  if (options.enabled === false) return null;
  const report = readBackstopReport(work);
  if (!report || !Array.isArray(report.tests)) return null;

  const pairs = [];
  let escalations = 0; let downgrades = 0; let skipped = 0;
  for (const entry of report.tests || []) {
    const pair = entry.pair || {};
    if (!pair.reference || !pair.test) continue;
    const base = path.join(work, 'html_report');
    const refPath = path.resolve(base, pair.reference);
    const testPath = path.resolve(base, pair.test);
    const record = { label: pair.label, viewport: pair.viewportLabel, backstopStatus: entry.status };
    if (provenIdentical(pair.diff)) {
      record.verdict = 'identical';
      pairs.push(record);
      continue;
    }
    if (!fs.existsSync(refPath) || !fs.existsSync(testPath)) {
      record.verdict = 'unavailable';
      skipped++;
      pairs.push(record);
      continue;
    }
    let geometry;
    try {
      geometry = analyse(refPath, testPath, options);
    } catch (e) {
      // A decode failure must not silently vanish: it is missing evidence.
      record.verdict = 'unknown';
      record.error = e.message;
      skipped++;
      pairs.push(record);
      continue;
    }
    const verdict = classify(geometry, options);
    Object.assign(record, {
      verdict: verdict.verdict,
      reasons: verdict.reasons || [],
      changedRatio: geometry.changedRatio,
      largestClusterPx: geometry.largestClusterPx,
      largestClusterBox: geometry.largestClusterBox,
      heightDelta: geometry.dimensions && geometry.dimensions.heightDelta,
    });
    if (entry.status !== 'fail' && ESCALATING.has(verdict.verdict)) {
      record.action = 'escalated';
      escalations++;
    } else if (entry.status === 'fail' && verdict.verdict === 'diffuse') {
      // Backstop failed it, geometry says there is no structural break.
      record.action = options.allowDiffuseDowngrade ? 'downgraded' : 'flagged_noise';
      if (options.allowDiffuseDowngrade) downgrades++;
    }
    pairs.push(record);
  }

  const escalated = pairs.filter(p => p.action === 'escalated');
  return {
    schemaVersion: '1.0',
    thresholds: classify({ skipped: null, changedRatio: 0, largestClusterPx: 0, largestClusterRatio: 0, dimensions: {} }, options).thresholds,
    allowDiffuseDowngrade: !!options.allowDiffuseDowngrade,
    analysed: pairs.length,
    skipped,
    escalations,
    downgrades,
    escalatedLabels: escalated.map(p => `${p.label} (${p.viewport})`),
    pairs,
  };
}

module.exports = { review, readBackstopReport, provenIdentical, ESCALATING };
