'use strict';
// Runs inside the browser. A full-document capture covers rendered boxes within its width.
function imageState({selectors:requiredSelectors=[],wait=false}) {
  function rendered(el) {
    const r=el.getBoundingClientRect();
    for(let n=el;n instanceof Element;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
    return r.width>0&&r.height>0&&r.right>=0&&r.left<document.documentElement.clientWidth&&r.bottom+scrollY>=0&&r.top+scrollY<document.documentElement.scrollHeight;
  }
  const images=Array.from(document.images), required=new Set(), missing=[];
  for(const selector of requiredSelectors){const matches=Array.from(document.querySelectorAll(selector));if(!matches.length)missing.push(selector);for(const el of matches){if(el.tagName!=='IMG'||!rendered(el))missing.push(selector);else required.add(el);}}
  const rows=images.map((el,index)=>({index,required:required.has(el),covered:rendered(el),ready:el.complete&&el.naturalWidth>0,complete:el.complete}));
  const readyForCapture=!missing.length&&rows.every(x=>!x.covered||x.complete);
  const result={ready:!missing.length&&rows.every(x=>!x.covered||x.ready),requiredUnavailable:missing,covered:rows.filter(x=>x.covered),outsideCoverage:rows.filter(x=>!x.covered).map(x=>({...x,reason:'hidden or outside captured area; interaction not exercised'}))};
  return wait?readyForCapture:result;
}
async function readiness(page,s){
  await page.evaluate(()=>{
    try {
      if(window.jQuery) window.jQuery(window).off('scroll.views_infinite_scroll');
    } catch{}
    document.querySelectorAll('img').forEach(img=>{
      img.loading='eager';
      if(img.dataset.src && !img.src) img.src=img.dataset.src;
      if(img.dataset.srcset && !img.srcset) img.srcset=img.dataset.srcset;
    });
    if(window.lazySizes?.loader?.checkElems) window.lazySizes.loader.checkElems();
  });
  const bound={iterations:10,maxDistance:10000,timeoutMs:2500,...s.ready.scroll};
  const start=Date.now();let distance=0;
  for(let i=0;i<bound.iterations&&distance<bound.maxDistance&&Date.now()-start<bound.timeoutMs;i++){
    if(await page.evaluate(()=>scrollY+innerHeight>=document.documentElement.scrollHeight-50))break;
    const step=Math.min(1000,bound.maxDistance-distance);await page.evaluate(n=>window.scrollBy({top:n,behavior:'instant'}),step);distance+=step;await page.waitForTimeout(40);
  }
  await page.evaluate(()=>{document.documentElement.style.scrollBehavior='auto';document.body.style.scrollBehavior='auto';window.scrollTo({top:0,left:0,behavior:'instant'});});
  await page.evaluate(()=>Promise.race([Promise.all(Array.from(document.images).filter(img=>{try{return !img.complete&&new URL(img.currentSrc||img.src||'data:',location.href).hostname===location.hostname;}catch{return false;}}).map(img=>new Promise(res=>{img.onload=img.onerror=res;}))),new Promise(r=>setTimeout(r,5000))]));
  const selectors=s.ready.requiredImages||[];
  try{await page.waitForFunction(imageState,{selectors,wait:true},{timeout:s.ready.timeoutMs||15000});}
  catch(e){return {...await page.evaluate(imageState,{selectors}).catch(()=>({ready:false,requiredUnavailable:[],covered:[],outsideCoverage:[]})),scrollDistance:distance};}
  return {...await page.evaluate(imageState,{selectors}),scrollDistance:distance};
}
module.exports={imageState,readiness};
