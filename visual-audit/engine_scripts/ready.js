'use strict';
const fs=require('fs');
async function steps(page,items) {
  for(const x of items||[]) {
    const loc=page.locator(x.selector);
    switch(x.action){
      case 'click':await loc.click();break;
      case 'fill':await loc.fill(x.value);break;
      case 'select':await loc.selectOption(x.value);break;
      case 'press':await loc.press(x.value);break;
      case 'check':await loc.check();break;
      case 'wait':case 'assertVisible':await loc.waitFor({state:'visible'});break;
      case 'assertText':if(!(await loc.innerText()).includes(x.value))throw new Error('Expected text missing');break;
      case 'upload':await loc.setInputFiles('/config/'+x.file);break;
      default:throw new Error('Unknown interaction');
    }
  }
}
module.exports=async(page,scenario)=>{
  const s=scenario.auditScenario,c=scenario.auditConfig;
  try {
    if(s.role && s.role!=='anonymous') await page.locator(c.roles[s.role].successSelector).waitFor({state:'visible'});
    await steps(page,s.setup);await steps(page,s.interactions);
    await page.locator(s.ready.selector).waitFor({state:'visible'});
    const actual=new URL(page.url());
    const expectedPath=typeof s.expectedUrl==='string'?s.expectedUrl:s.expectedUrl[scenario.auditMode==='reference'?'reference':'test'];
    const expected=new URL(expectedPath,scenario.auditMode==='reference'?scenario.referenceUrl:scenario.url);
    const isLoginRedirect = actual.pathname.includes('/user/login') && !expectedPath.includes('/user/login');
    if(isLoginRedirect){
      throw new Error('Unexpected login redirect: unauthorized access to ' + expectedPath);
    }
    if(actual.host !== expected.host){
      throw new Error('Unexpected cross-host redirect from ' + expected.href + ' to ' + actual.href);
    }
    for(const selector of s.requiredElements)await page.locator(selector).waitFor({state:'visible'});
    for(const text of s.requiredText||[])if(!(await page.locator('body').innerText()).includes(text))throw new Error('Required page text missing');
    try{await page.addStyleTag({content:'html, body, *,*::before,*::after { scroll-behavior:auto!important; animation:none!important;transition:none!important;caret-color:transparent!important; }'});}catch{}
    const freezeMedia=()=>{
      if(window.jQuery?.fn?.slick){
        try{
          window.jQuery('.slick-slider').each(function(){
            if(this.slick){
              this.slick.options.autoplay=false;
              this.slick.autoPlay=false;
              window.jQuery(this).slick('slickPause');
              window.jQuery(this).slick('slickGoTo',0,true);
            }
          });
        }catch{}
      }
      try{
        if(window.Swiper && Array.isArray(window.Swiper.instances)){
          window.Swiper.instances.forEach(sw => {
            if(sw.autoplay && typeof sw.autoplay.stop === 'function') sw.autoplay.stop();
            if(typeof sw.slideTo === 'function') sw.slideTo(0, 0);
          });
        }
        document.querySelectorAll('.swiper, .swiper-container').forEach(el => {
          if(el.swiper){
            if(el.swiper.autoplay && typeof el.swiper.autoplay.stop === 'function') el.swiper.autoplay.stop();
            if(typeof el.swiper.slideTo === 'function') el.swiper.slideTo(0, 0);
          }
        });
      }catch{}
      try{
        document.querySelectorAll('.splide').forEach(el => {
          if(el.splide && typeof el.splide.Components?.Autoplay?.pause === 'function'){
            el.splide.Components.Autoplay.pause();
            el.splide.go(0);
          }
        });
      }catch{}
      try{
        if(window.jQuery?.fn?.owlCarousel){
          window.jQuery('.owl-carousel').trigger('stop.owl.autoplay');
          window.jQuery('.owl-carousel').trigger('to.owl.carousel', [0, 0]);
        }
      }catch{}
      document.querySelectorAll('video, audio').forEach(v=>{
        try{ v.pause(); v.currentTime=0; }catch{}
      });
      try{
        document.querySelectorAll('svg animate, svg animateTransform').forEach(anim => {
          if(typeof anim.endElement === 'function') anim.endElement();
        });
      }catch{}
    };
    await page.evaluate(freezeMedia);
    await page.evaluate(timeout=>Promise.race([document.fonts.ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Font readiness timeout')),timeout))]),s.ready.timeoutMs||15000);
    const coverage=await require('./images').readiness(page,s);
    await page.evaluate(freezeMedia);
    await page.evaluate(()=>{document.documentElement.style.scrollBehavior='auto';document.body.style.scrollBehavior='auto';window.scrollTo({top:0,left:0,behavior:'instant'});});
    await page.waitForTimeout(200);
    fs.appendFileSync(scenario.auditWork+'/image-coverage.jsonl',JSON.stringify({id:s.id,...coverage})+'\n');
    const imageUrls=await page.evaluate(()=>Array.from(document.images).map(i=>i.currentSrc||i.src));
    const hiddenOnly=url=>imageUrls.includes(url)&&!coverage.covered.some(i=>imageUrls[i.index]===url);
    // Reject HTTP 4xx/5xx main document responses in all modes (reference and test)
    const docErrors = (page.__auditErrors || []).filter(err => /^HTTP [45]\d\d/.test(err));
    if(docErrors.length){
      throw new Error(`Route response failed: ${docErrors.join(', ')}`);
    }

    // Reject error pages (Drupal untrusted host or fatal crash) in all modes
    const pageText = await page.evaluate(() => document.body ? document.body.innerText : '');
    if(pageText.includes('The provided host name is not valid for this server.')){
      throw new Error('Invalid host header: Drupal returned "The provided host name is not valid for this server."');
    }
    if(pageText.includes('The website encountered an unexpected error.')){
      throw new Error('Drupal fatal error: "The website encountered an unexpected error."');
    }

    if(scenario.auditMode!=='reference'){
      for(const url of page.__auditImageErrors||[])if(!hiddenOnly(url))page.__auditErrors.push('Image HTTP failure');
      for(const e of page.__auditConsoleErrors||[]){
        const isTracker = /google|hotjar|hubspot|facebook|analytics|gtm|doubleclick/i.test(e.url || '');
        if(!e.resource && !isTracker) page.__auditErrors.push('Browser console error');
      }
      if(page.__auditErrors.length)throw new Error(page.__auditErrors.join('; '));
    }
    await steps(page,s.assertions);
    for(const mask of s.masks||[])await page.locator(mask.selector).evaluateAll(elements=>elements.forEach(el=>el.style.visibility='hidden'));

  } catch(e) {
    if(s.cleanup?.length) { try {await steps(page,s.cleanup);} catch(cleanupError) {e.message+='; cleanup failed: '+cleanupError.message;} }
    fs.appendFileSync(scenario.auditWork+'/functional-failures.jsonl',JSON.stringify({id:s.id,status:'functional_failure',message:e.message,imageCoverage:e.imageCoverage})+'\n');
    throw e;
  }
};
module.exports.steps=steps;
