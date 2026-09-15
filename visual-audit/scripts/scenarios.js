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
  const implementation=hash(['../engine_scripts/before.js','../engine_scripts/ready.js','../engine_scripts/images.js','../backstop.config.js','run.js','scenarios.js'].map(p=>fs.readFileSync(path.resolve(__dirname,p),'utf8')));
  return {scenarios,implementation,roles:config.roles||{},tls:config.localTlsExceptions||[],sandbox:config.browserSandbox!==false,locale:config.locale||'en-US',timezone:config.timezone||'UTC',image:'backstopjs/backstopjs:6.3.25@sha256:020d8f17eaa1cd3f2165f3caa6b3f9c56c5311c2cdedc4897b8cf0484b07040f',engine:'playwright',platform:process.arch};
}
function hash(x) {return crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');}
module.exports={validate,selected,settings,hash,url,fail};
