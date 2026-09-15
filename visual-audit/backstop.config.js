'use strict';
const {url}=require('./scripts/scenarios');
module.exports=function make(config,scenarios,mode,work='/work') {
  const reference=process.env.AUDIT_REFERENCE_URL||config.referenceUrl;
  const test=process.env.AUDIT_TEST_URL||config.testUrl;
  return {
    id:'promet_d11',engine:'playwright',engineOptions:{browser:'chromium',headless:true,chromiumSandbox:config.browserSandbox!==false,args:config.browserSandbox===false?['--no-sandbox']:[]},
    asyncCaptureLimit:Number(process.env.AUDIT_ASYNC_CAPTURE_LIMIT||3),asyncCompareLimit:Number(process.env.AUDIT_ASYNC_COMPARE_LIMIT||4),debug:false,debugWindow:false,
    onBeforeScript:'before.js',onReadyScript:'ready.js',
    scenarios:scenarios.map(s=>({...s,label:s.id,viewports:[{label:`${s.viewport.width}x${s.viewport.height}`,width:s.viewport.width,height:s.viewport.height}],url:s.path?new URL(s.path,url(test||reference)).href:s.testUrl,referenceUrl:s.path?new URL(s.path,url(reference)).href:s.referenceUrl,selectors:['document'],selectorExpansion:false,misMatchThreshold:s.threshold??0.2,requireSameDimensions:s.requireSameDimensions??config.requireSameDimensions??false,auditScenario:s,auditConfig:config,auditMode:mode,auditWork:work})),
    viewports:[],paths:{bitmaps_reference:work+'/bitmaps_reference',bitmaps_test:work+'/bitmaps_test',engine_scripts:'/opt/audit/engine_scripts',html_report:work+'/html_report',ci_report:work+'/ci_report'},report:['browser','CI'],openReport:false
  };
};
