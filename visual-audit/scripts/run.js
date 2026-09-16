'use strict';
const fs=require('fs'),path=require('path');
const {validate,selected,settings,hash,fail,stabilitySample}=require('./scenarios');
const work=process.env.AUDIT_WORK||'/work';
const write=(name,data)=>fs.writeFileSync(path.join(work,name),JSON.stringify(data,null,2)+'\n');
const evidence=require('../engine_scripts/evidence');
// Reads the per-record evidence directory (race-free) and falls back to the
// legacy .jsonl. Concurrent captures cannot safely append to a shared file.
const readLines=name=>evidence.read(work,name.replace(/\.jsonl$/,''));
const walk=dir=>fs.existsSync(dir)?fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(path.join(dir,e.name)):[path.join(dir,e.name)]):[];
function resetEvidence(){evidence.reset(work);}
function report(result,scenarios=[]) {
  write('result.json',result);
  fs.writeFileSync(path.join(work,'developer-report.md'),'# Visual verification\n\nStatus: '+result.status+'\n\n'+(result.message||'Passed the configured scenarios and thresholds.')+'\n\nSee result.json for failures and Backstop status. Screenshot comparison alone does not establish upgrade completion.\n');
  fs.writeFileSync(path.join(work,'qa-report.md'),'# Browser QA\n\n'+scenarios.map(s=>`## ${s.label} (${s.id})\n\nOpen ${s.path||'[configured environment URL]'} as ${s.role||'anonymous'} at ${s.viewport.width} × ${s.viewport.height}.\n\n${[...(s.setup||[]),...(s.interactions||[])].map(x=>`- ${x.action} ${x.selector}${x.value?' with '+x.value:''}`).join('\n')}\n\nExpected page: ${typeof s.expectedUrl==='string'?s.expectedUrl:JSON.stringify(s.expectedUrl)}. Required content: ${(s.requiredText||s.requiredElements).join(', ')}. Record unexpected behavior against this scenario ID.\n`).join('\n')+'\nUAT requires a named business approver and the exact tested release.\n');
}
async function capture(command,config) {
  const backstop=require('backstopjs');
  try {await backstop(command,{config});return 0;}catch(e){return Number.isInteger(e?.code)?e.code:1;}
}
async function main(){
  fs.mkdirSync(work,{recursive:true});
  const [mode,file]=process.argv.slice(2);
  let scenarios=[];const started=Date.now();let captures=0;let criticalResult=null;let stabilityCoverage=null;let geometryReview=null;
  try {
    const config=JSON.parse(fs.readFileSync(file,'utf8'));
    if(mode==='sitemap') {
      if(!config.environment?.authorized || !['local','dev','test','multidev'].includes(config.environment.kind))fail('Non-production authorization required',3);
      const result=await require('./sitemap').discover(config.sitemap||{});write('sitemap.json',result);return 0;
    }
    if(!['reference','test'].includes(mode))fail('Invalid browser command');
    validate(config);scenarios=selected(config,process.env.AUDIT_SELECT).slice().sort((a,b)=>Number(!!b.critical)-Number(!!a.critical));
    const partial=scenarios.length!==config.scenarios.length;
    if(config.browserSandbox===false&&!config.sandboxExceptionReason)fail('Disabling sandbox requires a documented container-specific reason',3);
    const captureSettings=settings(config,scenarios),identity=hash(captureSettings);
    const baseline=path.join(work,'capture-settings.json');
    if(mode==='reference'&&(fs.existsSync(baseline)||fs.existsSync(path.join(work,'bitmaps_reference'))))fail('Baseline exists; use a new output directory',3);
    if(mode==='test') {
      if(!fs.existsSync(baseline))fail('No baseline',3);
      const saved=JSON.parse(fs.readFileSync(baseline));
      if(!saved.stable || saved.identity!==identity)fail('Baseline settings changed or baseline unstable',3);
      if(!saved.referenceFiles?.length)fail('Baseline image manifest missing',3);
      for(const item of saved.referenceFiles) {
        const file=path.join(work,item.path);
        if(!fs.existsSync(file)||hash(fs.readFileSync(file).toString('base64'))!==item.hash)fail('Baseline image missing or changed',3);
      }
    }
    resetEvidence();
    const make=require('../backstop.config');
    const backstopConfig=make(config,scenarios,mode,work);
    const critical=scenarios.filter(s=>s.critical);
    if(mode==='test'&&critical.length&&critical.length<scenarios.length){
      const smoke=make(config,critical,mode,work);
      smoke.paths={...smoke.paths,bitmaps_test:work+'/critical/bitmaps_test',html_report:work+'/critical/html_report',ci_report:work+'/critical/ci_report'};
      const smokeStatus=await capture('test',smoke);captures+=critical.length;
      const valid=readLines('valid-captures.jsonl'),failures=readLines('functional-failures.jsonl');
      const complete=JSON.stringify(valid.map(x=>x.id).sort())===JSON.stringify(critical.map(x=>x.id).sort());
      criticalResult={status:smokeStatus===0&&complete&&!failures.length?'passed':'failed',validCaptures:valid,failures};
      write('critical-result.json',criticalResult);
      if(criticalResult.status!=='passed'){
        report({schemaVersion:'1.0',mode,status:failures.length?'functional_failure':!complete?'capture_failure':'visual_mismatch',exitCode:!complete&&!failures.length?2:1,coverage:'partial',criticalResult,expectedScenarios:scenarios.map(s=>s.id),message:'Critical scenarios failed; full required catalog was not completed.',metrics:{elapsedSeconds:(Date.now()-started)/1000,captureAttempts:captures}},scenarios);
        return !complete&&!failures.length?2:1;
      }
      resetEvidence();
    }
    let status=await capture(mode,backstopConfig);captures+=scenarios.length;
    let valid=readLines('valid-captures.jsonl'),functional=readLines('functional-failures.jsonl');
    const expected=scenarios.map(s=>s.id).sort();
    const complete=JSON.stringify(valid.map(x=>x.id).sort())===JSON.stringify(expected);
    let pngs=walk(path.join(work,mode==='reference'?'bitmaps_reference':'bitmaps_test')).filter(x=>x.endsWith('.png')&&!x.includes('diff'));
    let code=functional.length?1:!complete||pngs.length<scenarios.length?2:status?1:0;
    if(mode==='reference'&&code===0) {
      // Re-capture against the SAME reference site to measure baseline stability.
      // Sampled by default: critical scenarios plus an even spread, rather than
      // paying a second full pass over the catalogue on every baseline.
      resetEvidence();
      const sample=stabilitySample(scenarios,config);
      const sampleIds=new Set(sample.map(s=>s.id));
      const repeat={...backstopConfig,scenarios:backstopConfig.scenarios.filter(s=>sampleIds.has(s.auditScenario.id)).map(s=>({...s,url:s.referenceUrl,auditMode:'reference'}))};
      const repeatStatus=await capture('test',repeat);captures+=sample.length;
      const repeatValid=readLines('valid-captures.jsonl');
      const repeatFunctional=readLines('functional-failures.jsonl');
      const stable=repeatStatus===0&&repeatValid.length===sample.length&&!repeatFunctional.length;
      stabilityCoverage={sampled:sample.map(s=>s.id),total:scenarios.length,full:sample.length===scenarios.length};
      write('capture-settings.json',{...captureSettings,identity,stable,stabilityCoverage,referenceFiles:walk(path.join(work,'bitmaps_reference')).filter(f=>f.endsWith('.png')).map(f=>({path:path.relative(work,f),hash:hash(fs.readFileSync(f).toString('base64'))}))});
      if(!stable){code=repeatFunctional.length?1:2;status=repeatStatus;functional.push(...repeatFunctional);}
    }
    // Geometry review runs on the real comparison only. It can escalate a pass
    // to a failure (a dense localized break under the flat threshold); it never
    // downgrades unless diffGeometry.allowDiffuseDowngrade is explicitly set.
    if(mode==='test'&&!functional.length){
      try{
        geometryReview=require('./geometry-review').review(work,config);
        if(geometryReview){
          write('diff-geometry.json',geometryReview);
          if(geometryReview.escalations>0&&code===0){code=1;status=status||1;}
        }
      }catch(e){geometryReview={error:e.message};write('diff-geometry.json',geometryReview);}
    }
    if(code===0&&partial)code=3;
    const result={coverage:partial?'partial':'complete',criticalResult,stabilityCoverage,geometryReview,imageCoverage:readLines('image-coverage.jsonl'),metrics:{elapsedSeconds:(Date.now()-started)/1000,captureAttempts:captures},schemaVersion:'1.0',mode,status:code===0?'passed':code===3?'partial':code===2?'capture_failure':functional.length?'functional_failure':'visual_mismatch',underlyingBackstopStatus:status,exitCode:code,expectedScenarios:expected,validCaptures:valid,failures:functional,message:code===0?'Passed the configured scenarios and thresholds.':'Verification did not pass; inspect failures, completeness and baseline stability.'};
    report(result,scenarios);return code;
  } catch(e){const code=[1,2,3,64].includes(e.code)?e.code:2;report({schemaVersion:'1.0',status:code===3?'blocked':'tool_failure',exitCode:code,message:e.message},scenarios);return code;}
}
main().then(code=>{process.exitCode=code;},e=>{console.error(e);process.exitCode=2;}).finally(()=>{
  // Backstop/Playwright can leave browser handles open after resolving, which keeps the
  // container alive until the launcher's timeout kills it. Reports are written
  // synchronously above, so it is safe to force the exit once the loop is otherwise idle.
  setTimeout(()=>process.exit(process.exitCode??2),2000).unref();
});
