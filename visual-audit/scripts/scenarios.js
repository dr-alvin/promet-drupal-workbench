'use strict';
const crypto = require('crypto');
const fs=require('fs'),path=require('path');
function fail(message, code=64) { const e = new Error(message); e.code=code; throw e; }
function url(value) {
  let u; try { u=new URL(value); } catch { fail('Invalid absolute URL'); }
  if (!['http:','https:'].includes(u.protocol) || u.username || u.password) fail('Only HTTP(S) URLs without embedded credentials are supported');
  return u;
}
function validate(config) {
  if (!Array.isArray(config.scenarios) || !config.scenarios.length) fail('Scenario set must not be empty');
  if (!['local','dev','test','multidev'].includes(config.environment?.kind) || config.environment.authorized!==true) fail('Explicit non-production authorization required',3);
  const ids=new Set();
  for (const s of config.scenarios) {
    if (!/^[a-zA-Z0-9_-]+$/.test(s.id||'') || ids.has(s.id)) fail('Scenario IDs must be unique and filesystem safe');
    ids.add(s.id);
    if (!s.label || !s.expectedUrl || !Array.isArray(s.requiredElements) || !s.requiredElements.length || !s.ready?.selector) fail(`${s.id}: label, expectedUrl, requiredElements and ready.selector are required`);
    if (!s.viewport || !Number.isInteger(s.viewport.width) || !Number.isInteger(s.viewport.height) || s.viewport.width<1 || s.viewport.height<1) fail('Positive integer viewport required');
    if (Boolean(s.path) === Boolean(s.referenceUrl && s.testUrl)) fail('Use a relative path OR explicit reference/test URL pair');
    if (s.path && (!s.path.startsWith('/') || s.path.startsWith('//'))) fail('Paths must be site-relative');
    if (!s.path && s.referenceUrl===s.testUrl) fail('Absolute comparison endpoints must be different');
    if (s.referenceUrl) {url(s.referenceUrl);url(s.testUrl);}
    if (s.mutates && (!config.environment.disposable || !config.environment.allowFixtureMutation || !s.cleanup?.length)) fail('Mutating scenarios need authorized disposable environment and cleanup',3);
    if (s.role && s.role!=='anonymous' && !config.roles?.[s.role]) fail('Unknown authentication role');
    if (s.threshold!==undefined && (typeof s.threshold!=='number' || s.threshold<0 || s.threshold>100)) fail('Threshold must be percentage 0–100');
    for (const step of [...(s.setup||[]),...(s.interactions||[]),...(s.assertions||[]),...(s.cleanup||[])]) {
      if (!['click','fill','select','press','check','wait','assertVisible','assertText','upload'].includes(step.action)) fail('Unsupported interaction action');
      if (!step.selector) fail('Interaction requires selector');
      if (step.action==='upload' && (!step.file || step.file.includes('..') || step.file.startsWith('/'))) fail('Upload fixture must be inside configuration directory');
      if (step.action==='upload' && !s.mutates) fail('Uploads require a mutating disposable scenario',3);
    }
    for (const mask of s.masks||[]) if (!mask.selector || !mask.reason) fail('Masks require a selector and justification');
  }
  return config;
}
function selected(config, selection) {
  if (!selection) return config.scenarios;
  const ids=selection.split(',');
  if (ids.some(id=>!config.scenarios.some(s=>s.id===id))) fail('Unknown selected scenario');
  return config.scenarios.filter(s=>ids.includes(s.id));
}
function settings(config, scenarios) {
  const implementation=hash(['../engine_scripts/before.js','../engine_scripts/ready.js','../engine_scripts/images.js','../engine_scripts/evidence.js','../backstop.config.js','run.js','scenarios.js'].map(p=>fs.readFileSync(path.resolve(__dirname,p),'utf8')));
  // Stability coverage and antialiasing handling change what the baseline
  // attests to, so they belong in the identity: a baseline proved with a
  // 4-scenario repeat must not silently satisfy a run demanding a full repeat.
  const stability={full:!!config.stabilityFullRepeat,sampleSize:Number.isInteger(config.stabilitySampleSize)?config.stabilitySampleSize:null,ignoreAntialiasing:config.ignoreAntialiasing!==false};
  return {scenarios,implementation,stability,roles:config.roles||{},tls:config.localTlsExceptions||[],sandbox:config.browserSandbox!==false,locale:config.locale||'en-US',timezone:config.timezone||'UTC',image:'backstopjs/backstopjs:6.3.25@sha256:020d8f17eaa1cd3f2165f3caa6b3f9c56c5311c2cdedc4897b8cf0484b07040f',engine:'playwright',platform:process.arch};
}
function hash(x) {return crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');}
// Even sample across a list, always keeping entries flagged by `priority`.
// Spreading the sample beats truncation: a leading slice would only ever
// re-test the first N routes and never notice flake further down the catalogue.
function evenSample(list, cap, priority=()=>false) {
  if (cap<=0 || !list.length) return [];
  if (cap>=list.length) return list.slice();
  const keep=list.filter(priority), rest=list.filter(x=>!priority(x));
  if (keep.length>=cap) return keep.slice(0,cap);
  const room=cap-keep.length, step=rest.length/room;
  const sampled=[];
  for (let i=0;i<room;i++) sampled.push(rest[Math.min(rest.length-1,Math.floor(i*step))]);
  const chosen=new Set([...keep,...sampled]);
  return list.filter(x=>chosen.has(x));
}
// Which scenarios get re-captured to prove the baseline is reproducible.
// Repeating the entire catalogue doubles the cost of every baseline to detect
// flake that in practice clusters in a few dynamic routes. Critical scenarios
// are always re-tested; the rest is sampled. stabilityFullRepeat restores the
// exhaustive behaviour.
function stabilitySample(list, config={}) {
  if (config.stabilityFullRepeat) return list.slice();
  const configured=config.stabilitySampleSize;
  const cap=Number.isInteger(configured)&&configured>=0?configured:Math.min(list.length,Math.max(4,list.filter(s=>s.critical).length));
  return evenSample(list, cap, s=>!!s.critical);
}
module.exports={validate,selected,settings,hash,url,fail,evenSample,stabilitySample};
