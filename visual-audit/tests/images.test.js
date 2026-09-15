'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const ready=require('../engine_scripts/ready');
const {readiness}=require('../engine_scripts/images');
const fs=require('fs'),os=require('os'),path=require('path');
const pixel='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><rect width="24" height="24" fill="red"/></svg>';
const scenario={ready:{selector:'h1',timeoutMs:350}};
test('real Chromium image coverage and interaction regressions',async t=>{
 const browser=await chromium.launch({headless:true,chromiumSandbox:true});
 try{
  const page=await browser.newPage({viewport:{width:640,height:480}});
  await t.test('visible broken image fails',async()=>{await page.setContent('<img width="24" height="24" src="data:image/png,bad">');await assert.rejects(readiness(page,scenario));});
  await t.test('hidden responsive variant is outside coverage',async()=>{await page.setContent('<style>@media(min-width:600px){#mobile{display:none}}</style><img id="mobile" src="data:image/png,bad"><h1>Home</h1>');const r=await readiness(page,scenario);assert.equal(r.outsideCoverage.length,1);assert.equal(r.ready,true);});
  await t.test('bounded scrolling exercises lazy image',async()=>{await page.setContent('<div style="height:1400px">top</div><img width="24" height="24" loading="lazy" src=\''+pixel+'\'>');const r=await readiness(page,scenario);assert.equal(r.covered.length,1);assert.ok(r.scrollDistance>0);});
  await t.test('required hidden image fails until interaction',async()=>{await page.setContent('<button onclick="document.querySelector(\'img\').style.display=\'block\'">Open</button><img id="required" style="display:none" src=\''+pixel+'\'>');const s={ready:{timeoutMs:200,requiredImages:['#required']}};await assert.rejects(readiness(page,s));await page.locator('button').click();assert.equal((await readiness(page,s)).ready,true);});
  await t.test('missing required image and exhausted lazy readiness fail',async()=>{await assert.rejects(readiness(page,{ready:{timeoutMs:100,requiredImages:['#missing']}}));await page.setContent('<img width="40" height="40" data-src="unexercised.png">');await assert.rejects(readiness(page,scenario));});
  await t.test('expected URL failure cannot produce readiness pass',async()=>{await page.setContent('<h1>Home</h1>');const dir=fs.mkdtempSync(path.join(os.tmpdir(),'d11-ready-'));page.__auditErrors=[];await assert.rejects(ready(page,{auditScenario:{id:'url',expectedUrl:'/expected',requiredElements:['h1'],ready:{selector:'h1'}},auditConfig:{},auditMode:'test',url:'https://fixture.test/',auditWork:dir}));assert.ok(fs.readFileSync(path.join(dir,'functional-failures.jsonl'),'utf8').includes('Unexpected final URL'));});
 }finally{await browser.close();}
});
