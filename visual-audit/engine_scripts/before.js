'use strict';
const {url}=require('../scripts/scenarios');
const evidence=require('./evidence');
module.exports=async(page,scenario)=>{
  const config=scenario.auditConfig,s=scenario.auditScenario;
  const original=page.screenshot.bind(page);
  page.screenshot=async(...args)=>{
    const fs=require('fs');
    try {
      const opts = Object.assign({ timeout: 60000 }, args[0] || {});
      const result=await original(opts);
      if(s.cleanup?.length) {
        await require('./ready').steps(page,s.cleanup);
        evidence.record(scenario.auditWork,'cleanup',{id:s.id,status:'passed'});
      }
      evidence.record(scenario.auditWork,'valid-captures',{id:s.id,status:'passed'});
      return result;
    } catch(e) {
      evidence.record(scenario.auditWork,'functional-failures',{id:s.id,status:'capture_failure',message:'Screenshot or cleanup failed'});
      throw e;
    }
  };
  page.__auditConsoleErrors=[];page.__auditImageErrors=[];
  page.on('console',m=>{if(m.type()==='error'){const t=m.text();page.__auditConsoleErrors.push({url:m.location().url||'',resource:t.startsWith('Failed to load resource:')||t.includes('Content Security Policy')||t.startsWith('Refused to')});}});
  page.setDefaultTimeout(s.ready.timeoutMs||15000);
  page.setDefaultNavigationTimeout(config.navigationTimeoutMs||60000);
  try { await page.emulateMedia({ reducedMotion: 'reduce' }); } catch {}
  await page.addInitScript(() => {
    const inject = () => {
      if (document.documentElement && !document.getElementById('__audit_scrollbar_style')) {
        const el = document.createElement('style');
        el.id = '__audit_scrollbar_style';
        el.textContent = 'html, body { overflow-x: hidden !important; max-width: 100vw !important; scrollbar-width: none !important; -ms-overflow-style: none !important; } ::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }';
        (document.head || document.documentElement).appendChild(el);
      }
    };
    if (document.documentElement) inject();
    else document.addEventListener('DOMContentLoaded', inject);
  });
  await page.setExtraHTTPHeaders({'Accept-Language':config.locale||'en-US'});
  // Backstop creates the browser context. CDP overrides retain the same context and cookies.
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Emulation.setTimezoneOverride',{timezoneId:config.timezone||'UTC'});
  await cdp.send('Emulation.setLocaleOverride',{locale:config.locale||'en-US'});
  const target=url(scenario.auditMode==='reference'?scenario.referenceUrl:scenario.url);
  const tlsHosts=config.localTlsExceptions||[];
  if(tlsHosts.length && config.environment.kind!=='local') throw new Error('TLS exceptions are local-only');
  // CDP handles certificate errors per origin, without globally ignoring TLS errors.
  if(tlsHosts.includes(target.hostname)) {
    await cdp.send('Security.enable');await cdp.send('Security.setOverrideCertificateErrors',{override:true});
    cdp.on('Security.certificateError',async e=>{
      let allowed=false;try{allowed=tlsHosts.includes(new URL(e.requestURL).hostname);}catch{}
      await cdp.send('Security.handleCertificateError',{eventId:e.eventId,action:allowed?'continue':'cancel'});
    });
  }
  page.__auditErrors=[];
  page.on('pageerror',e=>page.__auditErrors.push('JavaScript: '+e.name));
  const targetHost = target.hostname;
  page.on('response',r=>{
    if(r.status()>=400){
      let rHost = '';
      try { rHost = new URL(r.url()).hostname; } catch{}
      const isFirstParty = !rHost || rHost === targetHost || rHost.endsWith('.' + targetHost);
      if(isFirstParty){
        if(r.request().resourceType()==='image')page.__auditImageErrors.push(r.url());
        else page.__auditErrors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`);
      }
    }
  });
  if(s.role && s.role!=='anonymous') {
    const auth=config.roles[s.role];
    if(!auth.loginPath||!auth.successSelector||!auth.usernameEnv||!auth.passwordEnv) throw new Error('Incomplete role authentication configuration');
    const username=process.env[auth.usernameEnv],password=process.env[auth.passwordEnv];
    if(!username||!password) throw new Error('Authentication secrets unavailable');
    await page.goto(new URL(auth.loginPath,target).href,{waitUntil:'domcontentloaded'});
    await page.locator(auth.usernameSelector||'[name="name"]').fill(username);
    await page.locator(auth.passwordSelector||'[name="pass"]').fill(password);
    await page.locator(auth.submitSelector||'[type="submit"]').click();
    await page.locator(auth.successSelector).waitFor({state:'visible'});
    if(/\/user\/login(?:\?|$)/.test(page.url())) throw new Error('Authentication remained on login page');
  }
};
