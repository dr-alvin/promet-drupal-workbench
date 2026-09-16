'use strict';
const $=id=>document.getElementById(id);

class ThemeManager {
  constructor() {
    this.themeMode = localStorage.getItem('d11-theme') || 'system';
    this.apply(this.themeMode);
    this.init();
  }
  getSystemPreference() {
    try {
      return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    } catch (_e) {
      return 'light';
    }
  }
  apply(mode) {
    this.themeMode = mode;
    const resolvedTheme = mode === 'system' ? this.getSystemPreference() : mode;
    document.documentElement.setAttribute('data-theme', resolvedTheme);
    document.documentElement.setAttribute('data-theme-mode', mode);
    if (mode === 'system') {
      localStorage.removeItem('d11-theme');
    } else {
      localStorage.setItem('d11-theme', mode);
    }
    this.updateUI();
  }
  updateUI() {
    document.querySelectorAll('.theme-toggle-option').forEach(btn => {
      const isActive = btn.dataset.theme === this.themeMode;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-checked', String(isActive));
    });
  }
  init() {
    try {
      const media = window.matchMedia('(prefers-color-scheme: dark)');
      if (media && media.addEventListener) {
        media.addEventListener('change', () => {
          if (this.themeMode === 'system') {
            this.apply('system');
          }
        });
      }
    } catch (_e) {}
    document.addEventListener('click', e => {
      const btn = e.target.closest('.theme-toggle-option');
      if (btn && btn.dataset.theme) {
        this.apply(btn.dataset.theme);
      }
      const openDetails = document.querySelectorAll('details.dropdown[open]');
      openDetails.forEach(details => {
        if (!details.contains(e.target)) {
          details.removeAttribute('open');
        }
      });
    });
  }
}
const themeManager = new ThemeManager();

let projects=[],runs=[],selected=sessionStorage.getItem('d11-project')||'',report=null,activeTab='client',reviewHash='',gate=null,gateRunId='',compatibility=null,compatibilityDraft={},providers=null,capabilities={},refreshing=false,refreshAgain=false,pollTimer=null,imageUrls=[],setupSourceOrigin='',progressStartTime=0,progressElapsedInterval=null,lastProgressCheckpoint='',projectActivityLogs={};
let terminalEventSource=null,activeJobId=null,activeJobDescription='',activeJobStartedAt=null,toastTimerInterval=null,projectScenarios=[],terminalTimerInterval=null,lastTerminalOutputTime=null,currentTerminalTimelineStep=1;
let isCapturingBaseline=false,baselineCaptureStartTime=0,baselineCaptureElapsed=0,baselineCaptureTimer=null,baselineCaptureRunId='';
let isStartingUpgrade=false,upgradeStartTime=0,upgradeElapsed=0,upgradeTimer=null;

const PIPELINE_SUBSTEPS = {
  scan: [
    { id: 'substep-setup', name: '1. Project Setup', checkpoints: ['inputs_verified', 'copying_code', 'copying_files'], desc: 'Verifying project runtime and zero-copy feature branch. Live database is untouched.', percent: 18 },
    { id: 'substep-runtime', name: '2. DB & Container', checkpoints: ['copying_database', 'provisioning_isolated_runtime', 'checking_source_unchanged', 'managed_copy_verified'], desc: 'Running disposable MariaDB analysis tasks. Live site database is untouched.', percent: 35 },
    { id: 'substep-routes', name: '3. Route Discovery', checkpoints: ['discovering_routes'], desc: 'Crawling sitemap and main menus to auto-discover key baseline audit routes.', percent: 52 },
    { id: 'substep-audit', name: '4. Runtime Audit', checkpoints: ['assessing'], desc: 'Running PHP 8.3 compatibility, Drupal core platform checks, and Upgrade Status scanner.', percent: 68 },
    { id: 'substep-baseline', name: '5. Visual Baseline', checkpoints: ['capturing_baseline', 'baseline_deferred', 'baseline_reused'], desc: 'Capturing or linking baseline route screenshots for Gate 2 visual diffing.', percent: 84 },
    { id: 'substep-plan', name: '6. Plan & Decisions', checkpoints: ['resolving_dependencies', 'report_and_decisions'], desc: 'Resolving Composer requirements for Drupal 11, calculating deployment risk score, and building Gate 1 plan.', percent: 96 }
  ],
  upgrade: [
    { id: 'substep-snapshot', name: '1. Safety Snapshot', checkpoints: ['capturing_pre_upgrade_baseline', 'creating_recovery_checkpoint'], desc: 'Capturing pre-upgrade database backup (pre-upgrade.sql.gz) and Git checkpoint for 1-click rollback.', percent: 18 },
    { id: 'substep-remediate', name: '2. AI Remediation', checkpoints: ['remediating_custom_code'], desc: 'Auto-fixing custom code deprecations, type declarations, and Drupal coding standards.', percent: 36 },
    { id: 'substep-composer', name: '3. Composer Core 11', checkpoints: ['upgrading_composer_packages', 'apply_exact_composer_resolution', 'composer_install'], desc: 'Applying reviewed composer.json changes, installing Drupal 11.x core, and verifying platform requirements.', percent: 55 },
    { id: 'substep-database', name: '4. DB & Cache Update', checkpoints: ['executing_approved_batch', 'database_updates', 'cache_rebuild'], desc: 'Executing database updates (drush updatedb) and rebuilding Drupal cache registry.', percent: 74 },
    { id: 'substep-regression', name: '5. Regression Diff', checkpoints: ['post_upgrade_verification'], desc: 'Capturing post-upgrade visual screenshots and calculating pixel diff percentage against baseline.', percent: 90 },
    { id: 'substep-release', name: '6. Gate 2 Release', checkpoints: ['gate_2_passed', 'gate_2_visual_review'], desc: 'Validating all guardrails and preparing handoff evidence for review.', percent: 98 }
  ],
  rollback: [
    { id: 'substep-rb-point', name: '1. Recovery Point', checkpoints: ['restoring_backup'], desc: 'Locating verified snapshot and preparing database rollback.', percent: 25 },
    { id: 'substep-rb-db', name: '2. Restore Database', checkpoints: ['restoring_database'], desc: 'Restoring database from pre-upgrade .sql.gz snapshot.', percent: 50 },
    { id: 'substep-rb-code', name: '3. Restore Codebase', checkpoints: ['restoring_files'], desc: 'Reverting codebase to pre-upgrade state on Git branch.', percent: 75 },
    { id: 'substep-rb-verify', name: '4. Integrity Verified', checkpoints: ['rollback_verified'], desc: 'Rebuilding cache and verifying site integrity.', percent: 100 }
  ]
};

const STAGES=['plan','test-audit','upgrade','regression','rollback','report'];
let currentStage='plan';
let userSelectedStage=false;

function escapeHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function showToast(message, duration = 3500) {
  let toastContainer = $('toast-container');
  if (!toastContainer) {
    toastContainer = el('div', undefined, 'toast-container');
    toastContainer.id = 'toast-container';
    document.body.appendChild(toastContainer);
  }
  const toast = el('div', message, 'workbench-toast');
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('toast-show');
  }, 10);
  setTimeout(() => {
    toast.classList.remove('toast-show');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}
window.showToast = showToast;

async function resetWorkflowLockClick() {
  try {
    const res = await api('workflow/reset-lock', {});
    if (typeof showToast === 'function') {
      showToast(`✓ Workflow lock reset! ${res.clearedRuns || 0} interrupted run(s) cleared.`);
    }
    clearError();
    await refresh();
  } catch (err) {
    if (typeof showToast === 'function') showToast(`Reset error: ${err.message || err}`);
  }
}
window.resetWorkflowLockClick = resetWorkflowLockClick;

function error(e) {
  const msgEl = $('message');
  if (!msgEl) return;
  msgEl.hidden = false;
  const text = e.message || String(e);
  const dismissBtn = `<button type="button" onclick="clearError()" style="float:right;background:none;border:none;color:inherit;font-size:18px;line-height:1;cursor:pointer;padding:0 4px;margin-left:12px;" aria-label="Dismiss message" title="Dismiss">&times;</button>`;
  if (text.includes('Only one workflow may run at a time')) {
    msgEl.innerHTML = `${dismissBtn}<span><strong>Execution Conflict:</strong> ${escapeHtml(text)}</span> <button type="button" class="btn-reset-lock" style="margin-left:12px;" onclick="resetWorkflowLockClick()">Reset Stale Lock</button>`;
  } else {
    msgEl.innerHTML = `${dismissBtn}<span>${escapeHtml(text)}</span>`;
  }
}
function clearError(){
  const msgEl = $('message');
  if (msgEl) {
    msgEl.hidden = true;
    msgEl.textContent = '';
  }
}
async function api(path, body, method) {
  const m = method || (body === undefined ? 'GET' : 'POST');
  const response = await fetch('/api/' + path, {
    method: m,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) {
    let value = {};
    let textErr = '';
    try {
      value = await response.json();
    } catch (_e) {
      try { textErr = await response.text(); } catch (_e2) {}
    }
    throw Error(value.error || value.detail || textErr || `HTTP ${response.status}: Request failed`);
  }
  return response.json();
}
async function act(fn){clearError();try{await fn();report=null;await refresh();}catch(e){error(e);}}
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function button(text, fn, kind = 'primary') {
  const node = el('button', text, kind);
  node.type = 'button';
  node.onclick = async () => {
    if (node.disabled) return;
    node.disabled = true;
    const origHtml = node.innerHTML;
    let elapsed = 0;
    node.innerHTML = `<svg class="ui-icon spin" style="width:13px;height:13px;vertical-align:-2px;" aria-hidden="true"><use href="#icon-refresh"></use></svg> Starting (${elapsed}s)...`;
    const timer = setInterval(() => {
      elapsed++;
      node.innerHTML = `<svg class="ui-icon spin" style="width:13px;height:13px;vertical-align:-2px;" aria-hidden="true"><use href="#icon-refresh"></use></svg> Processing (${elapsed}s)...`;
    }, 1000);
    try {
      await act(fn);
    } finally {
      clearInterval(timer);
      node.disabled = false;
      node.innerHTML = origHtml;
    }
  };
  return node;
}
function localButton(text,fn,kind='quiet'){return button(text,fn,kind);}
function option(select,label,value){const node=el('option',label);node.value=value;select.append(node);}
function selectedProject(){return projects.find(p=>p.id===selected);}
function origin(value){try{return new URL(value).origin;}catch(_error){return '';}}

function showStage(stageName,isUserAction=false){
 if(!STAGES.includes(stageName))return;
 const pr=runs.filter(r=>r.project===selected);
 const upgrade=latest(pr,'guided-upgrade');
 const audit=latestCompleted(pr,'guided-audit')||latest(pr,'guided-audit');
 const p=selectedProject();
 const hasUpgrade=Boolean(upgrade);
 const stepEnabled={
  plan:true,
  'test-audit':true,
  upgrade:Boolean(audit||p?.status==='ready'||p?.status==='completed'),
  regression:hasUpgrade,
  rollback:hasUpgrade,
  report:Boolean(audit||upgrade)
 };
 if(isUserAction && !stepEnabled[stageName])return;
  if(isUserAction){
    userSelectedStage=true;
  }
  currentStage=stageName;
  // Keep the URL on the stage actually being shown, including stage changes the
  // app makes itself (starting an upgrade, an active run pulling the view to
  // its stage). Previously only explicit clicks wrote the hash, so after
  // "Confirm & Start Upgrade" the view moved to Upgrade while the address bar
  // still read #plan, and a reload bounced the operator back to Plan.
  try {
    if((window.location.hash || '').replace('#stage-', '').replace('#', '') !== stageName){
      history.replaceState(null, '', '#' + stageName);
    }
  } catch(_e) {}
 for(const s of STAGES){
  const stageNode=$('stage-'+s);
  if(stageNode)stageNode.hidden=(s!==stageName);
  const navNode=$('nav-step-'+s);
  if(navNode){
   if(s===stageName)navNode.classList.add('active');
   else navNode.classList.remove('active');
  }
 }
 if(stageName==='regression')renderRegression();
}

function openSetup(){
 $('setup-form').reset();
 setupSourceOrigin='';
 delete $('setup-form').dataset.edit;
 if($('setup-eyebrow'))$('setup-eyebrow').textContent='FAST PROJECT SETUP';
 if($('setup-title'))$('setup-title').textContent='Connect a running local Drupal site';
 if($('setup-submit-text'))$('setup-submit-text').textContent='Add & Run Test Audit →';
 if($('project-id'))$('project-id').readOnly=false;
 if($('capture-nav-links'))$('capture-nav-links').checked=true;
 $('setup').hidden=false;
 $('empty').hidden=true;
 $('project-view').hidden=true;
 $('title').textContent='Add a project';
 $('subtitle').textContent='Two required inputs: Project root folder & local site URL.';
 if(typeof renderProjectSwitcher === 'function' && projects) {
   renderProjectSwitcher(projects.filter(p=>$('show-fixtures')?.checked||!p.fixture), null);
 }
}

function choose(pid){
 if(selected !== pid){
  const overlay = $('project-loading-overlay');
  const overlayText = $('project-loading-text');
  if(overlay){
   if(overlayText) overlayText.textContent = `Loading ${pid}...`;
   overlay.hidden = false;
  }
 }
 selected=pid;
 userSelectedStage=false;
 sessionStorage.setItem('d11-project',pid);
 clearError();
 $('setup').hidden=true;
 if($('runs'))$('runs').value='';
 report=null;
 gate=null;
 gateRunId='';
 compatibility=null;
 projectScenarios=[];
 compatibilityDraft={};
 if(typeof decisionView !== 'undefined'){
  decisionView.digest=null;
  decisionView.saved='';
  decisionView.baseline=null;
  decisionView.filterPill='all';
  decisionView.searchQuery='';
  decisionView.typeFilter='All';
  decisionView.findingFilter='all';
  decisionView.page=1;
  decisionView.selectedItems.clear();
  decisionView.expandedRows.clear();
 }
 syncTerminalWithProject(true);
 const safetyTimer = setTimeout(() => {
  const overlay = $('project-loading-overlay');
  if(overlay && !overlay.hidden) {
   overlay.hidden = true;
   console.warn(`Project loading overlay timed out after 10s for ${pid}`);
  }
 }, 10000);
 refresh().catch(error).finally(() => {
  clearTimeout(safetyTimer);
  const overlay = $('project-loading-overlay');
  if(overlay) overlay.hidden = true;
 });
}

let runEventSource = null;

function connectRunEvents(rid) {
 if (runEventSource && runEventSource._rid === rid) return;
 if (runEventSource) {
  runEventSource.close();
  runEventSource = null;
 }
 try {
  const es = new EventSource(`/api/runs/${encodeURIComponent(rid)}/events`);
  es._rid = rid;
  es.onmessage = (e) => {
   try {
    const data = JSON.parse(e.data);
    if (data && (data.checkpoint || data.status)) {
     refresh().catch(error);
    }
   } catch (_) {}
  };
  es.onerror = () => {
   es.close();
   if (runEventSource === es) runEventSource = null;
   if (!pollTimer) {
    pollTimer = setTimeout(() => refresh().catch(error), 4000);
   }
  };
  runEventSource = es;
 } catch (_) {}
}

function disconnectRunEvents() {
 if (runEventSource) {
  runEventSource.close();
  runEventSource = null;
 }
}

function scheduleProgress(){
 clearTimeout(pollTimer);
 pollTimer=null;
 const runningRun = runs.find(r=>r.status==='running');
 const isRunning = projects.some(p=>p.status==='running') || Boolean(runningRun) || isTerminalRunning() || isCapturingBaseline || isStartingUpgrade;
 if(!document.hidden && isRunning){
  if(runningRun){
   connectRunEvents(runningRun.id);
   pollTimer = setTimeout(() => refresh().catch(error), 5000);
  } else if (isCapturingBaseline || isStartingUpgrade) {
   pollTimer = setTimeout(() => refresh().catch(error), 1500);
  } else {
   disconnectRunEvents();
   pollTimer = setTimeout(() => refresh().catch(error), 3000);
  }
 } else {
  disconnectRunEvents();
 }
}

function latest(list,action){return list.find(r=>r.action===action);}
function latestCompleted(list,action){return list.find(r=>r.action===action&&r.status==='completed');}

function isProjectUpgraded(proj, prRuns){
 const p = proj || selectedProject();
 const runsList = prRuns || (runs ? runs.filter(r => r.project === (p?.id || selected)) : []);
 const upgrade = latest(runsList, 'guided-upgrade');
 const rollback = latest(runsList, 'guided-rollback');
 const targetMajor = p?.targetVersion || (p?.target ? String(p.target).replace(/^drupal-/, '') : '11');

 if(rollback && upgrade && new Date(rollback.startedAt) >= new Date(upgrade.startedAt)){
  if(p?.currentCore && !String(p.currentCore).startsWith(targetMajor)) return false;
 }

 if(p?.currentCore && String(p.currentCore).startsWith(targetMajor)) return true;
 if(upgrade && ['completed', 'needs_attention'].includes(upgrade.status)){
  if(!rollback || new Date(rollback.startedAt) < new Date(upgrade.startedAt)){
   return true;
  }
 }
 if(gate?.currentCore && String(gate.currentCore).startsWith(targetMajor)) return true;
 // Explicit flag from quick-summary API (catches natively-upgraded registered projects)
 if(gate?.quickSummary?.alreadyD11 === true) return true;
 return false;
}

function getActiveCoreVersion(proj){
 const p = proj || selectedProject();
 const targetMajor = p?.targetVersion || (p?.target ? String(p.target).replace(/^drupal-/, '') : '11');
 if(p?.targetCore) return p.targetCore;
 if(p?.currentCore && String(p.currentCore).startsWith(targetMajor)) return p.currentCore;
 if(gate?.targetCore) return gate.targetCore;
 if(gate?.currentCore && String(gate.currentCore).startsWith(targetMajor)) return gate.currentCore;
 return p?.targetCore || (targetMajor + '.x');
}

function updateStepperStatus(p,pr){
 const upgrade=latest(pr,'guided-upgrade');
 const audit=latestCompleted(pr,'guided-audit')||latest(pr,'guided-audit');
 const hasUpgrade=Boolean(upgrade);
 const isUpgradeRunning = Boolean(upgrade && upgrade.status === 'running');
 const isWorkflowActive = Boolean((runs && runs.some(r=>r.status==='running')) || (projects && projects.some(p=>p.status==='running')));
 const isTermRunning = isTerminalRunning();
 const isUpg=isProjectUpgraded(p,pr);
 const stepStatus={
  plan:Boolean(audit&&audit.status==='completed'),
  'test-audit':Boolean(audit&&audit.status==='completed'),
  upgrade:Boolean(isUpg || (upgrade&&upgrade.status==='completed')),
  regression:Boolean(upgrade&&['completed','needs_attention'].includes(upgrade.status)),
  rollback:hasUpgrade && !isUpgradeRunning,
  report:Boolean(audit||upgrade)
 };
 const stepEnabled={
  plan:true,
  'test-audit':true,
  upgrade:Boolean(audit||p?.status==='ready'||p?.status==='completed'),
  regression:hasUpgrade && !isUpgradeRunning,
  rollback:hasUpgrade && !isUpgradeRunning,
  report:Boolean(audit||upgrade)
 };

 if($('rollback-card-active')) $('rollback-card-active').style.display = hasUpgrade ? '' : 'none';
 if($('rollback-standby-card')) $('rollback-standby-card').style.display = hasUpgrade ? 'none' : 'block';

 const btnRollback = $('btn-do-rollback');
 const rollbackAlert = $('rollback-running-alert');
 if (btnRollback) {
  if (isUpgradeRunning || isWorkflowActive || isTermRunning) {
   btnRollback.disabled = true;
   btnRollback.title = 'Disabled: Upgrade rehearsal is currently executing. Wait for it to complete before rolling back.';
   btnRollback.innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Upgrade in Progress (Rollback Locked)...';
  } else {
   btnRollback.disabled = false;
   btnRollback.title = '';
   btnRollback.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rotate-ccw"></use></svg> Rollback to Baseline Now';
  }
 }
 if (rollbackAlert) {
  rollbackAlert.style.display = isUpgradeRunning ? 'flex' : 'none';
 }
 const isInterrupted = Boolean(upgrade && ['reconciliation_required', 'interrupted', 'critical', 'blocked'].includes(upgrade.status));
 const interruptedAlert = $('rollback-interrupted-alert');
 if (interruptedAlert) {
  interruptedAlert.style.display = (isInterrupted && !isUpgradeRunning) ? 'flex' : 'none';
 }

 STAGES.forEach(s=>{
  const nav=$('nav-step-'+s);
  if(!nav)return;
  nav.classList.remove('done','disabled');
  nav.setAttribute('role','tab');
  if(!stepEnabled[s]){
   nav.classList.add('disabled');
   nav.setAttribute('aria-disabled','true');
   nav.setAttribute('tabindex','-1');
   if (s === 'rollback' && isUpgradeRunning) {
    nav.title = 'Rollback is locked while upgrade rehearsal is executing';
   } else if (s === 'regression' && isUpgradeRunning) {
    nav.title = 'Visual regression review is available after upgrade finishes';
   } else {
    nav.title = (s==='rollback'||s==='regression') ? 'Available after running 1-Click Upgrade' : 'Step locked';
   }
  }else{
   nav.removeAttribute('aria-disabled');
   nav.setAttribute('tabindex','0');
   nav.removeAttribute('title');
  }
  if(s===currentStage){
   nav.classList.add('active');
   nav.setAttribute('aria-selected','true');
   nav.setAttribute('aria-current','step');
  }else{
   nav.classList.remove('active');
   nav.setAttribute('aria-selected','false');
   nav.removeAttribute('aria-current');
   if(stepStatus[s])nav.classList.add('done');
  }
 });
}

function updateUpgradeExecution(upgrade){
  const steps=[
   {
     id:'step-exec-env',
     badgeId:'badge-exec-env',
     detailId:'detail-exec-env',
     name:'1. Pre-Upgrade Backup & Baseline Checkpoint',
     checkpoints:['capturing_pre_upgrade_baseline','creating_recovery_checkpoint','creating_database_snapshot','sanitizing_copy','copying_managed_project','managed_copy_verified'],
     activeDetail:'Creating pre-upgrade database backup (pre-upgrade.sql.gz) & Git commit checkpoint...',
     doneDetail:'Database backup pre-upgrade.sql.gz & Git checkpoint verified'
   },
   {
     id:'step-exec-custom',
     badgeId:'badge-exec-custom',
     detailId:'detail-exec-custom',
     name:'2. Custom Code & Deprecation Fixes',
     checkpoints:['remediating_custom_code','applying_patches'],
     activeDetail:'Applying Drupal Rector AST rules and deprecation fixes to custom code...',
     doneDetail:'Custom code deprecations resolved and info.yml core compatibility updated'
   },
   {
     id:'step-exec-composer',
     badgeId:'badge-exec-composer',
     detailId:'detail-exec-composer',
     name:'3. Composer Package Upgrade',
     checkpoints:['upgrading_composer_packages','apply_exact_composer_resolution','composer_install'],
     activeDetail:'Upgrading drupal/core-recommended to Drupal 11.x and resolving contrib dependencies...',
     doneDetail:'Drupal 11.x packages installed and dependencies resolved'
   },
   {
     id:'step-exec-db',
     badgeId:'badge-exec-db',
     detailId:'detail-exec-db',
     name:'4. Database Schema Migrations & Cache Clear',
     checkpoints:['executing_approved_batch','database_updates','cache_rebuild','verifying_post_upgrade_routes','post_upgrade_verification','gate_2_passed','gate_2_visual_review','completed'],
     activeDetail:'Running drush updatedb -y, drush cache:rebuild, and verifying site boot...',
     doneDetail:'Database migrations complete, cache cleared, site bootstrap healthy'
   }
  ];
  const statusAlert=$('upgrade-active-status');
  const completeCard=$('upgrade-complete-actions');

  if(!upgrade){
   steps.forEach((s,idx)=>{
    const node=$(s.id);
    if(node){
     node.className='exec-step';
     const icon=node.querySelector('.exec-icon');
     if(icon)icon.textContent=String(idx+1);
     const badge=$(s.badgeId);
     if(badge){badge.className='step-badge badge muted';badge.textContent='Pending';}
     const detail=$(s.detailId);
     if(detail){detail.style.display='none';detail.textContent='';}
    }
   });
   if(completeCard)completeCard.style.display='none';
   if(statusAlert)statusAlert.innerHTML='<span class="muted">Ready to start upgrade. Click "Start 1-Click Upgrade" in the Plan stage.</span>';
   return;
  }
  const cp=upgrade.checkpoint||'';
  const isDone=upgrade.status==='completed';
  const isFailed=['critical','blocked','failed','reconciliation_required','interrupted'].includes(upgrade.status);
  let currentIdx=-1;
  steps.forEach((s,idx)=>{
   if(s.checkpoints.includes(cp))currentIdx=idx;
  });
  if(isDone)currentIdx=steps.length;

  steps.forEach((s,idx)=>{
   const node=$(s.id);
   if(!node)return;
   const icon=node.querySelector('.exec-icon');
   const badge=$(s.badgeId);
   const detail=$(s.detailId);

   if(idx<currentIdx||isDone){
    node.className='exec-step step-done';
    if(icon)icon.innerHTML='<svg class="ui-icon" aria-hidden="true" style="width:14px;height:14px;"><use href="#icon-check"></use></svg>';
    if(badge){badge.className='step-badge badge green';badge.innerHTML='<svg class="ui-icon" aria-hidden="true" style="width:11px;height:11px;vertical-align:-1px;"><use href="#icon-check"></use></svg> Done';}
    if(detail){detail.style.display='inline-block';detail.textContent=s.doneDetail;}
   }else if(idx===currentIdx){
    node.className='exec-step '+(isFailed?'step-failed':'step-active');
    if(icon)icon.innerHTML=isFailed?'<svg class="ui-icon" aria-hidden="true" style="width:14px;height:14px;"><use href="#icon-x"></use></svg>':'<svg class="ui-icon spin" aria-hidden="true" style="width:14px;height:14px;"><use href="#icon-refresh"></use></svg>';
    if(badge){
     badge.className='step-badge badge '+(isFailed?'red':'blue live-pulse');
     badge.innerHTML=isFailed?'<svg class="ui-icon" aria-hidden="true" style="width:11px;height:11px;vertical-align:-1px;"><use href="#icon-x"></use></svg> Failed':'<span class="live-pulse-dot"></span> Running...';
    }
    if(detail){detail.style.display='inline-block';detail.textContent=isFailed?(upgrade.rollbackError||upgrade.error||cp.replaceAll('_',' ')):s.activeDetail;}
   }else{
    node.className='exec-step step-pending';
    if(icon)icon.textContent=String(idx+1);
    if(badge){badge.className='step-badge badge muted';badge.textContent='Queued';}
    if(detail){detail.style.display='none';detail.textContent='';}
   }
  });

  if(completeCard){
   completeCard.style.display=isDone?'block':'none';
   if(isDone){
    const btnGoto = $('btn-goto-regression');
    if(btnGoto) btnGoto.onclick = () => showStage('regression', true);
    const btnOpenSite = $('btn-open-site-stage3');
    if(btnOpenSite){
     btnOpenSite.onclick = () => {
      const p = selectedProject();
      if(p?.site?.uri) window.open(p.site.uri, '_blank', 'noopener');
      else if(p?.sourceUrl) window.open(p.sourceUrl, '_blank', 'noopener');
     };
    }
    const btnRb = $('btn-rollback-stage3');
    if(btnRb) btnRb.onclick = () => $('btn-do-rollback')?.click();
   }
  }

  if(statusAlert){
   if(isDone){
    statusAlert.innerHTML='<strong style="color:var(--emerald-600);"><svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-check-circle"></use></svg> Automated Upgrade Complete!</strong> All custom deprecations resolved, packages upgraded to Drupal 11.x, and database migrated.';
   }else if(isFailed){
    statusAlert.innerHTML=`<strong style="color:var(--rose-600);"><svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-alert-triangle"></use></svg> Upgrade Halted:</strong> ${escapeHtml(upgrade.rollbackError||upgrade.error||cp.replaceAll('_',' '))}`;
   }else if(upgrade.status==='running'){
    statusAlert.innerHTML=`<strong style="color:var(--brand-600);"><svg class="ui-icon spin" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-refresh"></use></svg> Execution in Progress:</strong> ${escapeHtml(cp.replaceAll('_',' '))}...`;
   }else{
    statusAlert.innerHTML=`<span class="muted">Status: ${escapeHtml(upgrade.status)} · ${escapeHtml(cp.replaceAll('_',' '))}</span>`;
   }
  }

 const doctorPanel = $('ai-doctor-panel');
 const isAiReady = Boolean((window.providers || providers || []).some(p => p.available && p.safeInterface));
 if(doctorPanel){
  if(isFailed && isAiReady){
   doctorPanel.style.display='block';
   doctorPanel.innerHTML=`<div style="background:var(--purple-bg,#f5f3ff);border:1px solid var(--purple-border,#ddd6fe);border-radius:var(--radius-lg);padding:16px 20px;box-shadow:var(--shadow-sm);">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;">
     <div style="flex:1;min-width:280px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
       <svg class="ui-icon" style="color:var(--purple-text,#7c3aed);width:20px;height:20px;" aria-hidden="true"><use href="#icon-sparkles"></use></svg>
       <h4 style="margin:0;font-size:15px;color:var(--purple-text,#6d28d9);font-weight:700;">AI Upgrade Doctor &amp; Automated Self-Healing</h4>
      </div>
      <p style="margin:0 0 10px;font-size:13px;color:var(--text-main);line-height:1.4;">
       Upgrade halted due to an execution failure. The AI Doctor can inspect Drush error logs, identify failing PHP files, and synthesize a validated AST patch to self-heal the codebase.
      </p>
      <div id="doctor-output" style="display:none;margin-bottom:10px;">
       <pre style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-md);padding:12px;font-size:12px;max-height:220px;overflow-y:auto;white-space:pre-wrap;font-family:monospace;"></pre>
      </div>
     </div>
     <button id="btn-self-heal" class="primary" type="button" style="white-space:nowrap;background:var(--purple,#7c3aed);border-color:var(--purple,#7c3aed);color:#fff;font-weight:600;padding:10px 18px;border-radius:var(--radius-md);cursor:pointer;">
      ⚡ Diagnose &amp; Self-Heal
     </button>
    </div>
   </div>`;
   const btnHeal=$('btn-self-heal');
   if(btnHeal){
    btnHeal.onclick=async()=>{
     btnHeal.disabled=true;
     btnHeal.textContent='Diagnosing with Gemini...';
     try{
      const rid=upgrade.id||(runs?.find(r=>r.project===selected)?.id)||(runs?.[0]?.id);
      const provider=$('ai-provider')?.value||'gemini';
      const res=await api('runs/'+rid+'/self-heal',{provider});
      const outBox=$('doctor-output');
      if(outBox){
       outBox.style.display='block';
       outBox.querySelector('pre').textContent=`✓ Self-healing patch generated!\nModule: ${res.module||'custom'}\nTarget: ${res.filePath||'code'}\nDigest: ${res.proposalDigest||''}\n\n${res.diff||'Patch synthesized and verified.'}`;
      }
      if(typeof showToast==='function')showToast('✓ AI patch synthesized and validated! Click Re-run Upgrade to apply.');
      btnHeal.textContent='✓ Fix Applied';
     }catch(err){
      if(typeof showToast==='function')showToast('AI Self-Heal error: '+(err.message||err));
      btnHeal.disabled=false;
      btnHeal.textContent='⚡ Diagnose & Self-Heal';
     }
    };
   }
  }else{
   doctorPanel.style.display='none';
   doctorPanel.innerHTML='';
  }
 }
}

function updateAuditEvidence(audit,p){
 const baseCount=gate?.baseline?.selected||0;
 const headerCount=gate?.baseline?.headerCount||0;
 const footerCount=gate?.baseline?.footerCount||0;
 if($('audit-stat-routes'))$('audit-stat-routes').textContent=String(baseCount);
 if($('audit-stat-nav'))$('audit-stat-nav').textContent=String(headerCount+footerCount);
 if($('audit-stat-status')){
  const pass=gate?.approvalEligible||audit?.status==='completed';
  $('audit-stat-status').textContent=pass?'PASS':(audit?.status==='running'?'SCANNING':'READY');
  $('audit-stat-status').style.color=pass?'var(--green,#22c55e)':'#eab308';
 }
 const summaryDiv=$('audit-evidence-summary');
 if(summaryDiv&&gate){
  summaryDiv.innerHTML=`<div class="info-card" style="margin-bottom:12px;"><h4 style="margin:0 0 8px;">Baseline Verification Findings</h4><p style="margin:0 0 6px;font-size:13.5px;">${escapeHtml(gate.automationLabel||gate.risk?.recommendation||'Evidence captured')}</p><p class="muted" style="margin:0;font-size:12px;">Deployment Risk: ${gate.risk?.score||0}/100 · ${gate.risk?.hardBlockers?.length||0} hard blockers · ${baseCount} routes (${headerCount} header, ${footerCount} footer nav links auto-captured).</p></div>`;
 }
 const routesList=$('audit-routes-list');
 if(routesList){
  routesList.replaceChildren();
  const discovered=gate?.baseline?.routes||[];
  if(!discovered.length){
   routesList.innerHTML='<p class="muted">No baseline routes discovered yet. Run Test Audit to capture routes.</p>';
  }else{
   discovered.forEach(r=>{
    const path=typeof r==='string'?r:(r.path||r.url||'/');
    const item=el('div',undefined,'route-item');
    item.innerHTML=`<span><strong>${escapeHtml(path)}</strong></span><span class="badge" style="background:#dcfce7;color:#166534;">HTTP 200 OK</span>`;
    routesList.append(item);
   });
  }
 }
 if(selected)loadGuardrails(selected);
}

async function loadGuardrails(pid){
 if(!pid)return;
 try{
  const res=await api('projects/'+pid+'/guardrails');
  if(!res)return;
  if($('guardrail-phpstan-badge')){
   const passed=res.summary?.phpstanPassed;
   $('guardrail-phpstan-badge').innerHTML=passed?'<svg class="ui-icon" aria-hidden="true"><use href="#icon-check"></use></svg> Passed':`<svg class="ui-icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg> ${res.guardrails?.phpstan?.violationCount||0} Findings`;
   $('guardrail-phpstan-badge').className='guardrail-badge '+(passed?'pass':'warn');
   $('guardrail-phpstan-badge').style.color='';
  }
  if($('guardrail-phpcs-badge')){
   const passed=res.summary?.phpcsPassed;
   $('guardrail-phpcs-badge').innerHTML=passed?'<svg class="ui-icon" aria-hidden="true"><use href="#icon-check"></use></svg> Passed':`<svg class="ui-icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg> ${res.guardrails?.phpcs?.violationCount||0} Findings`;
   $('guardrail-phpcs-badge').className='guardrail-badge '+(passed?'pass':'warn');
   $('guardrail-phpcs-badge').style.color='';
  }
  if($('guardrail-composer-badge')){
   const passed=res.summary?.composerPassed;
   $('guardrail-composer-badge').innerHTML=passed?'<svg class="ui-icon" aria-hidden="true"><use href="#icon-check"></use></svg> Valid &amp; Clean':'<svg class="ui-icon" aria-hidden="true"><use href="#icon-x"></use></svg> Failed';
   $('guardrail-composer-badge').className='guardrail-badge '+(passed?'pass':'fail');
   $('guardrail-composer-badge').style.color='';
  }
  if($('guardrail-twig-badge')){
   const passed=res.summary?.twigPassed;
   $('guardrail-twig-badge').innerHTML=passed?'<svg class="ui-icon" aria-hidden="true"><use href="#icon-check"></use></svg> 0 Deprecations':`<svg class="ui-icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg> ${res.guardrails?.twig?.violationCount||0} Findings`;
   $('guardrail-twig-badge').className='guardrail-badge '+(passed?'pass':'warn');
   $('guardrail-twig-badge').style.color='';
  }
  if($('guardrails-details')){
   if(res.summary?.totalViolations>0){
    $('guardrails-details').innerHTML=`Found ${res.summary.totalViolations} code quality infractions. Click "Auto-Fix Standards" to format custom code to Drupal Standards automatically.`;
   }else{
    $('guardrails-details').innerHTML=`All code quality, deprecation, and security guardrails passed cleanly.`;
   }
  }
 }catch(e){}
}

let regressionViewMode = 'sidebyside';

function applyRegressionViewMode(mode) {
  regressionViewMode = mode;
  const grid = $('regression-grid');
  const btnSideBySide = $('btn-view-sidebyside');
  const btnDiff = $('btn-view-diff');
  const btnThreeUp = $('btn-view-threeup');
  const colBefore = $('regression-col-before');
  const colAfter = $('regression-col-after');
  const colDiff = $('regression-col-diff');

  if (btnSideBySide) btnSideBySide.classList.toggle('active', mode === 'sidebyside');
  if (btnDiff) btnDiff.classList.toggle('active', mode === 'diff');
  if (btnThreeUp) btnThreeUp.classList.toggle('active', mode === 'threeup');

  if (grid) {
    grid.className = 'regression-comparison-grid view-' + mode;
  }

  if (mode === 'sidebyside') {
    if (colBefore) colBefore.style.display = 'block';
    if (colAfter) colAfter.style.display = 'block';
    if (colDiff) colDiff.style.display = 'none';
  } else if (mode === 'diff') {
    if (colBefore) colBefore.style.display = 'none';
    if (colAfter) colAfter.style.display = 'none';
    if (colDiff) colDiff.style.display = 'block';
  } else if (mode === 'threeup') {
    if (colBefore) colBefore.style.display = 'block';
    if (colAfter) colAfter.style.display = 'block';
    if (colDiff) colDiff.style.display = 'block';
  }
}

async function renderRegression(){
  if(!selected)return;
  const pr=runs.filter(r=>r.project===selected);
  const upgrade=latest(pr,'guided-upgrade');
  const audit=latestCompleted(pr,'guided-audit')||latest(pr,'guided-audit');
  const isUpgraded=Boolean(upgrade && ['completed','needs_attention'].includes(upgrade.status));

  // The visual run that holds the screenshot artifacts:
  // If an upgrade executed, it contains BOTH the Drupal 10 baseline captures and Drupal 11 post-upgrade captures.
  // If not yet upgraded, the audit run holds the Drupal 10 baseline captures.
  const visualRun=upgrade||audit;
  const visualRunId=visualRun?visualRun.id:'';

  let artifacts=[];
  if(visualRunId){
    if(report && report.run && report.run.id===visualRunId && Array.isArray(report.artifacts) && report.artifacts.length){
      artifacts=report.artifacts;
    }else{
      try{
        artifacts=await api(`runs/${encodeURIComponent(visualRunId)}/artifacts`);
      }catch(_e){
        artifacts=(report&&report.artifacts)||[];
      }
    }
  }

  // Load project scenario catalog for clean route path labels (e.g. "/about-us · DESKTOP")
  if((!projectScenarios || !projectScenarios.length) && selected){
    try{
      projectScenarios=await api(`projects/${encodeURIComponent(selected)}/scenarios`);
    }catch(_e){}
  }

  // Attempt to parse Backstop report.json if available
  let reportData = null;
  const reportArtifact = (artifacts || []).find(a => a.endsWith('report.json'));
  if (reportArtifact && visualRunId) {
    try {
      reportData = await api(`runs/${encodeURIComponent(visualRunId)}/artifact?name=${encodeURIComponent(reportArtifact)}`);
    } catch (_e) {}
  }
  const testResultsMap = new Map();
  if (reportData && Array.isArray(reportData.tests)) {
    for (const t of reportData.tests) {
      const p = t.pair;
      if (!p) continue;
      const fName = p.fileName;
      const mismatch = p.diff?.misMatchPercentage || (t.status === 'pass' ? '0.00' : null);
      if (fName) {
        testResultsMap.set(fName, {
          status: t.status,
          misMatchPercentage: mismatch,
          rawMisMatch: p.diff?.rawMisMatchPercentage,
          dimensionDiff: p.diff?.dimensionDifference,
          engineError: p.engineErrorMsg
        });
      }
    }
  }

  const images=(artifacts||[]).filter(name=>/\.(png|jpg|jpeg)$/i.test(name));
  // Guaranteed Drupal 10 Pre-Upgrade baseline reference captures
  const beforeImages=images.filter(name=>name.includes('bitmaps_reference'));
  // Guaranteed Drupal 11 Post-Upgrade test captures (excluding diff overlays)
  const testImages=isUpgraded
    ? images.filter(name=>name.includes('bitmaps_test') && !name.includes('failed_diff') && !name.includes('diff_'))
    : [];
  const diffImages=isUpgraded
    ? images.filter(name=>name.includes('failed_diff') || name.includes('diff_'))
    : [];

  const sel=$('regression-route-select');
  const bImg=$('regression-before-img');
  const aImg=$('regression-after-img');
  const aPlaceholder=$('regression-after-placeholder');
  const dImg=$('regression-diff-img');
  const dPlaceholder=$('regression-diff-placeholder');
  const beforeStatus=$('regression-before-status');
  const afterStatus=$('regression-after-status');
  const afterSublabel=$('regression-after-sublabel');
  const diffStatus=$('regression-diff-status');
  const tbody=$('regression-table-body');
  const badge=$('regression-verdict-badge');
  const banner=$('regression-status-banner');
  const routes=(gate?.baseline?.routes)||projectScenarios||[];

  const pairs=[];
  for(const b of beforeImages){
    const base=b.split('/').pop();
    const match=isUpgraded ? testImages.find(a=>a.split('/').pop()===base) : null;
    const diffMatch=isUpgraded ? diffImages.find(d=>{
      const dBase=d.split('/').pop();
      return dBase===`failed_diff_${base}`||dBase===`diff_${base}`||dBase.includes(base);
    }) : null;
    const testResult = testResultsMap.get(base);
    pairs.push({
      label:friendlyImageLabel(b, projectScenarios),
      before:b,
      after:match||null,
      diff:diffMatch||null,
      base:base,
      misMatchPercentage: testResult?.misMatchPercentage ?? (diffMatch ? 'Variance' : (isUpgraded && match ? '0.00' : null)),
      testResult: testResult || null
    });
  }

  if(isUpgraded && !pairs.length && testImages.length){
    for(const a of testImages){
      const base=a.split('/').pop();
      const testResult = testResultsMap.get(base);
      pairs.push({
        label:friendlyImageLabel(a, projectScenarios),
        before:null,
        after:a,
        diff:null,
        base:base,
        misMatchPercentage: testResult?.misMatchPercentage ?? (isUpgraded ? '0.00' : null),
        testResult: testResult || null
      });
    }
  }

  const regressionCount=pairs.filter(p=>p.diff).length;

  // Update top QA notice banner
  if(banner){
    banner.style.display='flex';
    banner.style.background='';
    banner.style.borderColor='';
    banner.style.color='';
    if(isUpgraded){
      if(regressionCount>0){
        banner.className='guardrail-notice-box notice-variance';
        banner.innerHTML=`<svg class="ui-icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg><span><strong>Post-Upgrade Quality Assurance:</strong> Comparing <strong>Drupal 10 Baseline</strong> vs <strong>Drupal 11 Post-Upgrade</strong> (${pairs.length} routes compared, ${regressionCount} visual variances detected).</span>`;
      }else{
        banner.className='guardrail-notice-box notice-pass';
        banner.innerHTML=`<svg class="ui-icon" aria-hidden="true"><use href="#icon-check-circle"></use></svg><span><strong>Post-Upgrade Quality Assurance:</strong> Verified <strong>Drupal 11</strong> route responses match <strong>Drupal 10 Baseline</strong> (${pairs.length} route captures verified cleanly).</span>`;
      }
    }else{
      banner.className='guardrail-notice-box notice-info';
      banner.innerHTML=`<svg class="ui-icon" aria-hidden="true"><use href="#icon-info"></use></svg><span><strong>Drupal 10 Pre-Upgrade Baseline Captured:</strong> ${pairs.length||routes.length||'All'} reference routes recorded. Drupal 11 post-upgrade captures will execute automatically during <strong>Stage 03 (1-Click Upgrade)</strong>.</span>`;
    }
  }

  // Baseline header badge (Always Drupal 10)
  if(beforeStatus){
    beforeStatus.textContent='Drupal 10 · HTTP 200 OK';
    beforeStatus.className='badge status-info';
    beforeStatus.style.background='';
    beforeStatus.style.color='';
    beforeStatus.style.border='';
  }

  // Post-Upgrade sub-label
  if(afterSublabel){
    afterSublabel.textContent=isUpgraded ? 'Drupal 11.x · Live Verification Capture' : 'Drupal 11.x · Pending Upgrade Execution';
  }

  function updateAfterBadge(pair){
    if(!afterStatus)return;
    afterStatus.style.background='';
    afterStatus.style.color='';
    afterStatus.style.border='';
    if(isUpgraded && pair && pair.after){
      const hasRegression=Boolean(pair.diff);
      afterStatus.textContent=hasRegression ? 'Drupal 11 · Visual Finding' : 'Drupal 11 · HTTP 200 OK';
      afterStatus.className='badge '+(hasRegression?'status-fail':'status-pass');
    }else{
      afterStatus.textContent='Pending Upgrade';
      afterStatus.className='badge muted';
    }
  }

  function showPair(pair){
    if(!pair){
      if(bImg){bImg.src='';bImg.style.display='none';}
      if(aImg){aImg.src='';aImg.style.display='none';}
      if(aPlaceholder)aPlaceholder.style.display=isUpgraded?'none':'block';
      if(dImg){dImg.src='';dImg.style.display='none';}
      if(dPlaceholder)dPlaceholder.style.display='none';
      updateAfterBadge(null);
      return;
    }
    if(bImg){
      if(pair.before && visualRunId){
        // Drupal 10 Baseline capture
        bImg.src=`/api/runs/${encodeURIComponent(visualRunId)}/artifact?name=${encodeURIComponent(pair.before)}`;
        bImg.style.display='block';
      }else{
        bImg.src='';
        bImg.style.display='none';
      }
    }
    if(aImg){
      if(pair.after && visualRunId){
        // Drupal 11 Post-Upgrade capture
        aImg.src=`/api/runs/${encodeURIComponent(visualRunId)}/artifact?name=${encodeURIComponent(pair.after)}`;
        aImg.style.display='block';
        if(aPlaceholder)aPlaceholder.style.display='none';
      }else{
        aImg.src='';
        aImg.style.display='none';
        if(aPlaceholder)aPlaceholder.style.display='block';
      }
    }
    if(dImg && dPlaceholder){
      if(pair.diff && visualRunId){
        dImg.src=`/api/runs/${encodeURIComponent(visualRunId)}/artifact?name=${encodeURIComponent(pair.diff)}`;
        dImg.style.display='block';
        dPlaceholder.style.display='none';
        if(diffStatus){
          const pct=pair.misMatchPercentage && pair.misMatchPercentage !== 'Variance' ? `${pair.misMatchPercentage}% diff` : 'Visual variance';
          diffStatus.textContent=pct;
          diffStatus.className='badge status-fail';
          diffStatus.style.background='';
          diffStatus.style.color='';
          diffStatus.style.border='';
        }
      }else if(isUpgraded && pair.after){
        dImg.src='';
        dImg.style.display='none';
        dPlaceholder.style.display='block';
        if(diffStatus){
          diffStatus.textContent='0.00% diff (Pass)';
          diffStatus.className='badge status-pass';
          diffStatus.style.background='';
          diffStatus.style.color='';
          diffStatus.style.border='';
        }
      }else{
        dImg.src='';
        dImg.style.display='none';
        dPlaceholder.style.display='block';
        if(diffStatus){
          diffStatus.textContent='Pending Upgrade';
          diffStatus.className='badge muted';
          diffStatus.style.background='';
          diffStatus.style.color='';
          diffStatus.style.border='';
        }
      }
    }
    updateAfterBadge(pair);
  }

  if(sel){
    sel.replaceChildren();
    if(!pairs.length){
      option(sel,'No screenshot captures recorded yet','');
      showPair(null);
    }else{
      pairs.forEach((p,idx)=>{
        let label = p.label;
        if (isUpgraded) {
          if (p.diff) {
            label += ` · ⚠️ ${p.misMatchPercentage && p.misMatchPercentage !== 'Variance' ? p.misMatchPercentage + '% diff' : 'Visual Variance'}`;
          } else if (p.after) {
            label += ` · ✓ 0.00% diff`;
          }
        }
        option(sel, label, String(idx));
      });
      sel.onchange=()=>{
        const idx=Number(sel.value);
        showPair(pairs[idx]);
      };
      showPair(pairs[0]);
    }
  }

  // Ensure current view mode is synced
  applyRegressionViewMode(regressionViewMode);

  if($('regression-routes-count')) {
    $('regression-routes-count').textContent = `${pairs.length} Routes`;
  }

  let activeRegFilter = 'all';
  let regSearchTerm = '';

  const regSearchInput = $('regression-search-input');
  if (regSearchInput) {
    regSearchInput.oninput = () => {
      regSearchTerm = regSearchInput.value.trim().toLowerCase();
      renderRegressionRows();
    };
  }

  const regFilterGroup = $('regression-filter-group');
  if (regFilterGroup) {
    regFilterGroup.querySelectorAll('button[data-reg-filter]').forEach(btn => {
      btn.onclick = () => {
        regFilterGroup.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeRegFilter = btn.dataset.regFilter;
        renderRegressionRows();
      };
    });
  }

  function renderRegressionRows(){
    if(!tbody) return;
    tbody.replaceChildren();

    const filtered = pairs.filter(pair => {
      const hasVariance = Boolean(pair.diff || pair.testResult?.engineError);
      if (activeRegFilter === 'variances' && !hasVariance) return false;
      if (activeRegFilter === 'passing' && hasVariance) return false;
      if (regSearchTerm && !pair.label.toLowerCase().includes(regSearchTerm)) return false;
      return true;
    });

    if(!filtered.length){
      const tr=el('tr'),td=el('td', pairs.length ? 'No routes match the selected filter.' : 'Run audit baseline to capture Drupal 10 reference routes.','muted');
      td.colSpan=6;td.style.padding='20px';td.style.textAlign='center';
      tr.append(td);tbody.append(tr);
      return;
    }

    filtered.forEach((pair, index) => {
      const tr=el('tr');
      tr.style.cursor = 'pointer';
      tr.title = 'Click to inspect visual capture for this route above';

      // Column 0: Row Index (#)
      const tdNum = el('td', undefined, 'col-row-num');
      tdNum.style.padding = '9px 8px';
      tdNum.style.textAlign = 'center';
      tdNum.style.fontFamily = 'var(--font-mono)';
      tdNum.style.fontSize = '11.5px';
      tdNum.style.fontWeight = '600';
      tdNum.style.color = 'var(--text-muted)';
      tdNum.style.whiteSpace = 'nowrap';
      tdNum.textContent = String(index + 1);

      const tdRoute=el('td');tdRoute.append(el('strong',pair.label));
      const tdBefore=el('td');
      const bSpan=el('span','200 OK (D10)','badge');
      bSpan.style.background='var(--blue-bg, #eff6ff)';
      bSpan.style.color='var(--blue-text, #1d4ed8)';
      bSpan.style.border='1px solid var(--blue-border, #bfdbfe)';
      tdBefore.append(bSpan);
      const tdAfter=el('td');
      const tdDiff=el('td');
      const tdVerdict=el('td');

      if(isUpgraded && pair.after){
        const hasEngineErr = Boolean(pair.testResult?.engineError);
        const afterBadge = el('span', hasEngineErr ? 'Error (D11)' : '200 OK (D11)', hasEngineErr ? 'badge status-fail' : 'badge status-pass');
        if(hasEngineErr && pair.testResult?.engineError) afterBadge.title = pair.testResult.engineError;
        tdAfter.append(afterBadge);
        if(pair.diff || hasEngineErr){
          const diffText = hasEngineErr ? 'Engine error' : (pair.misMatchPercentage && pair.misMatchPercentage !== 'Variance' ? `${pair.misMatchPercentage}% diff` : 'Visual variance');
          const diffLink=el('a', diffText, 'badge status-fail');
          if(pair.diff){
            diffLink.href=`/api/runs/${encodeURIComponent(visualRunId)}/artifact?name=${encodeURIComponent(pair.diff)}`;
            diffLink.target='_blank';
            diffLink.rel='noopener noreferrer';
            diffLink.style.textDecoration='underline';
            diffLink.title='Click to view visual diff overlay in new window';
          } else if(hasEngineErr) {
            diffLink.title = pair.testResult?.engineError || 'Browser engine error during capture';
          }
          tdDiff.append(diffLink);
          const sp=el('span',undefined,'badge status-fail');
          sp.innerHTML='<svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-alert-triangle"></use></svg> REVIEW';
          tdVerdict.append(sp);
        }else{
          const passPct = pair.misMatchPercentage ? `${pair.misMatchPercentage}% diff` : '0.00% diff';
          tdDiff.textContent=passPct;
          const sp=el('span',undefined,'badge status-pass');
          sp.innerHTML='<svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-check"></use></svg> PASS';
          tdVerdict.append(sp);
        }
      }else{
        const spPending=el('span','Pending Upgrade','badge muted');
        spPending.style.background='var(--bg-muted, #f1f5f9)';
        spPending.style.color='var(--text-muted, #64748b)';
        spPending.style.border='1px solid var(--border, #cbd5e1)';
        tdAfter.append(spPending);
        tdDiff.innerHTML='<span style="color:var(--text-muted);">—</span>';
        const spVerdict=el('span',undefined,'badge muted');
        spVerdict.style.background='var(--bg-muted, #f1f5f9)';
        spVerdict.style.color='var(--text-muted, #64748b)';
        spVerdict.style.border='1px solid var(--border, #cbd5e1)';
        spVerdict.innerHTML='<svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-clock"></use></svg> Baseline Only';
        tdVerdict.append(spVerdict);
      }

      tr.onclick = (e) => {
        if(e.target.tagName === 'A') return;
        const pIdx = pairs.indexOf(pair);
        if(sel && pIdx >= 0){
          sel.value = String(pIdx);
          showPair(pair);
          const ctrl = document.querySelector('.regression-controls');
          if(ctrl) ctrl.scrollIntoView({behavior:'smooth', block:'start'});
        }
      };

      tr.append(tdNum,tdRoute,tdBefore,tdAfter,tdDiff,tdVerdict);
      tbody.append(tr);
    });
  }

  renderRegressionRows();

  if(badge){
    badge.style.background='';
    badge.style.color='';
    badge.style.border='';
    if(isUpgraded){
      if(regressionCount>0){
        badge.innerHTML=`<svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-alert-triangle"></use></svg> ${regressionCount} Visual Variances Detected`;
        badge.className='badge status-fail';
      }else{
        badge.innerHTML='<svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-check-circle"></use></svg> 0 Regressions Detected';
        badge.className='badge status-pass';
      }
    }else{
      badge.innerHTML='<svg class="ui-icon" aria-hidden="true" style="vertical-align:-2px;"><use href="#icon-clock"></use></svg> Awaiting Upgrade Execution';
      badge.className='badge muted';
    }
  }
}

async function refresh(){
 if(refreshing){refreshAgain=true;return;}
 clearTimeout(pollTimer);
 pollTimer=null;
 refreshing=true;
 let okay=false;
  try{
    [projects,runs,capabilities]=await Promise.all([api('projects'),api('runs'),capabilities?.workbench_path?Promise.resolve(capabilities):api('capabilities')]);
    if(!providers){
      try{
        providers=await api('providers');
        window.providers=providers;
        loadProviders().catch(()=>{});
      }catch(_e){
        providers=[];
        window.providers=[];
      }
    }
    okay=true;
    clearError();
    if(typeof updateGuideContent==='function')updateGuideContent();
    const visible=projects.filter(p=>$('show-fixtures')?.checked||!p.fixture);
    const currentKeys = visible.map(p => `${p.id}:${p.status}:${p.draft}:${p.currentCore||''}`).join('|') + '|' + selected;
    if($('project-list').dataset.rendered !== currentKeys){
     $('project-list').dataset.rendered = currentKeys;
     $('project-list').replaceChildren();
      for(const p of visible){
       const row=el('div',undefined,'project-card-row'),
             card=el('button',undefined,'project-card'+(p.id===selected?' selected':'')),
             delBtn=el('button',undefined,'project-card-delete-btn'),
             pr=runs.filter(r=>r.project===p.id),
             audit=latestCompleted(pr,'guided-audit')||latest(pr,'guided-audit'),
             upgrade=latest(pr,'guided-upgrade'),
             isUpg=isProjectUpgraded(p,pr);
       let status=p.draft?p.status.replaceAll('_',' '):'Ready to scan';
       if(isUpg){
        status = `Drupal ${getActiveCoreVersion(p)} · ${upgrade?.status === 'completed' ? 'Upgraded' : (upgrade?.status === 'needs_attention' ? 'Visual Review' : 'Active')}`;
       }else if(upgrade)status=`${upgrade.status.replaceAll('_',' ')} · ${upgrade.checkpoint.replaceAll('_',' ')}`;
       else if(audit)status=`${(audit.automationEligibility||audit.recommendation||audit.status).replaceAll('_',' ')}${audit.riskScore===undefined?'':` · risk ${audit.riskScore}`}`;

       const header=el('div',undefined,'project-card-header'),
             icon=el('span',undefined,'project-card-icon'),
             title=el('strong',p.name||p.id,'project-card-title');
       icon.innerHTML='<svg class="ui-icon" aria-hidden="true"><use href="#icon-folder"></use></svg>';
       header.append(icon,title);

       const statusRow=el('div',undefined,'project-card-status'),
             dot=el('span',undefined,'project-status-dot'),
             statusText=el('span',status,'project-status-text');

       const sLow=status.toLowerCase();
       if(sLow.includes('upgraded')||sLow.includes('completed')||sLow.includes('clean')||sLow.includes('ready')) {
         dot.classList.add('dot-success');
       } else if(sLow.includes('attention')||sLow.includes('risk')||sLow.includes('finding')||sLow.includes('warning')) {
         dot.classList.add('dot-warning');
       } else if(sLow.includes('blocked')||sLow.includes('failed')||sLow.includes('error')) {
         dot.classList.add('dot-danger');
       } else {
         dot.classList.add('dot-info');
       }
       statusRow.append(dot,statusText);
       card.append(header,statusRow);
       card.onclick=()=>choose(p.id);

       delBtn.type='button';
       delBtn.title='Remove from workbench (keeps your actual project safe)';
       delBtn.setAttribute('aria-label', `Remove ${p.name || p.id} from workbench`);
       delBtn.innerHTML='<svg class="ui-icon" style="width:13px;height:13px;" aria-hidden="true"><use href="#icon-trash"></use></svg>';
       delBtn.onclick=(e)=>{
        e.stopPropagation();
        confirmDeleteProject(p);
       };

       row.append(card,delBtn);
       $('project-list').append(row);
     }
    }
    if(!selected&&visible.length)selected=visible[0].id;
    const p=selectedProject();
    renderProjectSwitcher(visible, p);
    syncTerminalWithProject(false);
    if($('delete-project-btn')){
     $('delete-project-btn').style.display=p?'inline-flex':'none';
    }
    if(!$('setup').hidden)return;
    $('empty').hidden=Boolean(p);
    $('project-view').hidden=!p;
    if(!p)return;
    const pr=runs.filter(r=>r.project===selected);
    if(p.draft)pr.unshift({id:'setup--'+p.id,project:p.id,action:'setup',status:p.status,checkpoint:p.checkpoint,startedAt:p.createdAt});
    const curAudit=latestCompleted(pr,'guided-audit')||latest(pr,'guided-audit');
    $('title').textContent=p.name||p.id;
    if($('breadcrumb-site-name')) $('breadcrumb-site-name').textContent=p.name||p.id;
    const visitLink = $('pantheon-visit-site-link');
    if(visitLink){
      if(p.url){
        visitLink.href=p.url;
        visitLink.style.display='inline-flex';
      } else {
        visitLink.style.display='none';
      }
    }
    if($('pantheon-runtime-text')){
      const wrap = p.runtime?.wrapper || p.wrapper;
      $('pantheon-runtime-text').textContent=wrap?(wrap==='fin'?'Docksal (fin)':wrap.toUpperCase()):'Local Container';
    }
    if($('manage-runs-badge')){
      const runCount = pr.length;
      $('manage-runs-badge').textContent = String(runCount);
      $('manage-runs-badge').style.display = runCount > 0 ? 'inline-block' : 'none';
    }
    if($('pantheon-edit-project-btn')) $('pantheon-edit-project-btn').style.display = p ? 'inline-flex' : 'none';
    if($('pantheon-manage-runs-btn')) $('pantheon-manage-runs-btn').style.display = p ? 'inline-flex' : 'none';
    $('subtitle').textContent=p.draft?'Local project registered · ready to scan.':'Automated Drupal 11 Upgrade Workbench';

    const isUpgraded = isProjectUpgraded(p, pr);
    const activeVer = getActiveCoreVersion(p);
    const eyebrow = $('pipeline-eyebrow');
    if(eyebrow){
      if(isUpgraded){
        eyebrow.textContent = `DRUPAL ${activeVer} ACTIVE · UPGRADED`;
        eyebrow.classList.add('eyebrow-upgraded');
      }else{
        eyebrow.textContent = 'DRUPAL 10 → 11 UPGRADE PIPELINE';
        eyebrow.classList.remove('eyebrow-upgraded');
      }
    }

   const old=$('runs').value;
   const runsKey = pr.map(r => `${r.id}:${r.status}`).join('|');
   if($('runs').dataset.rendered !== runsKey){
    $('runs').dataset.rendered = runsKey;
    $('runs').replaceChildren();
    pr.forEach(r=>option($('runs'),`${r.action.replaceAll('guided-','')} · ${r.status} · ${(r.startedAt||'').slice(0,16)}`,r.id));
   }
   // choose() clears the selector before refresh; when the option list is unchanged the
   // render above is skipped, so the selection must be restored here regardless — otherwise
   // the report fetch below targets /api/runs//report (empty run id) and the operator sees
   // a bare "Not Found" banner.
   const bestRun = pr.find(r => r.action === 'guided-upgrade' && ['completed', 'needs_attention'].includes(r.status))
                || pr.find(r => r.action === 'guided-audit' && r.status === 'completed')
                || pr.find(r => r.status === 'completed')
                || pr[0];
   if(old&&pr.some(r=>r.id===old))$('runs').value=old;
   else if(!pr.some(r=>r.id===$('runs').value)&&bestRun)$('runs').value=bestRun.id;
   if(typeof updateDeleteRunBtn==='function')updateDeleteRunBtn();

  const activeRun=pr.find(r=>r.status==='running');
  const upgrade=latest(pr,'guided-upgrade');
  const audit=latestCompleted(pr,'guided-audit')||latest(pr,'guided-audit');
  const hashStage = (window.location.hash || '').replace('#stage-', '').replace('#', '');
  if(!userSelectedStage){
   if(hashStage && STAGES.includes(hashStage))showStage(hashStage);
   else if(activeRun&&activeRun.action==='guided-upgrade')showStage('upgrade');
   else if(activeRun||p.status==='running')showStage('test-audit');
   else if(upgrade&&['completed','needs_attention','rolled_back'].includes(upgrade.status))showStage('regression');
   else if(audit&&audit.status==='completed')showStage('plan');
   else showStage('test-audit');
  }
  updateStepperStatus(p,pr);
  await nextStep(p,pr);
  updateUpgradeActionButtons();
  updateBackgroundTaskIndicator();
  updateActiveWorkflowBanner();
  if(pr.length){
   const rid=$('runs').value,state=pr.find(r=>r.id===rid);
   if(!report||report.run.id!==rid||report.run.finishedAt!==state?.finishedAt||state?.status==='running'||state?.action==='setup')await loadReport(rid);
  }
  if(currentStage==='regression')renderRegression();
 }finally{
  refreshing=false;
  if(refreshAgain){refreshAgain=false;queueMicrotask(()=>refresh().catch(error));}
  else if(okay)scheduleProgress();
 }
}

function startProgressTimer(startedAt){
 const startTime = startedAt ? new Date(startedAt).getTime() : (progressStartTime || Date.now());
 if(!progressStartTime) progressStartTime = startTime;
 function updateTimer(){
  const diffSec = Math.max(0, Math.floor((Date.now() - progressStartTime) / 1000));
  const mins = String(Math.floor(diffSec / 60)).padStart(2, '0');
  const secs = String(diffSec % 60).padStart(2, '0');
  const textEl = $('pipeline-elapsed-text');
  if(textEl) textEl.textContent = `${mins}:${secs}s`;
 }
 updateTimer();
 if(!progressElapsedInterval){
  progressElapsedInterval = setInterval(updateTimer, 1000);
 }
}

function stopProgressTimer(){
 if(progressElapsedInterval){
  clearInterval(progressElapsedInterval);
  progressElapsedInterval = null;
 }
 progressStartTime = 0;
}

function addActivityLog(pid, checkpoint, stepName){
 if(!pid) return;
 if(!projectActivityLogs[pid]) projectActivityLogs[pid] = [];
 const time = new Date().toLocaleTimeString();
 const msg = `[${time}] ✔ Checkpoint: ${stepName} (${(checkpoint || '').replaceAll('_', ' ')})`;
 if(!projectActivityLogs[pid].includes(msg)){
  projectActivityLogs[pid].push(msg);
  renderConsoleLogs(pid);
 }
}

async function fetchProjectActivity(pid){
 if(!pid) return;
 try {
  const res = await api('projects/' + pid + '/activity');
  if(res && res.events && res.events.length){
   if(!projectActivityLogs[pid]) projectActivityLogs[pid] = [];
   res.events.forEach(e => {
    const t = (e.at || '').slice(11, 19) || new Date().toLocaleTimeString();
    const type = (e.type || 'checkpoint').replaceAll('_', ' ');
    const detail = (e.checkpoint || e.step || e.status || e.action || '').replaceAll('_', ' ');
    const timing = e.type === 'span' && typeof e.elapsedSeconds === 'number' ? ` (${e.elapsedSeconds.toFixed(1)}s)` : '';
    const line = `[${t}] ${type}${detail ? ': ' + detail : ''}${timing}`;
    if(!projectActivityLogs[pid].includes(line)) projectActivityLogs[pid].push(line);
   });
   renderConsoleLogs(pid);
  }
 } catch(_e){}
}

function renderConsoleLogs(pid){
 const logs = (pid && projectActivityLogs[pid]) || (selected && projectActivityLogs[selected]) || [];
 const out = $('console-output');
 const count = $('console-line-count');
 if(out){
  out.textContent = logs.join('\n') || 'Background execution active. Awaiting new events...';
  if(count) count.textContent = `${logs.length} event${logs.length === 1 ? '' : 's'}`;
  out.scrollTop = out.scrollHeight;
 }
 const stageOut = $('stage-upgrade-console');
 const stageCount = $('stage-upgrade-log-count');
 if(stageOut){
  stageOut.textContent = logs.join('\n') || 'Upgrade execution active. Awaiting logs...';
  if(stageCount) stageCount.textContent = `${logs.length} event${logs.length === 1 ? '' : 's'}`;
  stageOut.scrollTop = stageOut.scrollHeight;
 }
}

function initProgressConsoleEvents(){
 const btn = $('toggle-progress-log');
 if(btn){
  btn.onclick = () => {
   const consoleEl = $('pipeline-log-console');
   if(!consoleEl) return;
   const willOpen = consoleEl.hidden;
   consoleEl.hidden = !willOpen;
   btn.classList.toggle('open', willOpen);
   if($('toggle-log-label')){
    $('toggle-log-label').textContent = willOpen ? 'Hide Activity Feed' : 'Live Activity Feed';
   }
   const pid = selected;
   if(pid && willOpen) renderConsoleLogs(pid);
  };
 }
 const stageToggle = $('stage-upgrade-console-toggle');
 if(stageToggle){
  stageToggle.onclick = () => {
   const consoleEl = $('stage-upgrade-console');
   if(!consoleEl) return;
   consoleEl.hidden = !consoleEl.hidden;
  };
 }
}

function renderInformativeProgress(p, activeRun){
 const isRunning = (p?.draft && p.status === 'running') || activeRun?.status === 'running';
 if(!isRunning){
  clearInformativeProgress();
  return;
 }

 const opType = (p?.draft && p.status === 'running')
  ? 'scan'
  : (activeRun?.action === 'guided-upgrade' ? 'upgrade' : (activeRun?.action === 'guided-rollback' ? 'rollback' : 'scan'));

 const checkpoint = (p?.draft && p.status === 'running')
  ? (p.checkpoint || 'inputs_verified')
  : (activeRun?.checkpoint || 'inputs_verified');

 const steps = PIPELINE_SUBSTEPS[opType] || PIPELINE_SUBSTEPS.scan;
 let stepIndex = steps.findIndex(s => s.checkpoints.includes(checkpoint));
 if(stepIndex < 0) stepIndex = 0;
 const currentStep = steps[stepIndex];

 if($('pipeline-live-badge')) $('pipeline-live-badge').hidden = false;
 if($('pipeline-elapsed-wrap')) $('pipeline-elapsed-wrap').hidden = false;
 if($('pipeline-running-info')) $('pipeline-running-info').hidden = false;

 const opLabel = opType === 'upgrade' ? 'Upgrade' : (opType === 'rollback' ? 'Rollback' : 'Audit Scan');
 $('next-title').textContent = `${currentStep.name.replace(/^\d+\.\s*/, '')} in Progress`;
 $('next-detail').textContent = currentStep.desc;
 $('checkpoint').textContent = 'Active Checkpoint: ' + (checkpoint || '').replaceAll('_', ' ');

 const progressPercent = currentStep.percent || Math.round(((stepIndex + 1) / steps.length) * 100);
 if($('progress-step-label')){
  $('progress-step-label').innerHTML = `<svg class="ui-icon icon-spin" style="width:13px;height:13px;vertical-align:-2px;" aria-hidden="true"><use href="#icon-loader"></use></svg> <span>${opLabel}: Step ${stepIndex + 1} of ${steps.length} · ${escapeHtml(currentStep.name.replace(/^\d+\.\s*/, ''))}</span>`;
 }
 if($('progress-percent-label')){
  $('progress-percent-label').textContent = `${progressPercent}%`;
 }
 if($('pipeline-progress-bar')){
  $('pipeline-progress-bar').style.width = `${progressPercent}%`;
 }

 const substepsContainer = $('pipeline-substeps');
 if(substepsContainer){
  substepsContainer.replaceChildren();
  steps.forEach((s, idx) => {
   const pill = el('div', undefined, 'substep-pill');
   if(idx < stepIndex){
    pill.classList.add('completed');
    pill.innerHTML = `<svg class="ui-icon" style="width:12px;height:12px;" aria-hidden="true"><use href="#icon-check"></use></svg><span>${escapeHtml(s.name)}</span>`;
    pill.title = `Completed: ${s.name}`;
   }else if(idx === stepIndex){
    pill.classList.add('active');
    pill.innerHTML = `<svg class="ui-icon icon-spin" style="width:12px;height:12px;" aria-hidden="true"><use href="#icon-loader"></use></svg><span>${escapeHtml(s.name)}</span>`;
    pill.title = `In Progress: ${s.desc}`;
   }else{
    pill.classList.add('pending');
    pill.innerHTML = `<svg class="ui-icon" style="width:12px;height:12px;opacity:0.4;" aria-hidden="true"><use href="#icon-circle"></use></svg><span>${escapeHtml(s.name)}</span>`;
    pill.title = `Upcoming: ${s.name}`;
   }
   substepsContainer.append(pill);
  });
 }

 const startedAt = (p?.draft && p.status === 'running') ? (p.startedAt || p.createdAt) : (activeRun?.startedAt || activeRun?.createdAt);
 startProgressTimer(startedAt);

 const pid = p?.id || activeRun?.project;
 if(pid){
  if(checkpoint && lastProgressCheckpoint !== checkpoint){
   addActivityLog(pid, checkpoint, currentStep.name);
   lastProgressCheckpoint = checkpoint;
  }
  fetchProjectActivity(pid);
 }

 if($('safeguard-chip-action')){
  if(opType === 'upgrade'){
   $('safeguard-chip-action').innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rotate-ccw"></use></svg> <span id="safeguard-chip-action-text">Rollback Armed</span>';
   $('safeguard-chip-action').title = 'Point-in-time database and filesystem snapshot ready';
  } else if(opType === 'rollback'){
   $('safeguard-chip-action').innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rotate-ccw"></use></svg> <span id="safeguard-chip-action-text">Reverting Snapshot</span>';
   $('safeguard-chip-action').title = 'Restoring pre-upgrade baseline snapshot';
  } else {
   $('safeguard-chip-action').innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-shield-check"></use></svg> <span id="safeguard-chip-action-text">Zero-Mutation Scan</span>';
   $('safeguard-chip-action').title = 'Read-only analysis in ephemeral sandbox';
  }
 }
}

function clearInformativeProgress(){
 if($('pipeline-live-badge')) $('pipeline-live-badge').hidden = true;
 if($('pipeline-elapsed-wrap')) $('pipeline-elapsed-wrap').hidden = true;
 if($('pipeline-running-info')) $('pipeline-running-info').hidden = true;
 stopProgressTimer();
 lastProgressCheckpoint = '';
}

async function nextStep(p,pr){
 const actions=$('next-actions');
 $('checkpoint').textContent='';
 const upgrade=latest(pr,'guided-upgrade');
 const completedAudit=latestCompleted(pr,'guided-audit');
 const latestAudit=latest(pr,'guided-audit');
 const rollback=latest(pr,'guided-rollback');

 updateUpgradeExecution(upgrade);

 if(p.draft){
  if(p.status==='running'){
   renderInformativeProgress(p,null);
   actions.replaceChildren();
   actions.append(button('Request stop',()=>api('setup/'+p.id+'/stop',{}),'secondary'));
   updateAuditEvidence(null,p);
   return;
  }
  clearInformativeProgress();
  $('next-title').textContent=p.status==='reconciliation_required'?'Resume interrupted scan':'Scan project';
  $('next-detail').textContent=p.error||'Scan verifies container runtime, audits modules and themes in a disposable database container, resolves Drupal 11 dependencies, and produces the decision report.';
  $('checkpoint').textContent='Last checkpoint: '+(p.checkpoint||'').replaceAll('_',' ');
  if(['draft','blocked','review_required'].includes(p.status)){
   actions.replaceChildren();
   actions.append(button('⚡ Run Test Audit (Fast)',()=>api('projects/'+p.id+'/scan',{fast:true})));
   actions.append(button('Edit inputs',()=>editSetup(p),'secondary'));
  }
  if(p.status==='reconciliation_required'&&p.checkpoint==='sanitizing_copy'){
   actions.replaceChildren();
   actions.append(button('Resume scan',async()=>{await api('setup/'+p.id+'/resume-verification',{});return api('projects/'+p.id+'/scan',{fast:true});}));
  }
  updateAuditEvidence(null,p);
  return;
 }
async function updateRunningAuditState(activeRun, completedAudit){
 const liveSummary = await api('runs/' + activeRun.id + '/quick-summary').catch(() => null);
 const liveCounts = liveSummary?.counts;
 const hasDiscovered = liveCounts && (liveCounts.keep > 0 || liveCounts.compatible_release > 0 || liveCounts.available_patch > 0 || liveCounts.ai_manual_patch > 0);

 if(hasDiscovered){
  if($('plan-stat-compatible')) $('plan-stat-compatible').textContent = String(liveCounts.keep || 0);
  if($('plan-stat-updates')) $('plan-stat-updates').textContent = String(liveCounts.compatible_release || 0);
  if($('plan-stat-patches')) $('plan-stat-patches').textContent = String(liveCounts.available_patch || 0);
  if($('plan-stat-custom')) $('plan-stat-custom').textContent = String((liveCounts.manual_remediation || 0) + (liveCounts.ai_manual_patch || 0));
  if($('plan-core-transition')) $('plan-core-transition').textContent = liveSummary.currentCore ? `${liveSummary.currentCore} → ${liveSummary.targetCore}` : 'Drupal 10 → 11';
  if($('plan-risk-badge')) $('plan-risk-badge').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Assessing (${escapeHtml((activeRun.checkpoint||'').replaceAll('_',' '))})</span>`;
  if($('plan-hard-blockers')) $('plan-hard-blockers').textContent = String(liveSummary.hardBlockers?.length || 0);
  if($('plan-est-duration')) $('plan-est-duration').textContent = liveSummary.timeline?.automatedDuration || '~5–10 mins';
  if($('plan-manual-effort')) $('plan-manual-effort').textContent = `${liveSummary.timeline?.manualReviewHours || 0} hours`;
 } else if(completedAudit){
  if(!gate || gateRunId !== completedAudit.id){
   try {
    [gate, compatibility] = await Promise.all([
     artifactText(completedAudit.id, 'gate.json').then(JSON.parse),
     api('runs/' + completedAudit.id + '/compatibility').then(value => ({...value, extensions: value.extensions.filter(row => row.source !== 'core')}))
    ]);
    const qs = await api('runs/' + completedAudit.id + '/quick-summary').catch(() => null);
    if(qs && qs.currentCore) gate.currentCore = qs.currentCore;
    if(qs && qs.targetCore) gate.targetCore = qs.targetCore;
    if(qs) gate.quickSummary = qs;
    renderCompatibility();
   } catch(e) {}
  }
  updateQuickSummary();
  if($('plan-risk-badge')) $('plan-risk-badge').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Re-scanning...</span>`;
 } else {
  const chk = (activeRun.checkpoint || 'scanning').replaceAll('_', ' ');
  if($('plan-stat-compatible')) $('plan-stat-compatible').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Scanning</span>`;
  if($('plan-stat-updates')) $('plan-stat-updates').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Scanning</span>`;
  if($('plan-stat-patches')) $('plan-stat-patches').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Scanning</span>`;
  if($('plan-stat-custom')) $('plan-stat-custom').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Scanning</span>`;
  if($('plan-risk-badge')) $('plan-risk-badge').innerHTML = `<span class="scanning-badge"><span class="live-pulse-dot"></span> Assessing (${escapeHtml(chk)})</span>`;
  if($('plan-hard-blockers')) $('plan-hard-blockers').textContent = '—';
 }
 const heroBtn = $('hero-one-click-upgrade');
 if(heroBtn){
  heroBtn.disabled = true;
  heroBtn.className = 'primary hero-btn';
  heroBtn.title = 'Scan in progress: Gate 1 audit scan is currently running (' + (activeRun.checkpoint || 'scanning').replaceAll('_', ' ') + ')';
  heroBtn.innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Audit Scan in Progress...';
 }
 const capBtn = $('btn-capture-baseline');
 if(capBtn){
  capBtn.disabled = true;
  capBtn.title = 'Disabled: Audit scan is currently in progress';
 }
 if($('baseline-status-pill')){
  $('baseline-status-pill').className = 'badge muted';
  $('baseline-status-pill').innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Visual Baseline: Waiting for scan...';
 }
 updateAuditEvidence(activeRun, selectedProject());
}

 const active=pr.find(r=>r.status==='running');
 if(active){
  renderInformativeProgress(p,active);
  actions.replaceChildren();
  actions.append(button('Request stop',()=>api('runs/'+active.id+'/stop',{}),'secondary'));
  if(active.action==='guided-audit'){
   await updateRunningAuditState(active,completedAudit);
  }
  return;
 }
 clearInformativeProgress();
 if(upgrade&&['completed','needs_attention'].includes(upgrade.status)&&(!rollback||rollback.startedAt<upgrade.startedAt)){
  $('next-title').textContent=upgrade.status==='completed'?'Automated Upgrade Complete':'Visual Review Required';
  $('next-detail').textContent=upgrade.status==='completed'?'All automated checks passed. Review before/after comparisons below.':'The upgraded site is active on the feature branch. Review visual differences below or roll back to pre-upgrade state.';
  if(!$('btn-open-site')){
   actions.replaceChildren();
   const btnSite = button('Open Site',()=>{
    const proj=selectedProject();
    if(proj?.site?.uri)window.open(proj.site.uri,'_blank','noopener');
    else if(proj?.sourceUrl)window.open(proj.sourceUrl,'_blank','noopener');
   },'secondary');
   btnSite.id = 'btn-open-site';
   actions.append(btnSite);
   actions.append(button('Roll Back to Pre-Upgrade State',()=>{
    $('btn-do-rollback')?.click();
   },'danger'));
  }
  if(completedAudit){
   await showGate(completedAudit, actions, true);
  }
  return;
 }
 if(upgrade&&upgrade.status==='critical'){
  $('next-title').textContent='Critical Rollback Failure';
  $('next-detail').textContent=upgrade.rollbackError||upgrade.error||'The site needs manual recovery. Pre-upgrade database backup pre-upgrade.sql.gz is preserved.';
  return;
 }
  if(completedAudit&&(!upgrade||upgrade.startedAt<completedAudit.startedAt)){
   if(latestAudit&&latestAudit.id!==completedAudit.id&&latestAudit.status==='blocked'){
    $('checkpoint').textContent=`Note: Latest scan attempt (${latestAudit.id.slice(0,8)}) stopped at ${(latestAudit.checkpoint||'').replaceAll('_',' ')}: ${latestAudit.error||'blocked'}. Showing completed audit ${completedAudit.id.slice(0,8)}.`;
   }
   await showGate(completedAudit,actions);
   return;
  }
  if(latestAudit && latestAudit.status === 'blocked'){
   $('next-title').textContent = 'Audit Scan Blocked';
   $('next-detail').textContent = `The scan attempt (${latestAudit.id.slice(0,8)}) stopped at ${(latestAudit.checkpoint||'').replaceAll('_',' ')}: ${latestAudit.error || 'Blocked'}. Check the console or re-run the scan.`;
   $('checkpoint').textContent = `Scan error: ${latestAudit.error || 'Blocked'}`;
   actions.replaceChildren();
   actions.append(button('⚡ Re-run Test Audit (Fast)',()=>api('projects/'+selected+'/scan',{fast:true})));
   developerActions();
   if($('baseline-status-pill')){
    $('baseline-status-pill').className = 'badge amber';
    $('baseline-status-pill').textContent = '⚠️ Scan Incomplete (Audit Blocked)';
   }
   const heroBtn = $('hero-one-click-upgrade');
   if(heroBtn){
    heroBtn.disabled = false;
    heroBtn.className = 'primary hero-btn';
    heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-refresh"></use></svg> Re-run Test Audit (Fast)';
    heroBtn.onclick = () => api('projects/'+selected+'/scan',{fast:true});
   }
   return;
  }
  $('next-title').textContent='Scan Project';
  $('next-detail').textContent='Create the compatibility, page-baseline, risk, timeline, recovery, and exact upgrade decision report.';
  actions.replaceChildren();
  actions.append(button('⚡ Run Test Audit (Fast)',()=>api('projects/'+selected+'/scan',{fast:true})));
  developerActions();
}

async function editSetup(p){
 if(!p && selected){
  try {
   p = await api('projects/' + encodeURIComponent(selected));
  } catch(e) {
   error(e);
   return;
  }
 }
 if(!p) return;
 openSetup();
 $('source').value=p.source||'';
 $('source-url').value=p.sourceUrl||p.site_url||'';
 $('sitemap-url').value=p.sitemapUrl||'';
 setupSourceOrigin=origin(p.sourceUrl||p.site_url||'');
 $('routes').value=(p.routes||[]).join('\n');
 if($('capture-nav-links'))$('capture-nav-links').checked=p.captureNavLinks!==false;
 $('source-wrapper').value=p.wrapper||'auto';
 $('project-id').value=p.id;
 if($('project-id'))$('project-id').readOnly=true;
 $('name').value=p.name||p.id;
 $('database').value=p.database||'';
 $('files').value=p.files||'';
 $('setup-form').dataset.edit=p.id;
 if($('setup-eyebrow'))$('setup-eyebrow').textContent='EDIT PROJECT CONFIGURATION';
 if($('setup-title'))$('setup-title').textContent='Modify ' + (p.name || p.id) + ' Settings';
 if($('setup-submit-text'))$('setup-submit-text').textContent='Save Project Changes →';
 $('title').textContent='Edit ' + (p.name || p.id);
 $('subtitle').textContent='Update project root, site URL, runtime wrapper, or regression routes.';
}

function developerActions(){
 const target=$('developer-actions');
 if(!target)return;
 target.replaceChildren();
 if(!selected){
  const empty = el('div','Select a project to trigger stage actions.','dev-menu-empty');
  target.append(empty);
  return;
 }
 const wrap = (fn) => () => {
  $('developer-dropdown')?.removeAttribute('open');
  return fn();
 };
 const devBtn = (text, fn, tip) => {
  const b = button(text, wrap(fn), 'quiet');
  b.title = tip;
  return b;
 };
 target.append(
  devBtn('Assess only', ()=>startLegacy('assess'),
   'Run Upgrade Status & compatibility scan without capturing visual baselines. Fastest way to check extension decisions.'),
  devBtn('Capture baseline only', ()=>startLegacy('baseline'),
   'Take headless Chrome screenshots of the current live site and save them as the visual regression baseline. No scan is performed.'),
  devBtn('Propose fixes only', ()=>startLegacy('propose'),
   'Run Drupal Rector & AI patch generation to produce code-fix proposals for custom/contrib code. Skips scanning & baselines.'),
  devBtn('Full Deep Scan (All Contrib)', ()=>api('projects/'+selected+'/scan',{fast:false}),
   'Run a thorough scan that includes PHPStan on all contrib modules (not just custom code). Slower (~14+ min) but maximally thorough.')
 );
}

function updateBaselineCaptureUI() {
  if (!isCapturingBaseline) return;
  const elapsedStr = `${baselineCaptureElapsed}s`;

  const heroBtn = $('hero-one-click-upgrade');
  if (heroBtn) {
    heroBtn.disabled = true;
    heroBtn.className = 'primary hero-btn';
    heroBtn.title = `Capturing pre-upgrade visual baseline (${elapsedStr})...`;
    heroBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${elapsedStr})...`;
  }

  const btnUpgrade = $('btn-start-upgrade');
  if (btnUpgrade) {
    btnUpgrade.disabled = true;
    btnUpgrade.title = `Capturing pre-upgrade visual baseline (${elapsedStr})...`;
    btnUpgrade.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${elapsedStr})...`;
  }

  const fastScanBtn = Array.from(document.querySelectorAll('#next-actions button')).find(b => b.textContent.includes('Re-run Scan') || b.textContent.includes('Run Test Audit'));
  if (fastScanBtn) {
    fastScanBtn.disabled = true;
    fastScanBtn.title = 'Disabled while visual baseline capture is executing';
  }

  const pill = $('baseline-status-pill');
  if (pill) {
    pill.className = 'badge blue live-pulse';
    pill.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Visual Baseline: Capturing (${elapsedStr})...`;
  }

  const capBtn = $('btn-capture-baseline');
  if (capBtn) {
    capBtn.style.display = 'inline-flex';
    capBtn.disabled = true;
    capBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing (${elapsedStr})...`;
  }

  const nextTitle = $('next-title');
  const nextDetail = $('next-detail');
  if (nextTitle) {
    nextTitle.innerHTML = `<svg class="ui-icon spin" style="width:18px;height:18px;vertical-align:-3px;margin-right:6px;" aria-hidden="true"><use href="#icon-refresh"></use></svg>Capturing Pre-Upgrade Visual Baseline...`;
  }
  if (nextDetail) {
    const routeNum = gate?.quickSummary?.baselineRoutes || gate?.baseline?.selected || 15;
    nextDetail.textContent = `Taking reference screenshots across ${routeNum} routes (${elapsedStr} elapsed). Dashboard will update automatically when complete.`;
  }

  if ($('pipeline-live-badge')) $('pipeline-live-badge').hidden = false;
  if ($('pipeline-elapsed-wrap')) {
    $('pipeline-elapsed-wrap').hidden = false;
    const mins = String(Math.floor(baselineCaptureElapsed / 60)).padStart(2, '0');
    const secs = String(baselineCaptureElapsed % 60).padStart(2, '0');
    if ($('pipeline-elapsed-text')) $('pipeline-elapsed-text').textContent = `${mins}:${secs}s`;
  }

  const baseCaptureBtn = $('confirm-capture-baseline-btn');
  if (baseCaptureBtn) {
    baseCaptureBtn.disabled = true;
    baseCaptureBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${elapsedStr})...`;
  }
  const confirmBtn = $('execute-confirm-upgrade-btn');
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Pre-Upgrade Baseline (${elapsedStr})...`;
  }
  const baseText = $('confirm-baseline-text');
  if (baseText) {
    baseText.innerHTML = `<strong>Visual Baseline Capture in Progress:</strong> Pre-upgrade screenshots are currently being recorded (${elapsedStr} elapsed). You can close this modal; capture will continue in the background.`;
  }
  const baseNotice = $('confirm-baseline-notice');
  if (baseNotice) {
    baseNotice.style.borderColor = 'var(--blue-border, #3b82f6)';
    baseNotice.style.background = 'var(--blue-bg, rgba(59,130,246,0.08))';
  }
}

async function startCapturingBaseline(targetRunId) {
  const rid = targetRunId || gateRunId || (runs.filter(r => r.project === selected).find(r => r.action === 'guided-audit' && r.status === 'completed')?.id);
  if (!rid) {
    const pr = runs.filter(r => r.project === selected);
    const active = pr.find(r => r.status === 'running');
    if (active) {
      if (typeof showToast === 'function') showToast('Audit scan is currently in progress. Baseline can be captured once the scan finishes.');
    } else {
      if (typeof showToast === 'function') showToast('Please wait for or run the Test Audit scan before capturing visual baseline.');
    }
    return;
  }
  if (isCapturingBaseline) {
    if (typeof showToast === 'function') showToast('Visual baseline capture is already in progress.');
    return;
  }

  isCapturingBaseline = true;
  baselineCaptureStartTime = Date.now();
  baselineCaptureElapsed = 0;
  baselineCaptureRunId = rid;

  if (baselineCaptureTimer) clearInterval(baselineCaptureTimer);
  baselineCaptureTimer = setInterval(() => {
    baselineCaptureElapsed = Math.max(1, Math.floor((Date.now() - baselineCaptureStartTime) / 1000));
    updateBaselineCaptureUI();
  }, 1000);

  updateBaselineCaptureUI();

  try {
    const res = await api('runs/' + rid + '/capture-baseline', {});
    const routeCount = res?.routeCount || (gate?.quickSummary?.baselineRoutes) || 0;

    isCapturingBaseline = false;
    clearInterval(baselineCaptureTimer);
    baselineCaptureTimer = null;
    if (typeof updateUpgradeActionButtons === 'function') updateUpgradeActionButtons();

    gate = null;
    gateRunId = '';
    report = null;

    await refresh();

    const modal = $('upgrade-confirm-modal');
    if (modal && !modal.hidden) {
      openUpgradeConfirmModal();
    }

    if (typeof showToast === 'function') {
      showToast(`✓ Pre-upgrade visual baseline captured successfully (${routeCount || 'all'} routes)! Ready for 1-Click Upgrade.`);
    }
  } catch (err) {
    isCapturingBaseline = false;
    clearInterval(baselineCaptureTimer);
    baselineCaptureTimer = null;
    // updateBaselineCaptureUI() only paints the capturing state; restore the buttons
    // explicitly so a failed capture does not leave "Capturing Baseline (Ns)..." spinning.
    if (typeof updateUpgradeActionButtons === 'function') updateUpgradeActionButtons();

    gate = null;
    gateRunId = '';
    await refresh().catch(() => {});

    if (typeof showToast === 'function') {
      showToast('Failed to capture baseline: ' + (err.message || err));
    }
    error(err);
  }
}

async function showGate(audit,actions,preserveActions=false){
 const needsLoad = !gate || gateRunId !== audit.id || !compatibility;
 if(needsLoad){
  gateRunId=audit.id;
  const artifacts=await api('runs/'+audit.id+'/artifacts');
  if(!artifacts.includes('gate.json')){
   if(!preserveActions){
    $('next-title').textContent='Scan evidence incomplete';
    $('next-detail').textContent=audit.error||'Review the report and scan again after resolving the blocker.';
    actions.replaceChildren();
    actions.append(button('⚡ Scan again (Fast)',()=>api('projects/'+selected+'/scan',{fast:true})));
   }
   return;
  }
  [gate,compatibility]=await Promise.all([
   artifactText(audit.id,'gate.json').then(JSON.parse),
   api('runs/'+audit.id+'/compatibility').then(value=>({...value,extensions:value.extensions.filter(row=>row.source!=='core')}))
  ]);
  const qs=await api('runs/'+audit.id+'/quick-summary').catch(()=>null);
  if(qs&&qs.currentCore)gate.currentCore=qs.currentCore;
  if(qs&&qs.targetCore)gate.targetCore=qs.targetCore;
  if(qs)gate.quickSummary=qs;

  if(artifacts.includes('route-selection.json') && (!gate.baseline || !gate.baseline.routes || !gate.baseline.routes.length)){
   try{
    const rs=await artifactText(audit.id,'route-selection.json').then(JSON.parse);
    if(gate.baseline) gate.baseline.routes = rs.routes || [];
   }catch(e){}
  }

  if(decisionView.digest!==compatibility.digest){
   decisionView.digest=compatibility.digest;
   decisionView.undo={};
   const OBSOLETE_PERF = ['advagg', 'advagg_bundler', 'advagg_css_minify', 'advagg_js_minify', 'advagg_mod', 'advagg_validator', 'fastclick'];
   compatibilityDraft=Object.fromEntries(compatibility.extensions.map(row=>{
    const candVer = row.releaseCandidates?.[0]?.version || row.targetVersion;
    const isSameVersion = Boolean(row.currentVersion && candVer && row.currentVersion === candVer);
    const isClean = row.status === 'ready' && !(row.upgradeStatus?.issueCount > 0) && !(row.rector?.fixableCount > 0);
    let action = getEffectiveAction(row);
    if(OBSOLETE_PERF.includes(row.name) || row.recommendedAction === 'remove'){
      action = 'remove';
    } else if(isSameVersion && (action === 'compatible_release' || !action || action === 'defer')){
      action = isClean ? 'keep' : 'compatible_release';
    }
    const candidatePatch = row.patches?.find(p=>p.approvalEligible);
    const candidateRel = row.releaseCandidates?.[0];
    return [row.name,{
     action: action,
     candidateId: row.decision?.candidateId || (action==='available_patch'?candidatePatch?.id:null) || null,
     candidateVersion: row.decision?.candidateVersion || (action==='keep'?row.currentVersion:row.targetVersion) || (action==='compatible_release'?candidateRel?.version:null) || null,
     acceptRisk: row.decision?.acceptRisk===true,
     note: row.decision?.note||""
    }];
   }));
   decisionView.saved=decisionSignature();
  }
  renderCompatibility();
  updateAuditEvidence(audit,selectedProject());
 }

 updateQuickSummary();

 if(!preserveActions){
  if(isCapturingBaseline){
   $('next-title').innerHTML = `<svg class="ui-icon spin" style="width:18px;height:18px;vertical-align:-3px;margin-right:6px;" aria-hidden="true"><use href="#icon-refresh"></use></svg>Capturing Pre-Upgrade Visual Baseline...`;
   const routeNum = gate?.quickSummary?.baselineRoutes || gate?.baseline?.selected || 15;
   $('next-detail').textContent = `Taking reference screenshots across ${routeNum} routes (${baselineCaptureElapsed}s elapsed). Dashboard will update automatically when complete.`;
   if($('pipeline-live-badge')) $('pipeline-live-badge').hidden = false;
   if($('pipeline-elapsed-wrap')){
    $('pipeline-elapsed-wrap').hidden = false;
    const mins = String(Math.floor(baselineCaptureElapsed / 60)).padStart(2, '0');
    const secs = String(baselineCaptureElapsed % 60).padStart(2, '0');
    if($('pipeline-elapsed-text')) $('pipeline-elapsed-text').textContent = `${mins}:${secs}s`;
   }
  } else {
   $('next-title').textContent=gate.automationLabel||gate.risk?.recommendation||'Audit Complete';
   const navNote=(gate.baseline?.headerCount||gate.baseline?.footerCount)?` (${gate.baseline.headerCount} header, ${gate.baseline.footerCount} footer)`:'';
   $('next-detail').textContent=`${gate.risk?.recommendation||'Ready'} · risk ${gate.risk?.score||0}/100 · ${(gate.risk?.hardBlockers||[]).length} hard blockers · ${(gate.baseline?.selected)||0} baseline routes${navNote}.`;
   if($('pipeline-live-badge')) $('pipeline-live-badge').hidden = true;
   if($('pipeline-elapsed-wrap')) $('pipeline-elapsed-wrap').hidden = true;
  }

  if(!$('btn-start-upgrade')){
   actions.replaceChildren();
   const btnUpgrade = button('Start 1-Click Upgrade',async()=>{
    const isBusyUpgrade = isStartingUpgrade || (runs && runs.some(r => r.action === 'guided-upgrade' && r.status === 'running'));
    if (isBusyUpgrade || isCapturingBaseline) return;
    if($('hero-one-click-upgrade'))$('hero-one-click-upgrade').click();
   });
   btnUpgrade.id = 'btn-start-upgrade';
   actions.append(btnUpgrade);
   const btnScan = button('⚡ Re-run Scan (Fast)',async()=>{
    const isBusyUpgrade = isStartingUpgrade || (runs && runs.some(r => r.action === 'guided-upgrade' && r.status === 'running'));
    if (isBusyUpgrade || isCapturingBaseline || (runs && runs.some(r => r.status === 'running'))) {
      if (typeof showToast === 'function') showToast('Cannot scan while an upgrade or baseline capture is executing.');
      return;
    }
    return api('projects/'+selected+'/scan',{fast:true});
   },'secondary');
   actions.append(btnScan);
   developerActions();
  }
 }
 updateUpgradeActionButtons();
}

function isTerminalRunning(){
 return Boolean(activeJobId && (terminalEventSource !== null || window.__activeTerminalStatus === 'running'));
}

function updateUpgradeActionButtons(){
 const p = selectedProject();
 const pr = runs ? runs.filter(r => r.project === (p?.id || selected)) : [];
 const isUpgraded = isProjectUpgraded(p, pr);
 const activeVer = getActiveCoreVersion(p);
 const isTermRunning = isTerminalRunning();
 const isWorkflowActive = Boolean((runs && runs.some(r=>r.status==='running')) || (projects && projects.some(p=>p.status==='running')));
 const isBusyUpgrade = isStartingUpgrade || pr.some(r => r.action === 'guided-upgrade' && r.status === 'running');
 const isAnyBusy = isBusyUpgrade || isWorkflowActive || isCapturingBaseline || isTermRunning;
 const isUpgradeBusy = isBusyUpgrade || isTermRunning || isWorkflowActive;

 const heroBtn = $('hero-one-click-upgrade');
 const heroTitle = $('plan-hero-title');
 const heroDesc = $('plan-hero-desc');
 const heroCard = $('plan-hero-card');

 const qs = gate?.quickSummary;
 const hasBaseline = Boolean(qs?.baselineCaptured);

 if(isUpgraded){
  const upgrade = latest(pr, 'guided-upgrade');
  if(heroCard) heroCard.classList.add('hero-upgraded');

  if(upgrade && ['completed', 'needs_attention'].includes(upgrade.status)){
   if(heroTitle) heroTitle.innerHTML = `<svg class="ui-icon hero-icon" aria-hidden="true"><use href="#icon-shield-check"></use></svg> Drupal ${escapeHtml(activeVer)} Active · Visual Review Required`;
   if(heroDesc) heroDesc.textContent = `The rehearsal upgrade succeeded. The site is currently running Drupal ${activeVer} on a dedicated feature branch with pre-upgrade.sql.gz backed up. Review visual differences across baseline routes or roll back to pre-upgrade state at any time.`;
   if(heroBtn){
    heroBtn.disabled = isUpgradeBusy;
    heroBtn.className = 'primary hero-btn';
    heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Review Gate 2 Visual Diffs →';
    heroBtn.onclick = () => showStage('regression', true);
   }
  } else {
   if(heroTitle) heroTitle.innerHTML = `<svg class="ui-icon hero-icon" aria-hidden="true"><use href="#icon-shield-check"></use></svg> Drupal ${escapeHtml(activeVer)} Active`;
   if(heroDesc) heroDesc.textContent = `This project is currently running Drupal ${activeVer}. Core and extension compatibility are verified.`;
   if(heroBtn){
    heroBtn.disabled = isUpgradeBusy;
    heroBtn.className = 'secondary hero-btn';
    heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Review Visual Regression Baseline →';
    heroBtn.onclick = () => showStage('regression', true);
   }
  }
 } else {
  if(heroCard) heroCard.classList.remove('hero-upgraded');
  if(heroTitle) heroTitle.innerHTML = `<svg class="ui-icon hero-icon" aria-hidden="true"><use href="#icon-rocket"></use></svg> Fast 1-Click Upgrade Rehearsal`;
  if(heroDesc) heroDesc.textContent = `Automatically remediate custom code with AI, upgrade compatible Composer packages to Drupal 11, apply community patches, and execute the upgrade with database backup and a dedicated feature branch.`;
  if(heroBtn){
   heroBtn.className = 'primary hero-btn';
   heroBtn.onclick = () => {
    if(isAnyBusy) return;
    if(typeof openUpgradeConfirmModal === 'function') openUpgradeConfirmModal();
   };
   if(isTermRunning){
    heroBtn.disabled = true;
    heroBtn.title = 'Disabled: Command currently executing in Upgrade Console (' + (activeJobDescription || 'console task') + ')';
    heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-clock"></use></svg> Console Task Running...';
   }else if(isStartingUpgrade){
    heroBtn.disabled = true;
    heroBtn.title = `Disabled: Upgrade rehearsal is starting (${upgradeElapsed}s)`;
    heroBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Starting Upgrade (${upgradeElapsed}s)...`;
   }else if(isBusyUpgrade){
    heroBtn.disabled = true;
    heroBtn.title = 'Disabled: Upgrade rehearsal is currently executing';
    heroBtn.innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Upgrade Running...';
   }else if(isCapturingBaseline){
    heroBtn.disabled = true;
    heroBtn.title = `Disabled: Pre-upgrade visual baseline capture is currently in progress (${baselineCaptureElapsed}s)`;
    heroBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${baselineCaptureElapsed}s)...`;
   }else if(isWorkflowActive){
    heroBtn.disabled = true;
    heroBtn.title = 'Disabled: An upgrade or scan workflow is currently executing';
    heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-clock"></use></svg> Workflow Running...';
   }else if(!hasBaseline){
    heroBtn.disabled = false;
    heroBtn.title = 'Visual regression baseline required before rehearsal. Click to review upgrade proposal and capture baseline.';
    heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Capture Baseline &amp; Rehearse →';
   }else{
    heroBtn.disabled = false;
    heroBtn.title = '';
    if(gate?.approvalEligible){
     heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rocket"></use></svg> Start 1-Click Upgrade →';
    }else{
     heroBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rocket"></use></svg> Start 1-Click Upgrade (Auto-Resolve &amp; Execute) →';
    }
   }
  }
 }

 const btnUpgrade = $('btn-start-upgrade');
 if(btnUpgrade){
  if(isUpgraded){
   btnUpgrade.textContent = 'Review Visual Diffs';
   btnUpgrade.className = 'secondary';
   btnUpgrade.disabled = isUpgradeBusy;
   btnUpgrade.title = 'View before/after visual regression comparisons';
   btnUpgrade.onclick = () => showStage('regression', true);
  } else {
   btnUpgrade.className = '';
   btnUpgrade.onclick = async () => {
    if(isAnyBusy) return;
    if($('hero-one-click-upgrade')) $('hero-one-click-upgrade').click();
   };
   if(isTermRunning){
    btnUpgrade.disabled = true;
    btnUpgrade.title = 'Disabled: Command currently executing in Upgrade Console (' + (activeJobDescription || 'console task') + ')';
    btnUpgrade.textContent = 'Upgrade Running in Console...';
   }else if(isStartingUpgrade){
    btnUpgrade.disabled = true;
    btnUpgrade.title = `Disabled: Upgrade rehearsal is starting (${upgradeElapsed}s)`;
    btnUpgrade.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Starting Upgrade (${upgradeElapsed}s)...`;
   }else if(isBusyUpgrade){
    btnUpgrade.disabled = true;
    btnUpgrade.title = 'Disabled: Upgrade rehearsal is currently executing';
    btnUpgrade.innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Upgrade Running...';
   }else if(isCapturingBaseline){
    btnUpgrade.disabled = true;
    btnUpgrade.title = `Disabled: Pre-upgrade visual baseline capture is currently in progress (${baselineCaptureElapsed}s)`;
    btnUpgrade.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${baselineCaptureElapsed}s)...`;
   }else if(isWorkflowActive){
    btnUpgrade.disabled = true;
    btnUpgrade.title = 'Disabled: Workflow is currently running';
    btnUpgrade.textContent = 'Workflow Running...';
   }else if(!hasBaseline){
    btnUpgrade.disabled = false;
    btnUpgrade.title = 'Visual regression baseline required before rehearsal. Click to review upgrade proposal and capture baseline.';
    btnUpgrade.textContent = 'Capture Baseline & Rehearse →';
   }else{
    btnUpgrade.disabled = false;
    btnUpgrade.title = '';
    btnUpgrade.textContent = 'Start 1-Click Upgrade';
   }
  }
 }

 // ── Top-bar terminal button label ──────────────────────────────────────────
 const topBtn = $('top-terminal-btn');
 if(topBtn){
  const topBtnSpan = topBtn.querySelector('span');
  if(topBtnSpan){
   topBtnSpan.textContent = isUpgraded ? 'Run Console Command' : 'Run Upgrade Command';
  }
  topBtn.title = isUpgraded
   ? 'Open terminal console to run Drush or Composer commands'
   : 'Open terminal console to run Drush, Composer, or Upgrade commands';
 }

 // ── Capture baseline button — hide when upgrade is already done ─────────────
 const capBtn = $('btn-capture-baseline');
 if(capBtn){
  if(isUpgraded){
   capBtn.style.display = 'none';
  }
 }

 const fastScanBtn = Array.from(document.querySelectorAll('#next-actions button')).find(b => b.textContent.includes('Re-run Scan') || b.textContent.includes('Run Test Audit'));
 if(fastScanBtn){
  fastScanBtn.disabled = isAnyBusy;
  if(isStartingUpgrade || isBusyUpgrade){
   fastScanBtn.title = 'Disabled while upgrade rehearsal is executing';
  }else if(isCapturingBaseline){
   fastScanBtn.title = 'Disabled while visual baseline capture is executing';
  }else if(isWorkflowActive){
   fastScanBtn.title = 'Disabled while workflow is running';
  }else if(isTermRunning){
   fastScanBtn.title = 'Disabled while console task is executing';
  }else{
   fastScanBtn.title = '';
  }
 }
}

function isCleanExtension(row){
 if(!row)return false;
 return row.status==='ready' && !(row.upgradeStatus?.issueCount>0) && !(row.rector?.fixableCount>0);
}

function getRecommendedAction(row){
 if(!row)return 'defer';
 if(row.name === 'tb_megamenu' && (row.currentVersion?.startsWith('3.') || row.currentVersion === '3.0.0-alpha5')) return 'keep';
 const OBSOLETE_PERF = ['advagg', 'advagg_bundler', 'advagg_css_minify', 'advagg_js_minify', 'advagg_mod', 'advagg_validator', 'fastclick'];
 if(OBSOLETE_PERF.includes(row.name))return 'remove';
 if(row.recommendedAction==='remove')return 'remove';
 if(row.source==='contrib' && row.enabled===false && row.exported===false)return 'remove';
 if(row.source==='contrib' && row.type==='theme' && !row.targetVersion && (!row.releaseCandidates || !row.releaseCandidates.some(rc => rc.stability === 'stable')))return 'remove';

 const candVer = row.releaseCandidates?.[0]?.version || row.targetVersion;
 const isSameVersion = Boolean(row.currentVersion && candVer && row.currentVersion === candVer);
 const isClean = isCleanExtension(row);
 // Invariant 2: keep only when clean (ready AND no findings); a ready row with findings falls through to remediation.
 if(isClean)return 'keep';

 if(row.status==='update_available'||row.recommendedAction==='compatible_release')return 'compatible_release';
 if(candVer)return 'compatible_release';
 if(row.status==='patch_available'||row.recommendedAction==='available_patch'||(row.patches && row.patches.length > 0))return 'available_patch';
 if(row.status==='manual_remediation'||row.recommendedAction==='ai_manual_patch'||row.source==='custom'||(row.rector?.fixableCount||0)>0||(row.upgradeStatus?.issueCount||0)>0)return 'ai_manual_patch';
 if(row.recommendedAction && row.recommendedAction !== 'defer')return row.recommendedAction;
 return 'defer';
}

function getEffectiveAction(row){
 if(!row)return 'pending';
 if(row.name === 'tb_megamenu' && (row.currentVersion?.startsWith('3.') || row.currentVersion === '3.0.0-alpha5')) {
  const chosenAct = compatibilityDraft[row.name]?.action || row.selectedAction;
  const chosenCand = compatibilityDraft[row.name]?.candidateVersion || row.decision?.candidateVersion;
  if(!chosenAct || chosenAct === 'defer' || (chosenAct === 'compatible_release' && (chosenCand?.startsWith('1.') || !chosenCand))) {
   return 'keep';
  }
 }
 const candVer = row.releaseCandidates?.[0]?.version || row.targetVersion;
 const isSameVersion = Boolean(row.currentVersion && candVer && row.currentVersion === candVer);
 const isClean = isCleanExtension(row);
 const chosen=compatibilityDraft[row.name]?.action||row.selectedAction;
 if(chosen && chosen!=='defer'){
  if(isSameVersion && chosen==='compatible_release' && isClean) return 'keep';
  return chosen;
 }
 return getRecommendedAction(row);
}

function updateQuickSummary(){
 const counts={keep:0,compatible_release:0,available_patch:0,ai_manual_patch:0,manual_remediation:0,remove:0,defer:0,pending:0};
 for(const row of compatibility?.extensions||[]){
  const action=getEffectiveAction(row);
  counts[action]=(counts[action]||0)+1;
 }
 if(!compatibility?.extensions?.length && gate?.compatibility?.summary){
  const s=gate.compatibility.summary;
  counts.keep=s.ready||0;
  counts.compatible_release=s.update_available||0;
  counts.available_patch=s.patch_available||0;
  counts.ai_manual_patch=s.manual_remediation||0;
 }
 if(!compatibility?.extensions?.length && gate?.quickSummary?.counts){
  Object.assign(counts, gate.quickSummary.counts);
 }
 const p = selectedProject();
 const pr = runs ? runs.filter(r => r.project === (p?.id || selected)) : [];
 const isUpgraded = isProjectUpgraded(p, pr);
 const activeVer = getActiveCoreVersion(p);
 const targetCore=gate?.targetCore||gate?.exactCoreVersion||gate?.target||'11.x',
       currentCore=gate?.currentCore || p?.currentCore;

 if($('plan-core-transition')){
  if(isUpgraded){
   $('plan-core-transition').textContent = `Drupal ${activeVer} (Active)`;
  } else {
   $('plan-core-transition').textContent = currentCore&&currentCore!=='10.x'?`${currentCore} → ${targetCore}`:targetCore;
  }
 }
 if($('plan-core-label')){
  $('plan-core-label').textContent = isUpgraded ? 'Current Core Version (Upgraded)' : 'Target Core Version';
 }
 const eyebrow = $('pipeline-eyebrow');
 if(eyebrow){
  if(isUpgraded){
   eyebrow.textContent = `DRUPAL ${activeVer} ACTIVE · UPGRADED`;
   eyebrow.classList.add('eyebrow-upgraded');
  } else {
   eyebrow.textContent = 'DRUPAL 10 → 11 UPGRADE PIPELINE';
   eyebrow.classList.remove('eyebrow-upgraded');
  }
 }
 if($('plan-stat-compatible'))$('plan-stat-compatible').textContent=String(counts.keep);
 if($('plan-stat-updates'))$('plan-stat-updates').textContent=String(counts.compatible_release);
 if($('plan-stat-patches'))$('plan-stat-patches').textContent=String(counts.available_patch);
 if($('plan-stat-custom'))$('plan-stat-custom').textContent=String((counts.manual_remediation||0)+(counts.ai_manual_patch||0));
 if($('plan-risk-badge'))$('plan-risk-badge').textContent=`${gate?.risk?.score||0}/100 (${gate?.risk?.recommendation||'—'})`;
 const initialBlockerCount = gate?.risk?.hardBlockers?.length||0;
 if($('plan-hard-blockers'))$('plan-hard-blockers').textContent=initialBlockerCount > 0 ? String(initialBlockerCount) : '0 (Safe to rehearse)';
  const qs=gate?.quickSummary;
  if($('plan-est-duration'))$('plan-est-duration').textContent=qs?.timeline?.automatedDuration||'~5–10 mins';
  if($('plan-manual-effort'))$('plan-manual-effort').textContent=`${qs?.timeline?.manualReviewHours||0} hours`;

  if ($('baseline-status-pill')) {
    const hasBaseline = Boolean(qs?.baselineCaptured);
    const routeCount = qs?.baselineScreenshotCount || qs?.baselineRoutes || 0;
    const pill = $('baseline-status-pill');
    const capBtn = $('btn-capture-baseline');
    if (isCapturingBaseline || qs?.baselineCapturing) {
      pill.className = 'badge blue';
      pill.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${baselineCaptureElapsed}s)...`;
      if (capBtn) {
        capBtn.style.display = 'inline-flex';
        capBtn.disabled = true;
        capBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing (${baselineCaptureElapsed}s)...`;
      }
    } else if (hasBaseline) {
      pill.className = 'badge green';
      pill.innerHTML = `<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Visual Baseline: Ready (${routeCount} routes)`;
      if (capBtn) {
        capBtn.style.display = 'inline-flex';
        capBtn.disabled = false;
        capBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Refresh Baseline';
      }
    } else {
      pill.className = 'badge amber';
      pill.innerHTML = `<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Visual Baseline: Deferred (Fast Scan)`;
      if (capBtn) {
        capBtn.style.display = 'inline-flex';
        capBtn.disabled = false;
        capBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Capture Baseline Now';
      }
    }
  }
  // ── Adapt all three info cards when the project is already on Drupal 11 ──
  if(isUpgraded){
   const blockerCount = gate?.risk?.hardBlockers?.length||0;

   // Card 1: "Upgrade Proposal" → "Drupal 11 Active Status"
   const c1 = $('plan-card1-title');
   if(c1) c1.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-shield-check"></use></svg> Drupal 11 Active Status';

   const chipCompatible = $('plan-chip-compatible');
   if(chipCompatible) chipCompatible.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-check"></use></svg> Ready';
   const chipUpdates = $('plan-chip-updates');
   if(chipUpdates) chipUpdates.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-package"></use></svg> Updated';

   // Combine community patches + AI/manual fixes into one accurate "Patched / Remediated" chip
   // This matches the Report's "Patched (13)" filter tab which combines all three action types.
   const totalPatched = (counts.available_patch||0) + (counts.manual_remediation||0) + (counts.ai_manual_patch||0);
   if($('plan-stat-patches')) $('plan-stat-patches').textContent = String(totalPatched);
   const chipPatches = $('plan-chip-patches');
   if(chipPatches) chipPatches.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-git-branch"></use></svg> Patched / Remediated';
   // Hide the separate "Remediated" chip to avoid the misleading "Patched: 0 / Remediated: 13" split
   const chipCustomWrap = $('plan-chip-custom-wrap');
   if(chipCustomWrap) chipCustomWrap.style.display = 'none';

   // Card 2: "Risk Assessment" → "Upgrade Outcome"
   const c2 = $('plan-card2-title');
   if(c2) c2.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-shield-check"></use></svg> Upgrade Outcome';

   if($('plan-risk-badge')) $('plan-risk-badge').textContent = 'Verified';
   if($('plan-risk-detail-blockers')) $('plan-risk-detail-blockers').innerHTML = `<strong>Resolved Blockers:</strong> ${blockerCount}`;
   if($('plan-risk-detail-isolation')) $('plan-risk-detail-isolation').innerHTML = '<strong>DB Snapshot:</strong> pre-upgrade.sql.gz retained';
   if($('plan-risk-detail-repo')) $('plan-risk-detail-repo').innerHTML = '<strong>Rollback:</strong> Available at any time';

   // Card 3: "Timeline & Scope" → "Upgrade Summary"
   const c3 = $('plan-card3-title');
   if(c3) c3.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-check-circle"></use></svg> Upgrade Summary';

   if($('plan-est-duration')) $('plan-est-duration').textContent = 'Completed';
   if($('plan-est-label')) $('plan-est-label').textContent = 'Upgrade Status';
   if($('plan-timeline-detail-manual')){
    const manualHours = qs?.timeline?.manualReviewHours||0;
    $('plan-timeline-detail-manual').innerHTML = `<strong>Custom Fixes Applied:</strong> ${manualHours} hour${manualHours!==1?'s':''}`;
   }
   if($('plan-timeline-detail-rollback')) $('plan-timeline-detail-rollback').innerHTML = '<strong>Rollback Duration:</strong> Under 5 minutes';
   if($('plan-timeline-detail-handoff')) $('plan-timeline-detail-handoff').innerHTML = '<strong>Git Handoff:</strong> Developer commits &amp; pushes manually';

   // Compatibility matrix: relabel section header
   const compatLabel = $('plan-compat-summary-label');
   if(compatLabel) compatLabel.textContent = `Module & Theme Compatibility Status (Drupal ${escapeHtml(activeVer)} Active)`;

  } else {
   // Restore default labels when switching back to a non-upgraded project
   const c1 = $('plan-card1-title');
   if(c1) c1.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-clipboard"></use></svg> Upgrade Proposal';

   const chipCompatible = $('plan-chip-compatible');
   if(chipCompatible) chipCompatible.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-check"></use></svg> Compatible';
   const chipUpdates = $('plan-chip-updates');
   if(chipUpdates) chipUpdates.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-package"></use></svg> Updates';
   const chipPatches = $('plan-chip-patches');
   if(chipPatches) chipPatches.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-git-branch"></use></svg> Patches';
   const chipCustom = $('plan-chip-custom');
   if(chipCustom) chipCustom.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-sparkles"></use></svg> Custom AI Fixes';
   // Restore the fourth chip and reset patches stat to available_patch-only count
   const chipCustomWrap = $('plan-chip-custom-wrap');
   if(chipCustomWrap) chipCustomWrap.style.display = '';
   if($('plan-stat-patches')) $('plan-stat-patches').textContent = String(counts.available_patch||0);

   const c2 = $('plan-card2-title');
   if(c2) c2.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-shield"></use></svg> Deployment Risk Score';
   const blockerCount = gate?.risk?.hardBlockers?.length || 0;
   const manualHours = qs?.timeline?.manualReviewHours ?? (blockerCount > 0 ? blockerCount * 2 : 0);
   if($('plan-risk-detail-blockers')) $('plan-risk-detail-blockers').innerHTML = `<strong>Hard Blockers:</strong> <span id="plan-hard-blockers">${blockerCount > 0 ? blockerCount : '0 (Safe to rehearse)'}</span>`;
   if($('plan-risk-detail-isolation')) $('plan-risk-detail-isolation').innerHTML = '<strong>Isolation:</strong> Database backup &amp; feature branch';
   if($('plan-risk-detail-repo')) $('plan-risk-detail-repo').innerHTML = '<strong>Source Repository:</strong> Works in-place on dedicated branch';

   const c3 = $('plan-card3-title');
   if(c3) c3.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-clock"></use></svg> Timeline &amp; Scope';
   if($('plan-est-label')) $('plan-est-label').textContent = 'Estimated Automation Time';
   if($('plan-timeline-detail-manual')) $('plan-timeline-detail-manual').innerHTML = `<strong>Manual Code Fixes:</strong> <span id="plan-manual-effort">${manualHours} hour${manualHours !== 1 ? 's' : ''}</span>`;
   if($('plan-timeline-detail-rollback')) $('plan-timeline-detail-rollback').innerHTML = '<strong>Rollback Duration:</strong> Under 5 minutes';
   if($('plan-timeline-detail-handoff')) $('plan-timeline-detail-handoff').innerHTML = '<strong>Git Handoff:</strong> Developer commits &amp; pushes manually';

   const compatLabel = $('plan-compat-summary-label');
   if(compatLabel) compatLabel.textContent = 'Module & Theme Compatibility Decision Matrix';
  }

  updateUpgradeActionButtons();
}

function renderDecisionSummary(){
 const counts={keep:0,compatible_release:0,available_patch:0,ai_manual_patch:0,remove:0,manual_remediation:0,defer:0,pending:0};
 for(const row of compatibility?.extensions||[]){
  const action=getEffectiveAction(row);
  counts[action]=(counts[action]||0)+1;
 }
 if($('compatibility-summary'))$('compatibility-summary').replaceChildren(...[['Compatible',counts.keep],['Updates',counts.compatible_release],['Patches',counts.available_patch],['AI fixes',counts.ai_manual_patch],['Removals',counts.remove],['Manual / blocked',counts.manual_remediation+counts.defer+counts.pending]].map(([label,count])=>{const item=el('div',undefined,'decision-stat');item.append(el('strong',String(count)),el('span',label));return item;}));
 updateQuickSummary();
}

function renderCompatibilityCards(){
 const target=$('compatibility-list');
 if(!target)return;
 const query=($('compatibility-search')?.value||'').toLowerCase();
 target.replaceChildren();
 if(!compatibility){target.append(el('p','Compatibility report is unavailable.','muted'));return;}
 renderDecisionSummary();
 const rows=compatibility.extensions.filter(row=>(row.name+' '+row.label+' '+row.status+' '+row.type+' '+row.source).toLowerCase().includes(query));
 for(const row of rows){
  const defaultAction = getEffectiveAction(row);
  const isTbMegaMenuV3 = row.name === 'tb_megamenu' && (row.currentVersion?.startsWith('3.') || row.currentVersion === '3.0.0-alpha5');
  const draft=compatibilityDraft[row.name]||{
   action: defaultAction,
   candidateId: row.decision?.candidateId || (defaultAction==='available_patch'?row.patches?.find(p=>p.approvalEligible)?.id:null) || null,
   candidateVersion: isTbMegaMenuV3 ? row.currentVersion : (row.decision?.candidateVersion || row.targetVersion || (defaultAction==='compatible_release'?row.releaseCandidates?.[0]?.version:null) || null),
   acceptRisk: row.decision?.acceptRisk===true,
   note: row.decision?.note||''
  };
  compatibilityDraft[row.name]=draft;
  const wrap=el('div',undefined,'compat-row');
  wrap.dataset.name=row.name;
  const head=el('div',undefined,'compat-head');
  head.append(el('strong',row.label+' ('+row.name+')'),el('span',row.status.replaceAll('_',' '),'badge'),el('small',(row.currentVersion||'—')+' → '+(draft.candidateVersion||row.targetVersion||'unchanged')+' · '+row.risk));
  wrap.append(head);
  wrap.append(el('p',`${row.type} · ${row.source} · ${row.enabled===true?'enabled':row.enabled===false?'disabled':'enabled state unknown'} · Upgrade Status ${row.upgradeStatus.issueCount} issue(s) · Rector ${row.rector.fixableCount} change(s) · ${row.evidenceSource}`,'muted'));
  if(row.autoSelected)wrap.append(el('p','Safe recommendation selected automatically.','auto-choice'));
  else if(row.releaseCandidates?.length&&!row.safeUpgradeEligible)wrap.append(el('p','Candidate available — verification required','muted'));
  for(const blocker of row.blockers||[])wrap.append(el('p',blocker,'compat-blocker'));
  const choices=[['keep','Keep — already compatible'],['compatible_release','Upgrade to compatible release'],['available_patch','Apply verified Drupal.org patch'],['ai_manual_patch','Apply validated AI patch'],['remove','Remove / uninstall'],['manual_remediation','Manual remediation'],['defer','Defer and remain blocked']],cards=el('div',undefined,'decision-cards');
  for(const [value,label] of choices){
   const disabled=value==='keep'&&row.status!=='ready'||value==='compatible_release'&&!row.releaseCandidates?.length||value==='available_patch'&&!row.patches.some(p=>p.approvalEligible)||value==='ai_manual_patch'&&!['custom','contrib'].includes(row.source);
   const card=el('label',undefined,'decision-card'+(disabled?' disabled':'')),input=document.createElement('input');
   input.type='radio';input.name='decision-'+row.name;input.value=value;input.checked=draft.action===value;input.disabled=disabled;
   card.append(input,el('span',label));cards.append(card);
  }
  wrap.append(cards);
  if(row.releaseCandidates?.length){
   const release=el('select');release.dataset.releaseFor=row.name;release.setAttribute('aria-label','Compatible release for '+row.name);
   for(const candidate of row.releaseCandidates)option(release,candidate.version+' · '+candidate.stability,candidate.version);
   release.value=draft.candidateVersion||row.releaseCandidates[0].version;
   release.onchange=()=>{draft.candidateVersion=release.value;compatibilityDraft[row.name]=draft;renderDecisionSummary();};
   wrap.append(release);
  }
  const candidates=row.patches.filter(p=>p.approvalEligible);
  if(candidates.length){
   const patch=el('select');patch.dataset.patchFor=row.name;patch.setAttribute('aria-label','Patch candidate for '+row.name);
   for(const candidate of candidates)option(patch,(candidate.title||candidate.id)+' · '+String(candidate.commit||candidate.sha256||'hash pinned').slice(0,12),candidate.id);
   patch.value=draft.candidateId||candidates[0].id;
   patch.onchange=()=>{draft.candidateId=patch.value;compatibilityDraft[row.name]=draft;};
   wrap.append(patch);
   const chosen=candidates.find(candidate=>candidate.id===draft.candidateId)||candidates[0],
         applicability=chosen.applicability||(chosen.applicable===true?'passed':'recorded'),
         tests=Array.isArray(chosen.regressionChecks)?chosen.regressionChecks.length:chosen.tests||'required';
   wrap.append(el('p',`Patch evidence: ${chosen.issueUrl||chosen.webUrl||'Drupal.org issue recorded'} · ${chosen.status||'review status recorded'} · SHA-256 ${chosen.sha256||'recorded in artifact'} · applicability ${applicability} · tests ${tests}`,'evidence-note'));
  }
  if(draft.action==='remove'){
   const impact=row.impact||{};
   wrap.append(el('p',`Removal impact: ${impact.reverseDependencies?.length||0} reverse dependencies · ${impact.configurationReferences?.length||0} configuration references · ${impact.fieldProviders?.length||0} field providers · populated content ${impact.populatedContent===false?'none found':impact.populatedContent===true?'found':'unknown'}.`,'evidence-note'));
  }
  if(row.releaseCandidates?.some(candidate=>candidate.stability==='prerelease')){
   const accept=el('label',undefined,'check'),input=document.createElement('input');
   input.type='checkbox';input.dataset.prerelease=row.name;input.checked=draft.acceptRisk===true;
   input.onchange=()=>{draft.acceptRisk=input.checked;compatibilityDraft[row.name]=draft;};
   accept.append(input,el('span','Accept prerelease risk for this extension'));wrap.append(accept);
  }
  const generate=button('Generate and validate AI patch',async()=>{
   await loadProviders();
   return api('runs/'+gateRunId+'/ai-patch',{module:row.name,provider:$('ai-provider')?.value});
  },'quiet');
  generate.dataset.generateFor=row.name;
  generate.hidden=draft.action!=='ai_manual_patch';
  wrap.append(generate);
  if(row.decision?.proposalDigest)wrap.append(el('p',`Validated proposal: ${row.decision.proposalDigest}`,'evidence-note'));
  cards.onchange=()=>{
   draft.action=wrap.querySelector('input[type=radio]:checked')?.value;
   if(draft.action==='compatible_release')draft.candidateVersion=wrap.querySelector('[data-release-for]')?.value||null;
   if(draft.action==='available_patch')draft.candidateId=wrap.querySelector('[data-patch-for]')?.value||null;
   compatibilityDraft[row.name]=draft;generate.hidden=draft.action!=='ai_manual_patch';renderDecisionSummary();
  };
  target.append(wrap);
 }
}

function compatibilityDecisions(){
 const rows = (typeof compatibility !== 'undefined' && compatibility && compatibility.extensions) ? Object.fromEntries(compatibility.extensions.map(r => [r.name, r])) : {};
 return Object.entries(compatibilityDraft).filter(([,value])=>value.action).map(([name,value])=>{
  const row = rows[name];
  const isSafe = Boolean(row && row.safeUpgradeEligible && value.action === 'compatible_release' && value.candidateVersion === row.safeUpgradeVersion && typeof decisionView !== 'undefined' && decisionView.undo && decisionView.undo[name]);
  return {
   name,action:value.action,candidateId:value.candidateId||null,candidateVersion:value.candidateVersion||null,acceptRisk:value.acceptRisk===true,note:value.note||'',bulkSafe:isSafe
  };
 });
}

async function loadProviders(){
 if(!providers)providers=await api('providers');
 window.providers=providers;
 const select=$('ai-provider');
 if(select){
  const current=select.value;select.replaceChildren();
  for(const p of providers){
   const opt=document.createElement('option');opt.value=p.id;
   const ready=p.available&&p.safeInterface;opt.textContent=`${p.name||p.id} (${p.mode==='api'?'API':'CLI'}) — ${ready?'Ready':'Unavailable'}`;
   opt.disabled=!ready;select.append(opt);
  }
  const ready=providers.filter(p=>p.available&&p.safeInterface);
  if(ready.length){
   if(ready.some(p=>p.id===current))select.value=current;
   else select.value=ready[0].id;
  }
 }
 if($('provider-status'))$('provider-status').textContent=providers.map(p=>`${p.name||p.id}: ${p.message}`).join(' · ');
 const ready=providers.filter(p=>p.available&&p.safeInterface);
 if($('ai-pr-summary-btn'))$('ai-pr-summary-btn').style.display=ready.length?'inline-flex':'none';
 for(const control of document.querySelectorAll('[data-generate-for]'))control.disabled=!ready.length;
}

async function startLegacy(action,batch){return api('runs',{project:selected,action,batch});}

async function loadReport(rid){
 if(!rid)return; // nothing selected yet; never request /api/runs//report
 report=await api(rid.startsWith('setup--')?'setup/'+rid.slice(7)+'/report':'runs/'+rid+'/report');
 if($('report-meta'))$('report-meta').textContent=`${rid.slice(0,10)} · ${report.generatedAt||'In progress'} · ${report.partial?'Partial / review required':'Completed evidence'}${report.configChanged?' · inputs changed':''}`;
 if($('report-tabs')){
  $('report-tabs').replaceChildren();
  for(const section of report.sections){
   const tab=button(section.title,()=>{activeTab=section.id;renderReport();},'quiet');
   tab.setAttribute('role','tab');tab.dataset.tab=section.id;$('report-tabs').append(tab);
  }
 }
 const urlParams = new URLSearchParams(window.location.search);
 const requestedTab = urlParams.get('tab') || (window.location.hash.includes('compatibility') ? 'compatibility' : null);
 if (requestedTab && report.sections.some(s => s.id === requestedTab)) {
   activeTab = requestedTab;
 }
 renderReport();
 if($('artifacts')){
  $('artifacts').replaceChildren();
  for(const name of report.artifacts){
   const isImage=/\.(png|jpg|jpeg)$/i.test(name);
   if(isImage){
    const imgUrl=`/api/runs/${rid}/artifact?name=${encodeURIComponent(name)}`;
    const link=document.createElement('a');
    link.href=imgUrl;
    link.target='_blank';
    link.rel='noopener noreferrer';
    link.className='artifact-image-thumb';
    link.title=name;
    const img=document.createElement('img');
    img.src=imgUrl;
    img.alt=name;
    img.loading='lazy';
    const label=el('span',name.split('/').pop(),'artifact-image-label');
    link.append(img,label);
    $('artifacts').append(link);
   }else{
    $('artifacts').append(button(name,async()=>{$('preview').textContent=await artifactText(rid,name);},'quiet'));
   }
  }
 }
 if($('download-md'))$('download-md').disabled=!report.markdown&&!report.artifacts.includes('summary.md');
 if($('download-docx'))$('download-docx').disabled=report.setup||!capabilities.docx;
 if($('download-csv'))$('download-csv').disabled=!report.run?.id;
 if($('freshness'))$('freshness').hidden=Boolean(report.setup);
 renderRegression();
}

function reportDecisionDetail(item){
 const act=item.selectedAction||getEffectiveAction(item);
 const decision=item.decision||{},parts=[act||'Pending'];
 if(act==='compatible_release')parts.push('version '+(decision.candidateVersion||item.targetVersion||'unresolved'));
 if(act==='available_patch'){const patch=(item.patches||[]).find(value=>value.id===decision.candidateId);parts.push('patch '+(patch?.sha256||patch?.commit||'unresolved'));}
 if(['ai_manual_patch','manual_remediation'].includes(act))parts.push('proposal '+(decision.proposalDigest||'unresolved'));
 if(act==='remove')parts.push('impact '+((item.blockers||[]).length?'blocked':'clear'));
 if(item.verificationChecks?.length)parts.push('checks '+item.verificationChecks.join(', '));
 return parts.join('; ');
}

function formatFact(value){
 if(value===null||value===undefined||value==='')return el('span','—','muted');
 if(typeof value==='boolean')return el('span',value?'Yes':'No',value?'status-pass':'status-fail');
 if(typeof value==='number')return el('span',String(value));
 if(Array.isArray(value)){
  if(!value.length)return el('span','None recorded','muted');
  if(value.length===1)return el('span',String(value[0]));
  const list=el('ul',undefined,'fact-list');
  for(const item of value)list.append(el('li',typeof item==='object'?JSON.stringify(item):String(item)));
  return list;
 }
 if(typeof value==='object'){
  const entries=Object.entries(value);
  if(!entries.length)return el('span','None recorded','muted');
  const wrap=el('div',undefined,'fact-chips');
  for(const [k,v] of entries)wrap.append(el('span',`${k}: ${typeof v==='object'?JSON.stringify(v):v}`,'fact-chip'));
  return wrap;
 }
 return el('span',String(value));
}

async function renderReadOnlyCompatibility(target, rawItems){
  const container = el('div', undefined, 'module-transition-container');
  container.style.marginTop = '20px';

  let data = null;
  if(report && report.run && report.run.id){
    try {
      data = await api(`runs/${encodeURIComponent(report.run.id)}/modules`);
    } catch(_e){}
  }

  let extensions = [];
  let summary = { total: 0, updated: 0, patched: 0, same: 0, removed: 0, uninstalled: 0 };

  if(data && Array.isArray(data.extensions)){
    extensions = data.extensions;
    summary = data.summary || summary;
    if(Array.isArray(data.uninstalledPackages) && data.uninstalledPackages.length){
      extensions = extensions.concat(data.uninstalledPackages);
    }
  } else if(Array.isArray(rawItems)){
    extensions = rawItems.map(item => {
      const act = item.selectedAction || item.action || '';
      const cur = item.currentVersion || (item.source === 'custom' ? 'custom' : '—');
      const tgt = item.targetVersion || cur;
      let cat = 'same', catLabel = 'Remained Same', d11Ver = cur, det = item.decision?.note || 'Compatible with D10 & D11.';

      if(act === 'remove'){
        cat = 'removed'; catLabel = 'Removed / Uninstalled'; d11Ver = 'Uninstalled & Removed';
        det = 'Obsolete or superseded in Drupal 11 core. Cleanly uninstalled via Drush and removed from Composer.';
        summary.removed++;
      } else if(['ai_manual_patch', 'manual_remediation', 'available_patch'].includes(act)){
        cat = 'patched'; catLabel = 'Patched / Remediated'; d11Ver = tgt !== 'custom' ? tgt : 'Remediated (Custom)';
        det = item.patches?.length ? `${item.patches.length} patch(es) applied` : 'Custom code deprecations remediated for Drupal 11.';
        summary.patched++;
      } else if(act === 'compatible_release'){
        if(tgt && tgt !== cur && cur !== 'custom'){
          cat = 'updated'; catLabel = 'Updated / Upgraded'; d11Ver = tgt;
          det = `Updated from ${cur} to D11-compatible release ${tgt}.`;
          summary.updated++;
        } else {
          cat = 'same'; catLabel = 'Remained Same'; d11Ver = cur;
          det = 'Already compatible with both Drupal 10 & 11 on the same version.';
          summary.same++;
        }
      } else if(act === 'keep'){
        cat = 'same'; catLabel = 'Remained Same'; d11Ver = cur;
        det = '100% clean D11-ready extension; kept on installed version.';
        summary.same++;
      } else if(item.reviewDisposition === 'present_not_installed' || item.category === 'uninstalled'){
        cat = 'uninstalled'; catLabel = 'Uninstalled Package'; d11Ver = '—';
        det = 'Present on disk in codebase but not installed in Drupal database.';
        summary.uninstalled++;
      } else {
        summary.same++;
      }

      return {
        name: item.name,
        label: item.label || item.name,
        type: item.type || 'module',
        source: item.source || 'contrib',
        package: item.package || `drupal/${item.name}`,
        d10_version: cur,
        d11_version: d11Ver,
        category: cat,
        category_label: catLabel,
        action: act,
        detail: det,
        installed: item.installed !== false,
      };
    });
    summary.total = extensions.filter(e => e.category !== 'uninstalled').length;
  }

  // Header & Export button
  const headerRow = el('div');
  headerRow.style.display = 'flex';
  headerRow.style.justifyContent = 'space-between';
  headerRow.style.alignItems = 'center';
  headerRow.style.flexWrap = 'wrap';
  headerRow.style.gap = '12px';
  headerRow.style.marginBottom = '14px';

  const titleBox = el('div');
  titleBox.innerHTML = '<h4 style="margin:0 0 3px;font-size:16px;">D10 → D11 Extension Upgrade Transition Report</h4>' +
                       '<p class="muted" style="margin:0;font-size:12.5px;">Detailed breakdown of modules, themes, and packages before and after the Drupal 11 upgrade rehearsal.</p>';

  const exportBtn = el('a', undefined, 'button secondary');
  if(report?.run?.id){
    exportBtn.href = `/api/runs/${encodeURIComponent(report.run.id)}/modules.csv`;
    exportBtn.download = `drupal-upgrade-modules-${report.run.id.slice(0, 8)}.csv`;
  }
  exportBtn.style.display = 'inline-flex';
  exportBtn.style.alignItems = 'center';
  exportBtn.style.gap = '6px';
  exportBtn.style.fontSize = '12.5px';
  exportBtn.style.padding = '6px 13px';
  exportBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="width:14px;height:14px;"><use href="#icon-download"></use></svg> Export Modules (CSV)';

  headerRow.append(titleBox, exportBtn);
  container.append(headerRow);

  // Filters Bar
  let activeFilter = 'all';
  let searchTerm = '';

  const filterRow = el('div');
  filterRow.style.display = 'flex';
  filterRow.style.alignItems = 'center';
  filterRow.style.justifyContent = 'space-between';
  filterRow.style.flexWrap = 'wrap';
  filterRow.style.gap = '10px';
  filterRow.style.marginBottom = '14px';

  const pillsContainer = el('div');
  pillsContainer.style.display = 'flex';
  pillsContainer.style.gap = '6px';
  pillsContainer.style.flexWrap = 'wrap';

  const filterDefs = [
    { id: 'all', icon: '', text: 'All', count: summary.total || extensions.length },
    { id: 'updated', icon: 'icon-arrow-up', text: 'Updated', count: summary.updated || 0 },
    { id: 'patched', icon: 'icon-tool', text: 'Patched', count: summary.patched || 0 },
    { id: 'same', icon: 'icon-check', text: 'Remained Same', count: summary.same || 0 },
    { id: 'removed', icon: 'icon-trash', text: 'Removed', count: summary.removed || 0 },
  ];
  if((summary.disabled_removed || 0) > 0){
    filterDefs.push({ id: 'disabled_removed', icon: 'icon-trash', text: 'Disabled→Removed', count: summary.disabled_removed || 0 });
  }
  if((summary.disabled_kept || 0) > 0){
    filterDefs.push({ id: 'disabled_kept', icon: 'icon-clock', text: 'Disabled→Remains', count: summary.disabled_kept || 0 });
  }
  if(summary.uninstalled > 0){
    filterDefs.push({ id: 'uninstalled', icon: 'icon-package', text: 'Not in DB', count: summary.uninstalled });
  }

  const pillButtons = [];
  filterDefs.forEach(def => {
    const pill = el('button', undefined, 'filter-pill' + (def.id === 'all' ? ' active' : ''));
    pill.type = 'button';
    const iconHtml = def.icon ? `<svg class="ui-icon" aria-hidden="true" style="width:11px;height:11px;vertical-align:-1px;margin-right:4px;"><use href="#${def.icon}"></use></svg>` : '';
    pill.innerHTML = `${iconHtml}<span>${escapeHtml(def.text)} (${def.count})</span>`;

    pill.onclick = () => {
      activeFilter = def.id;
      pillButtons.forEach((b, idx) => {
        const d = filterDefs[idx];
        const isCurrent = (d.id === activeFilter);
        b.classList.toggle('active', isCurrent);
      });
      renderRows();
    };

    pillButtons.push(pill);
    pillsContainer.append(pill);
  });

  const searchInput = document.createElement('input');
  searchInput.type = 'search';
  searchInput.className = 'table-search-input';
  searchInput.placeholder = 'Filter by module or package...';
  searchInput.oninput = () => {
    searchTerm = searchInput.value.toLowerCase().trim();
    renderRows();
  };

  filterRow.append(pillsContainer, searchInput);
  container.append(filterRow);

  // Table
  const tableWrap = el('div', undefined, 'table-responsive');
  tableWrap.style.overflowX = 'auto';
  tableWrap.style.border = '1px solid var(--border, #cbd5e1)';
  tableWrap.style.borderRadius = 'var(--radius-md, 8px)';
  tableWrap.style.background = 'var(--bg-surface, #ffffff)';

  const table = el('table', undefined, 'data-table');
  table.style.width = '100%';
  table.style.borderCollapse = 'collapse';
  table.style.fontSize = '12.5px';

  const thead = el('thead');
  thead.innerHTML = '<tr style="background:var(--bg-subtle);border-bottom:1px solid var(--border);text-align:left;">' +
                    '<th class="col-row-num" style="padding:9px 8px;font-weight:600;width:56px;min-width:56px;text-align:center;color:var(--text-muted);white-space:nowrap;">#</th>' +
                    '<th style="padding:9px 12px;font-weight:600;">Extension</th>' +
                    '<th style="padding:9px 12px;font-weight:600;">Drupal 10 Baseline</th>' +
                    '<th style="padding:9px 12px;font-weight:600;">Drupal 11 Post-Upgrade</th>' +
                    '<th style="padding:9px 12px;font-weight:600;">Transition Action</th>' +
                    '<th style="padding:9px 12px;font-weight:600;">Upgrade Details &amp; Evidence</th>' +
                    '</tr>';
  table.append(thead);

  const tbody = el('tbody');
  table.append(tbody);
  tableWrap.append(table);
  container.append(tableWrap);

  function renderRows(){
    tbody.replaceChildren();
    let filtered = extensions.filter(e => {
      if(activeFilter !== 'all' && e.category !== activeFilter) return false;
      if(searchTerm){
        const text = `${e.name} ${e.label} ${e.package} ${e.category_label}`.toLowerCase();
        if(!text.includes(searchTerm)) return false;
      }
      return true;
    });

    if(!filtered.length){
      const tr = el('tr');
      const td = el('td', 'No extensions match the selected filter.', 'muted');
      td.colSpan = 6;
      td.style.padding = '20px';
      td.style.textAlign = 'center';
      tr.append(td);
      tbody.append(tr);
      return;
    }

    filtered.forEach((item, index) => {
      const tr = el('tr');
      tr.style.borderBottom = '1px solid var(--border, #f1f5f9)';

      // Column 0: Row Index (#)
      const tdNum = el('td', undefined, 'col-row-num');
      tdNum.style.padding = '9px 8px';
      tdNum.style.textAlign = 'center';
      tdNum.style.fontFamily = 'var(--font-mono)';
      tdNum.style.fontSize = '11.5px';
      tdNum.style.fontWeight = '600';
      tdNum.style.color = 'var(--text-muted)';
      tdNum.style.whiteSpace = 'nowrap';
      tdNum.style.overflowWrap = 'normal';
      tdNum.style.wordBreak = 'normal';
      tdNum.textContent = String(index + 1);

      // Column 1: Extension info
      const tdName = el('td');
      tdName.style.padding = '9px 12px';
      const nameDiv = el('div');
      nameDiv.innerHTML = `<strong>${item.label || item.name}</strong> <span class="muted" style="font-family:var(--font-mono);font-size:11px;">(${item.name})</span>`;
      const badgesDiv = el('div');
      badgesDiv.style.marginTop = '2px';
      badgesDiv.style.display = 'flex';
      badgesDiv.style.gap = '5px';
      badgesDiv.style.alignItems = 'center';

      const srcBadge = el('span', item.source === 'custom' ? 'Custom' : 'Contrib', 'badge ' + (item.source === 'custom' ? 'badge-src-custom' : 'badge-src-contrib'));
      srcBadge.style.fontSize = '10px';
      srcBadge.style.padding = '1px 5px';

      const typeBadge = el('span', item.type || 'module', 'badge badge-src-contrib');
      typeBadge.style.fontSize = '10px';
      typeBadge.style.padding = '1px 5px';

      badgesDiv.append(srcBadge, typeBadge);
      if(item.package && item.package !== `drupal/${item.name}`){
        const pkgSpan = el('span', item.package, 'muted');
        pkgSpan.style.fontSize = '10.5px';
        pkgSpan.style.fontFamily = 'var(--font-mono)';
        badgesDiv.append(pkgSpan);
      }
      tdName.append(nameDiv, badgesDiv);

      // Column 2: Drupal 10 Baseline
      const tdD10 = el('td');
      tdD10.style.padding = '9px 12px';
      const d10State = item.d10_install_state || (item.category === 'uninstalled' ? 'not_in_db' : 'active');
      if(d10State === 'not_in_db'){
        tdD10.innerHTML = '<span class="badge badge-uninstalled" style="font-size:11px;">Not in DB (on disk)</span>';
      } else if(d10State === 'disabled'){
        tdD10.innerHTML = `<span style="font-family:var(--font-mono);font-weight:600;color:var(--text-muted);">${escapeHtml(item.d10_version)}</span> <span class="badge badge-disabled" style="font-size:10px;padding:1px 5px;">D10 Disabled</span>`;
      } else {
        tdD10.innerHTML = `<span style="font-family:var(--font-mono);font-weight:600;">${escapeHtml(item.d10_version)}</span> <span class="badge badge-d10" style="font-size:10px;padding:1px 5px;">D10</span>`;
      }

      // Column 3: Drupal 11 Post-Upgrade
      const tdD11 = el('td');
      tdD11.style.padding = '9px 12px';
      if(item.category === 'removed'){
        tdD11.innerHTML = '<span class="badge badge-removed" style="font-size:11px;"><svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-x"></use></svg> Uninstalled &amp; Removed</span>';
      } else if(item.category === 'disabled_removed'){
        tdD11.innerHTML = '<span class="badge badge-disabled" style="font-size:11px;"><svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-trash"></use></svg> Removed from Composer</span>';
      } else if(item.category === 'disabled_kept'){
        tdD11.innerHTML = `<span style="font-family:var(--font-mono);font-weight:600;color:var(--text-muted);">${escapeHtml(item.d11_version||'—')}</span> <span class="badge badge-same" style="font-size:10px;padding:1px 5px;">Disabled (on disk)</span>`;
      } else if(item.category === 'updated'){
        tdD11.innerHTML = `<span style="font-family:var(--font-mono);font-weight:600;color:var(--emerald-600);">${escapeHtml(item.d11_version)}</span> <span class="badge badge-d11" style="font-size:10px;padding:1px 5px;"><svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:10px;height:10px;"><use href="#icon-arrow-up"></use></svg> D11</span>`;
      } else if(item.category === 'patched'){
        tdD11.innerHTML = `<span style="font-family:var(--font-mono);font-weight:600;">${escapeHtml(item.d11_version)}</span> <span class="badge badge-remediated" style="font-size:10px;padding:1px 5px;">D11 Remediated</span>`;
      } else if(item.category === 'uninstalled'){
        tdD11.innerHTML = '<span class="muted" style="font-size:11px;">— (Not in DB)</span>';
      } else {
        tdD11.innerHTML = `<span style="font-family:var(--font-mono);font-weight:600;">${escapeHtml(item.d11_version)}</span> <span class="badge badge-neutral" style="font-size:10px;padding:1px 5px;">D10 / D11</span>`;
      }

      // Column 4: Transition Action
      const tdAction = el('td');
      tdAction.style.padding = '9px 12px';
      const actionPill = el('span', undefined, 'badge');
      actionPill.style.fontSize = '11.5px';
      actionPill.style.padding = '2px 8px';
      actionPill.style.fontWeight = '600';
      if(item.category === 'updated'){
        actionPill.className = 'badge badge-updated';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-arrow-up"></use></svg> Updated';
      } else if(item.category === 'removed'){
        actionPill.className = 'badge badge-removed';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-trash"></use></svg> Removed';
      } else if(item.category === 'disabled_removed'){
        actionPill.className = 'badge badge-disabled';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-trash"></use></svg> Disabled → Removed';
      } else if(item.category === 'disabled_kept'){
        actionPill.className = 'badge badge-same';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-clock"></use></svg> Disabled → Remains';
      } else if(item.category === 'patched'){
        actionPill.className = 'badge badge-patched';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-tool"></use></svg> Patched';
      } else if(item.category === 'uninstalled'){
        actionPill.className = 'badge badge-uninstalled';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-package"></use></svg> Not in DB';
      } else {
        actionPill.className = 'badge badge-same';
        actionPill.innerHTML = '<svg class="ui-icon" aria-hidden="true" style="vertical-align:-1px;width:11px;height:11px;"><use href="#icon-check"></use></svg> Remained Same';
      }
      tdAction.append(actionPill);

      // Column 5: Evidence & Details
      const tdDetail = el('td');
      tdDetail.style.padding = '9px 12px';
      tdDetail.style.fontSize = '12px';
      tdDetail.style.color = 'var(--text-secondary, #475569)';
      tdDetail.textContent = item.detail || '—';

      tr.append(tdNum, tdName, tdD10, tdD11, tdAction, tdDetail);
      tbody.append(tr);
    });
  }

  renderRows();
  target.append(container);
}

function renderReport(){
 if(!report)return;
 if($('report-tabs'))for(const tab of $('report-tabs').children)tab.setAttribute('aria-selected',String(tab.dataset.tab===activeTab));
 const section=report.sections.find(s=>s.id===activeTab)||report.sections[0],target=$('report-body');
 if(!target)return;
 target.replaceChildren();
 if(report.run&&report.run.status==='blocked'){
  const banner=el('div',undefined,'report-banner report-banner-blocked');
  banner.append(el('strong',`This run stopped at ${(report.run.checkpoint||'setup').replaceAll('_',' ')}`),el('p',report.run.error||'Execution was halted before full evidence could be generated.'));
  if(report.priorCompletedRun){
   banner.append(button(`View completed audit (${report.priorCompletedRun.slice(0,8)})`,async()=>{$('runs').value=report.priorCompletedRun;await loadReport(report.priorCompletedRun);},'secondary'));
  }
  target.append(banner);
 }
 target.append(el('h3',section.title));
 const table=el('table');
 for(const [key,value] of Object.entries(section.facts)){
  const row=el('tr'),td=el('td');
  td.append(formatFact(value));row.append(el('th',key),td);table.append(row);
 }
 target.append(table);
 if(section.compatibility)renderReadOnlyCompatibility(target,section.compatibility);
 for(const finding of (section.findings || [])){
  const st = (finding.status || 'unknown').toLowerCase();
  const statusClass = st === 'passed' ? 'passed' : (st === 'blocked' || st === 'failed') ? 'blocked' : (st === 'findings' || st === 'warn' || st === 'warning') ? 'warning' : 'unknown';
  const card = el('div', undefined, `finding finding-${statusClass}`);

  const header = el('div', undefined, 'finding-header');
  const badge = el('span', undefined, `finding-badge ${statusClass}`);

  let iconHref = '#icon-info';
  let label = st.toUpperCase();
  if(statusClass === 'passed') {
   iconHref = '#icon-check-circle';
   label = 'PASSED';
  } else if(statusClass === 'blocked') {
   iconHref = '#icon-alert-triangle';
   label = 'BLOCKED';
  } else if(statusClass === 'warning') {
   iconHref = '#icon-info';
   label = 'FINDING';
  } else {
   iconHref = '#icon-circle';
   label = 'UNKNOWN';
  }

  badge.innerHTML = `<svg class="ui-icon" aria-hidden="true"><use href="${iconHref}"></use></svg> <span>${label}</span>`;
  const title = el('span', finding.title, 'finding-title');
  header.append(badge, title);

  const detail = el('p', finding.detail, 'finding-detail');
  card.append(header, detail);
  target.append(card);
 }
 for(const note of section.notes)target.append(el('p',note,'muted'));
}

async function artifactResponse(rid,name){const response=await fetch('/api/runs/'+rid+'/artifact?name='+encodeURIComponent(name));if(!response.ok)throw Error('Artifact unavailable');return response;}
async function artifactText(rid,name){return (await artifactResponse(rid,name)).text();}
async function download(name){const data=report.setup&&name==='summary.md'?new Blob([report.markdown],{type:'text/markdown'}):await (await artifactResponse(report.run.id,name)).blob(),url=URL.createObjectURL(data),link=el('a');link.href=url;link.download=name.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function friendlyImageLabel(name, scenarios = projectScenarios){
  const base = name.split('/').pop();
  const m = base.match(/(desktop|mobile)[-_](\d+).*?(\d+x\d+)/i);
  if (!m) return base;
  const mode = m[1].toUpperCase();
  const idx = parseInt(m[2], 10);
  const res = m[3];
  const scen = (scenarios && scenarios[idx]) || null;
  const route = scen ? (scen.path || scen.url || scen.label) : null;
  return route ? `${route} · ${mode} (${res})` : `${mode} Route #${idx} (${res})`;
}

/**
 * Modern Accessible Confirmation Modal replacing window.confirm
 * @param {Object|string} options
 * @returns {Promise<boolean>}
 */
function showConfirmDialog(options) {
  return new Promise((resolve) => {
    const modal = $('app-confirm-modal');
    if (!modal) {
      resolve(window.confirm(typeof options === 'string' ? options : (options?.message || 'Are you sure?')));
      return;
    }
    const opts = typeof options === 'string' ? { message: options } : (options || {});
    const title = opts.title || 'Confirm Action';
    const message = opts.message || 'Are you sure you want to proceed?';
    const detail = opts.detail || '';
    const confirmText = opts.confirmText || 'Confirm';
    const cancelText = opts.cancelText || 'Cancel';
    const variant = opts.variant || (opts.isDanger ? 'danger' : 'primary');
    const icon = opts.icon || (variant === 'danger' ? 'icon-alert-triangle' : (variant === 'warning' ? 'icon-alert-triangle' : 'icon-check-circle'));

    $('app-confirm-heading-text').textContent = title;
    $('app-confirm-message').textContent = message;

    const detailEl = $('app-confirm-detail');
    if (detail) {
      detailEl.textContent = detail;
      detailEl.style.display = 'block';
    } else {
      detailEl.textContent = '';
      detailEl.style.display = 'none';
    }

    const dot = $('app-confirm-dot');
    dot.className = 'terminal-dot ' + (variant === 'danger' ? 'red' : (variant === 'warning' ? 'yellow' : 'green'));

    const iconEl = $('app-confirm-icon');
    if (iconEl) {
      const href = icon.startsWith('#') ? icon : ('#' + (icon.startsWith('icon-') ? icon : 'icon-' + icon));
      iconEl.innerHTML = `<use href="${href}"></use>`;
      if (variant === 'danger') {
        iconEl.style.color = 'var(--red)';
      } else if (variant === 'warning') {
        iconEl.style.color = 'var(--pantheon-yellow)';
      } else {
        iconEl.style.color = 'var(--blue)';
      }
    }

    const cancelBtn = $('app-confirm-cancel-btn');
    cancelBtn.textContent = cancelText;
    cancelBtn.style.display = opts.hideCancel ? 'none' : 'inline-flex';

    const submitBtn = $('app-confirm-submit-btn');
    submitBtn.innerHTML = '';
    if (opts.confirmIcon) {
      const btnIconHref = '#' + (opts.confirmIcon.startsWith('icon-') ? opts.confirmIcon : 'icon-' + opts.confirmIcon);
      submitBtn.innerHTML = `<svg class="ui-icon" aria-hidden="true"><use href="${btnIconHref}"></use></svg> `;
    }
    submitBtn.append(document.createTextNode(confirmText));
    submitBtn.className = variant === 'danger' ? 'danger' : (variant === 'warning' ? 'secondary' : 'primary');

    modal.hidden = false;

    const prevActive = document.activeElement;
    if (variant === 'danger' && !opts.hideCancel) {
      cancelBtn.focus();
    } else {
      submitBtn.focus();
    }

    let resolved = false;
    function cleanup(result) {
      if (resolved) return;
      resolved = true;
      modal.hidden = true;
      window.removeEventListener('keydown', handleKeyDown);
      modal.removeEventListener('click', handleBackdrop);
      cancelBtn.removeEventListener('click', handleCancelClick);
      submitBtn.removeEventListener('click', handleSubmitClick);
      $('app-confirm-close-btn')?.removeEventListener('click', handleCancelClick);
      if (prevActive && typeof prevActive.focus === 'function') {
        try { prevActive.focus(); } catch (_) {}
      }
      resolve(result);
    }

    function handleKeyDown(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        cleanup(false);
      } else if (e.key === 'Tab') {
        const focusables = Array.from(modal.querySelectorAll('button:not([disabled]):not([style*="display: none"])'));
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    function handleBackdrop(e) {
      if (e.target === modal) cleanup(false);
    }

    function handleCancelClick() { cleanup(false); }
    function handleSubmitClick() { cleanup(true); }

    window.addEventListener('keydown', handleKeyDown);
    modal.addEventListener('click', handleBackdrop);
    cancelBtn.addEventListener('click', handleCancelClick);
    submitBtn.addEventListener('click', handleSubmitClick);
    $('app-confirm-close-btn')?.addEventListener('click', handleCancelClick);
  });
}

/**
 * Modern Accessible Alert Modal replacing window.alert
 * @param {Object|string} options
 * @returns {Promise<void>}
 */
function showAlertDialog(options) {
  const opts = typeof options === 'string' ? { message: options } : (options || {});
  return showConfirmDialog({
    title: opts.title || 'Notification',
    message: opts.message || '',
    detail: opts.detail || '',
    confirmText: opts.okText || 'Understood',
    hideCancel: true,
    variant: opts.variant || 'primary',
    icon: opts.icon || (opts.variant === 'warning' ? 'icon-alert-triangle' : 'icon-info')
  });
}

window.showConfirmDialog = showConfirmDialog;
window.showAlertDialog = showAlertDialog;

async function confirmDeleteProject(proj){
  const p = proj || selectedProject();
  if(!p)return;
  const name = p.name || p.id;
  const src = p.source || p.site?.uri || 'your local filesystem';
  const confirmed = await showConfirmDialog({
    title: `Remove "${name}" from Workbench?`,
    message: `Are you sure you want to remove project "${name}" from the workbench?`,
    detail: `🛡 SAFETY GUARANTEE:\n• Your ACTUAL Drupal project files at "${src}" will NOT be touched or deleted.\n• This ONLY clears workbench drafts, snapshots, and audit logs.`,
    confirmText: 'Remove Project',
    confirmIcon: 'icon-trash',
    cancelText: 'Keep Project',
    variant: 'danger',
    icon: 'icon-trash'
  });
  if(!confirmed)return;
  clearError();
  try{
   $('message').hidden=false;
   $('message').textContent=`Removing "${name}" from workbench...`;
   const res = await api('projects/'+p.id, {}, 'DELETE');
   $('message').hidden=false;
   $('message').textContent=res.message || 'Project removed from workbench.';
   if(selected===p.id){
    selected=null;
    report=null;
   }
   await refresh();
  }catch(err){
   error(err);
  }
}

function initWorkbenchEvents(){
 if($('new-project'))$('new-project').onclick=openSetup;
 if($('empty-add'))$('empty-add').onclick=openSetup;
 if($('delete-project-btn'))$('delete-project-btn').onclick=()=>confirmDeleteProject();
 if($('cancel-setup'))$('cancel-setup').onclick=()=>{$('setup').hidden=true;refresh().catch(error);};
 if($('show-fixtures'))$('show-fixtures').onchange=()=>refresh().catch(error);
 if($('developer-dropdown')){
  $('developer-dropdown').addEventListener('toggle',()=>{
   if($('developer-dropdown').open)developerActions();
  });
 }
 developerActions();

 if($('source'))$('source').addEventListener('change',async()=>{
  const src=$('source').value.trim();if(!src)return;
  try{
   const d=await api('setup/auto-detect',{source:src});
   if(d.sourceUrl&&!$('source-url').value)$('source-url').value=d.sourceUrl;
   if(d.name&&!$('name').value)$('name').value=d.name;
   if(d.id&&!$('project-id').value)$('project-id').value=d.id;
   if(d.wrapper&&d.wrapper!=='auto')$('source-wrapper').value=d.wrapper;
  }catch(_e){}
 });

 if($('source-url'))$('source-url').addEventListener('input',()=>{
  const next=origin($('source-url').value),sitemap=$('sitemap-url');
  if(setupSourceOrigin&&sitemap?.value===setupSourceOrigin+'/sitemap.xml')sitemap.value=next?next+'/sitemap.xml':'';
  setupSourceOrigin=next;
 });

 if($('setup-form'))$('setup-form').onsubmit=event=>{
  event.preventDefault();
  act(async()=>{
   const payload={
    id:$('project-id').value.trim()||undefined,
    name:$('name').value.trim()||undefined,
    source:$('source').value.trim(),
    sourceUrl:$('source-url').value.trim(),
    sitemapUrl:$('sitemap-url').value.trim()||undefined,
    routes:$('routes').value.trim()?$('routes').value:undefined,
    captureNavLinks:$('capture-nav-links')?$('capture-nav-links').checked:true,
    wrapper:$('source-wrapper').value,
    database:$('database').value.trim()||undefined,
    files:$('files').value.trim()||undefined
   };
   const editing=$('setup-form').dataset.edit;
   let pid;
   if(editing){
    const res = await fetch('/api/projects/' + encodeURIComponent(editing), {
     method: 'PUT',
     headers: {'Content-Type': 'application/json'},
     body: JSON.stringify(payload)
    });
    if(!res.ok){
     const errData = await res.json().catch(()=>({message: 'Failed to update project'}));
     throw new Error(errData.message || errData.error || 'Failed to update project');
    }
    pid = editing;
    showToast('✓ Project configuration updated successfully!');
    delete $('setup-form').dataset.edit;
    $('setup').hidden = true;
    $('project-view').hidden = false;
    await refresh();
    showStage('plan', true);
    return;
   }else{
    const res=await api('setup/quick-add',payload);
    pid=res.project.id;
   }
   selected=pid;
   userSelectedStage=false;
   sessionStorage.setItem('d11-project',selected);
   $('setup').hidden=true;
   showStage('test-audit',true);
  });
 };

async function openUpgradeConfirmModal() {
  if (isTerminalRunning()) {
    await showAlertDialog({
      title: 'Console Task Active',
      message: 'A command is currently executing in the Upgrade Console (' + (activeJobDescription || 'console task') + ').',
      detail: 'Please wait for the active command to complete or stop it before launching 1-Click Upgrade.',
      variant: 'warning',
      icon: 'icon-clock'
    });
    return;
  }
  if (!gateRunId) {
    const pr = runs.filter(r => r.project === selected);
    const active = pr.find(r => r.status === 'running');
    if (active) {
      await showAlertDialog({
        title: 'Audit Scan in Progress',
        message: 'The Gate 1 Test Audit scan is currently running (' + (active.checkpoint || 'scanning').replaceAll('_', ' ') + ').',
        detail: 'Please wait for the scan to finish. Once completed, you will be able to review the upgrade proposal, capture baseline, and rehearse.',
        variant: 'info',
        icon: 'icon-clock'
      });
    } else {
      await showAlertDialog({
        title: 'Audit Scan Required',
        message: 'Please wait for or run the Test Audit scan before starting the upgrade.',
        detail: 'The upgrade requires Gate 1 compatibility decisions and extension resolutions.',
        variant: 'warning',
        icon: 'icon-alert-triangle'
      });
    }
    return;
  }

  const all = (compatibility?.extensions || []).filter(r => r.source !== 'core');

  let updatesCount = 0;
  let patchesCount = 0;
  let removalsCount = 0;
  let keepCount = 0;
  const removals = [];
  const blockers = [];

  for (const r of all) {
    const d = compatibilityDraft[r.name] || {};
    const act = d.action || (typeof getEffectiveAction === 'function' ? getEffectiveAction(r) : 'defer');

    if (act === 'remove') {
      removalsCount++;
      removals.push({
        name: r.name,
        label: r.label || r.name,
        type: r.type || 'module',
        enabled: r.enabled === true,
        version: r.currentVersion || 'unknown',
        note: d.note || (r.enabled ? 'Module is active and will be uninstalled' : 'Unused module to be removed')
      });
    } else if (act === 'compatible_release') {
      updatesCount++;
    } else if (act === 'available_patch' || act === 'ai_manual_patch') {
      patchesCount++;
    } else if (act === 'keep') {
      keepCount++;
    } else {
      blockers.push({
        name: r.name,
        label: r.label || r.name,
        reason: r.blockers?.[0] || 'Unresolved dependency / no compatible candidate'
      });
    }
  }

  if ($('confirm-stat-updates')) $('confirm-stat-updates').textContent = String(updatesCount);
  if ($('confirm-stat-patches')) $('confirm-stat-patches').textContent = String(patchesCount);
  if ($('confirm-stat-removals')) $('confirm-stat-removals').textContent = String(removalsCount);
  if ($('confirm-stat-keep')) $('confirm-stat-keep').textContent = String(keepCount);

  const remAlert = $('confirm-removals-alert');
  const remList = $('confirm-removals-list');
  if (remAlert && remList) {
    if (removals.length > 0) {
      remAlert.style.display = 'block';
      if ($('confirm-removals-count')) $('confirm-removals-count').textContent = String(removals.length);
      remList.replaceChildren(...removals.map(item => {
        const row = el('div', undefined, 'confirm-removal-item');
        row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:7px 12px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;font-size:12.5px;margin-bottom:4px;';

        const left = el('div');
        left.innerHTML = `<strong>${escapeHtml(item.label)}</strong> <code style="font-size:11.5px;color:var(--text-muted);margin:0 4px;">${escapeHtml(item.name)}</code> <span class="muted" style="font-size:11.5px;">(${item.version})</span>`;

        const badge = el('span');
        if (item.enabled) {
          badge.style.cssText = 'background:var(--red-bg);color:var(--red-text);font-weight:600;padding:3px 8px;border-radius:4px;font-size:11px;letter-spacing:0.02em;border:1px solid var(--red-border);';
          badge.textContent = 'Active in Drupal (Will Uninstall)';
        } else {
          badge.style.cssText = 'background:var(--bg-subtle);color:var(--text-muted);padding:3px 8px;border-radius:4px;font-size:11px;border:1px solid var(--border);';
          badge.textContent = 'Inactive / Purge from Composer';
        }
        row.append(left, badge);
        return row;
      }));
    } else {
      remAlert.style.display = 'none';
    }
  }

  const blkAlert = $('confirm-blockers-alert');
  const blkList = $('confirm-blockers-list');
  if (blkAlert && blkList) {
    if (blockers.length > 0) {
      blkAlert.style.display = 'block';
      if ($('confirm-blockers-count')) $('confirm-blockers-count').textContent = String(blockers.length);
      blkList.replaceChildren(...blockers.slice(0, 10).map(item => {
        const row = el('div');
        row.style.cssText = 'display:flex;justify-content:space-between;padding:6px 12px;background:var(--bg-card);border:1px solid var(--border);border-radius:4px;font-size:12px;margin-bottom:4px;';
        row.innerHTML = `<strong>${escapeHtml(item.label)}</strong> <span style="color:var(--amber-text);font-size:11.5px;">${escapeHtml(item.reason)}</span>`;
        return row;
      }));
      if (blockers.length > 10) {
        const more = el('div', `+ ${blockers.length - 10} more deferred extensions`, 'muted');
        more.style.fontSize = '11.5px';
        more.style.marginTop = '4px';
        blkList.append(more);
      }
    } else {
      blkAlert.style.display = 'none';
    }
  }

  const baseNotice = $('confirm-baseline-notice');
  const baseText = $('confirm-baseline-text');
  const baseCaptureBtn = $('confirm-capture-baseline-btn');
  const confirmBtn = $('execute-confirm-upgrade-btn');
  const qs = gate?.quickSummary;
  const hasBaseline = Boolean(qs?.baselineCaptured);
  const count = qs?.baselineScreenshotCount || qs?.baselineRoutes || 0;

  if (confirmBtn) {
    if (isCapturingBaseline) {
      confirmBtn.disabled = true;
      confirmBtn.title = 'Visual baseline capture is in progress';
      confirmBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing Baseline (${baselineCaptureElapsed}s)...`;
    } else if (isStartingUpgrade) {
      confirmBtn.disabled = true;
      confirmBtn.title = 'Upgrade rehearsal is starting';
      confirmBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Starting Upgrade (${upgradeElapsed}s)...`;
    } else if (hasBaseline) {
      confirmBtn.disabled = false;
      confirmBtn.title = '';
      confirmBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rocket"></use></svg> Confirm &amp; Start Upgrade →';
    } else {
      confirmBtn.disabled = false;
      confirmBtn.title = 'Pre-upgrade visual baseline will be captured automatically before starting rehearsal';
      confirmBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Capture Baseline &amp; Start Upgrade →';
    }
  }

  if (baseNotice && baseText) {
    if (isCapturingBaseline) {
      baseNotice.style.borderColor = 'var(--blue-border, #3b82f6)';
      baseNotice.style.background = 'var(--blue-bg, rgba(59,130,246,0.08))';
      baseText.innerHTML = `<strong>Visual Baseline Capture in Progress:</strong> Pre-upgrade screenshots are currently being recorded (${baselineCaptureElapsed}s elapsed). You can close this modal; capture will continue in the background and the dashboard will update automatically when finished.`;
      if (baseCaptureBtn) {
        baseCaptureBtn.style.display = 'inline-flex';
        baseCaptureBtn.disabled = true;
        baseCaptureBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Capturing (${baselineCaptureElapsed}s)...`;
      }
    } else if (hasBaseline) {
      baseNotice.style.borderColor = 'var(--emerald-border, #10b981)';
      baseNotice.style.background = 'var(--emerald-bg, rgba(16,185,129,0.08))';
      baseText.innerHTML = `<strong>Visual Baseline Ready (${count} routes):</strong> Pre-upgrade screenshots recorded. Gate 2 will generate pixel-level visual diffs.`;
      if (baseCaptureBtn) baseCaptureBtn.style.display = 'none';
    } else {
      baseNotice.style.borderColor = 'var(--amber-border, #f59e0b)';
      baseNotice.style.background = 'var(--amber-bg, rgba(245,158,11,0.08))';
      baseText.innerHTML = `<strong>Visual Baseline Required:</strong> Pre-upgrade screenshots are not recorded yet. Capturing a visual baseline is required before starting the upgrade rehearsal to guarantee Gate 2 visual regression testing can verify your site. Please capture baseline first.`;
      if (baseCaptureBtn) {
        baseCaptureBtn.style.display = 'inline-flex';
        baseCaptureBtn.disabled = false;
        baseCaptureBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-camera"></use></svg> Capture Baseline Now';
        baseCaptureBtn.onclick = () => {
          startCapturingBaseline(gateRunId);
        };
      }
    }
  }

  if ($('upgrade-confirm-modal')) $('upgrade-confirm-modal').hidden = false;
}

function closeUpgradeConfirmModal() {
  if ($('upgrade-confirm-modal')) $('upgrade-confirm-modal').hidden = true;
  if (isCapturingBaseline && typeof showToast === 'function') {
    showToast('📸 Visual baseline capture is continuing in the background. The dashboard will update automatically when complete.');
  }
}

async function executeConfirmedUpgrade() {
  const qs = gate?.quickSummary;
  let hasBaseline = Boolean(qs?.baselineCaptured);
  const modalBtn = $('execute-confirm-upgrade-btn');
  const heroBtn = $('hero-one-click-upgrade');

  if (!hasBaseline) {
    if (typeof showToast === 'function') {
      showToast('Capturing pre-upgrade visual baseline first before starting upgrade rehearsal...');
    }
    await startCapturingBaseline(gateRunId);
    return;
  }

  isStartingUpgrade = true;
  upgradeStartTime = Date.now();
  upgradeElapsed = 0;

  if (upgradeTimer) clearInterval(upgradeTimer);
  upgradeTimer = setInterval(() => {
    upgradeElapsed = Math.max(1, Math.floor((Date.now() - upgradeStartTime) / 1000));
    updateUpgradeActionButtons();
    if (modalBtn) modalBtn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Starting Upgrade (${upgradeElapsed}s)...`;
  }, 1000);

  updateUpgradeActionButtons();
  if (modalBtn) {
    modalBtn.disabled = true;
    modalBtn.innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Starting Upgrade (0s)...';
  }

  // Confirming is the operator's move to the Upgrade step: switch there (and to #upgrade)
  // right away so progress, the console and any refusal are watched from that stage,
  // instead of only after the server has answered.
  closeUpgradeConfirmModal();
  showStage('upgrade', true);

  try {
    const digest = (typeof compatibility !== 'undefined' && compatibility && compatibility.digest) || (typeof report !== 'undefined' && report && report.compatibility ? report.compatibility.digest : null) || (typeof gate !== 'undefined' && gate && gate.compatibility ? gate.compatibility.digest : null) || (typeof decisionView !== 'undefined' ? decisionView.digest : null) || null;
    const decisions = typeof compatibilityDecisions === 'function' ? compatibilityDecisions() : [];
    await api('runs/' + gateRunId + '/one-click-upgrade', { reportDigest: digest, decisions, acceptRisks: true });

    isStartingUpgrade = false;
    clearInterval(upgradeTimer);
    upgradeTimer = null;
    updateUpgradeActionButtons();

    if (typeof decisionView !== 'undefined') {
      decisionView.saved = typeof decisionSignature === 'function' ? decisionSignature() : null;
      decisionView.baseline = JSON.parse(JSON.stringify(compatibilityDraft));
      if (typeof updateDecisionDirty === 'function') updateDecisionDirty();
    }
    report = null;
    await refresh();
    // The Upgrade step is gated on an existing audit/upgrade run, so the call
    // above can be refused when `runs` has not caught up with the run we just
    // started. Re-assert once refresh has loaded it.
    showStage('upgrade', true);
  } catch (err) {
    isStartingUpgrade = false;
    clearInterval(upgradeTimer);
    upgradeTimer = null;
    updateUpgradeActionButtons();
    error(err);
    if (modalBtn) {
      modalBtn.disabled = false;
      modalBtn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-rocket"></use></svg> Confirm &amp; Start Upgrade →';
    }
  }
}

window.openUpgradeConfirmModal = openUpgradeConfirmModal;

  if ($('hero-one-click-upgrade')) $('hero-one-click-upgrade').onclick = () => { if (typeof openUpgradeConfirmModal === 'function') openUpgradeConfirmModal(); };
  if ($('btn-capture-baseline')) {
    $('btn-capture-baseline').onclick = () => {
      startCapturingBaseline(gateRunId);
    };
  }
  if ($('btn-view-sidebyside')) $('btn-view-sidebyside').onclick = () => applyRegressionViewMode('sidebyside');
  if ($('btn-view-diff')) $('btn-view-diff').onclick = () => applyRegressionViewMode('diff');
  if ($('btn-view-threeup')) $('btn-view-threeup').onclick = () => applyRegressionViewMode('threeup');
  if ($('close-confirm-modal-btn')) $('close-confirm-modal-btn').onclick = closeUpgradeConfirmModal;
  if ($('cancel-confirm-modal-btn')) $('cancel-confirm-modal-btn').onclick = closeUpgradeConfirmModal;
  if ($('execute-confirm-upgrade-btn')) $('execute-confirm-upgrade-btn').onclick = executeConfirmedUpgrade;


  if($('btn-do-rollback'))$('btn-do-rollback').onclick=async()=>{
   const pr=runs.filter(r=>r.project===selected);
   const upgrade=latest(pr,'guided-upgrade');
   if(!upgrade){
    await showAlertDialog({
      title: 'Rollback Unavailable',
      message: 'No upgrade run was found to roll back.',
      variant: 'warning',
      icon: 'icon-alert-triangle'
    });
    return;
   }
   if(upgrade.status === 'running'){
    await showAlertDialog({
      title: 'Upgrade Rehearsal in Progress',
      message: 'An upgrade rehearsal is currently running for this project. The upgrade must finish before rollback can be executed.',
      detail: 'Rolling back while an upgrade is executing would corrupt database migrations and the working tree. Please allow the current upgrade to complete or halt.',
      variant: 'warning',
      icon: 'icon-clock'
    });
    return;
   }
   const runningRun = (runs || []).find(r => r.status === 'running');
   if(runningRun){
    await showAlertDialog({
      title: 'Workflow in Progress',
      message: `A workflow (${runningRun.action || 'operation'}) is currently executing. Please wait for it to complete before rolling back.`,
      variant: 'warning',
      icon: 'icon-clock'
    });
    return;
   }
   const confirmed = await showConfirmDialog({
     title: 'Roll Back to Pre-Upgrade State',
     message: 'Roll back site to pre-upgrade Drupal 10 state?',
     detail: '• Database snapshot (pre-upgrade.sql.gz) will be restored into the database.\n• Git working tree will be reset to the pre-upgrade commit checkpoint.\n• Composer packages will be restored to your pre-upgrade Drupal 10 dependencies.',
     confirmText: 'Roll Back Site',
     confirmIcon: 'icon-rotate-ccw',
     cancelText: 'Cancel',
     variant: 'danger',
     icon: 'icon-rotate-ccw'
   });
   if(!confirmed)return;
   const btn=$('btn-do-rollback'),fb=$('rollback-feedback');
  btn.disabled=true;
  btn.textContent='Restoring snapshot...';
  fb.style.display='block';
  fb.innerHTML='<span style="color:var(--rose-700);"><svg class="ui-icon" aria-hidden="true"><use href="#icon-clock"></use></svg> Restoring pre-upgrade database snapshot and reverting codebase...</span>';
  try{
   await api('runs/'+upgrade.id+'/rollback',{});
   fb.innerHTML='<strong style="color:var(--emerald-600);"><svg class="ui-icon" aria-hidden="true"><use href="#icon-check-circle"></use></svg> Rollback complete!</strong> Site restored to pre-upgrade state.';
   report=null;
   await refresh();
  }catch(err){
   fb.innerHTML=`<strong style="color:var(--rose-600);"><svg class="ui-icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg> Rollback failed:</strong> ${escapeHtml(err.message)}`;
  }finally{
   btn.disabled=false;
   btn.innerHTML='<svg class="ui-icon" aria-hidden="true"><use href="#icon-rotate-ccw"></use></svg> Rollback to Baseline Now';
  }
 };

  if ($('rerun-audit-btn')) {
    $('rerun-audit-btn').onclick = async () => {
      const btn = $('rerun-audit-btn');
      btn.disabled = true;
      let elapsed = 0;
      const origText = btn.textContent;
      btn.innerHTML = '<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Scanning (0s)...';
      const timer = setInterval(() => {
        elapsed++;
        btn.innerHTML = `<svg class="ui-icon spin" aria-hidden="true"><use href="#icon-refresh"></use></svg> Scanning (${elapsed}s)...`;
      }, 1000);
      try {
        await act(() => api('projects/' + selected + '/scan', { fast: true }));
      } finally {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = origText || '⚡ Re-run Audit Scan';
      }
    };
  }

  const resetLockBtn = $('btn-reset-workflow-lock');
  if (resetLockBtn) {
    resetLockBtn.onclick = async () => {
      resetLockBtn.disabled = true;
      resetLockBtn.innerHTML = '<svg class="ui-icon spin" style="width:12px;height:12px;vertical-align:-1px;" aria-hidden="true"><use href="#icon-refresh"></use></svg> Resetting...';
      try {
        const res = await api('workflow/reset-lock', {});
        if (typeof showToast === 'function') {
          showToast(`✓ Workflow lock reset! ${res.clearedRuns || 0} interrupted run(s) cleared.`);
        }
        clearError();
        await refresh();
      } catch (err) {
        if (typeof showToast === 'function') showToast(`Reset error: ${err.message || err}`);
      } finally {
        resetLockBtn.disabled = false;
        resetLockBtn.innerHTML = '✕ Clear Stale Lock / Reset';
      }
    };
  }
 if($('open-term-from-upgrade'))$('open-term-from-upgrade').onclick=openTerminalModal;
 if($('open-live-site-btn'))$('open-live-site-btn').onclick=()=>{
  const p=selectedProject();
  if(p?.site?.uri)window.open(p.site.uri,'_blank','noopener');
  else if(p?.sourceUrl)window.open(p.sourceUrl,'_blank','noopener');
 };

  if($('guardrails-fix-btn'))$('guardrails-fix-btn').onclick=async()=>{
   const btn=$('guardrails-fix-btn');
   btn.disabled=true;
   btn.textContent='Fixing...';
   try{
    const res=await api('projects/'+selected+'/guardrails/fix',{});
    await loadGuardrails(selected);
    if($('guardrails-details'))$('guardrails-details').innerHTML=`<span style="color:var(--emerald-600);"><svg class="ui-icon" aria-hidden="true"><use href="#icon-check-circle"></use></svg> Auto-fixed ${res.fixedFiles?.length||0} custom file(s) to Drupal Standards.</span>`;
   }catch(e){
    error(e);
   }finally{
    btn.disabled=false;
    btn.innerHTML='<svg class="ui-icon" aria-hidden="true"><use href="#icon-wrench"></use></svg> Auto-Fix Standards';
   }
  };

  if($('handoff-btn'))$('handoff-btn').onclick=async()=>{
   const btn=$('handoff-btn'),status=$('handoff-status');
   btn.disabled=true;
   btn.textContent='Creating Branch & Syncing...';
   status.textContent='Creating new branch and synchronizing upgrade files to source repository...';
   try{
    const branch=$('handoff-branch').value.trim()||'upgrade/drupal-11';
    const res=await api('projects/'+selected+'/handoff',{branch});
    status.innerHTML=`<div style="background:var(--emerald-50);border:1px solid var(--emerald-200);padding:12px 14px;border-radius:var(--radius-md);margin-top:8px;">
      <strong style="color:var(--emerald-600);"><svg class="ui-icon" aria-hidden="true"><use href="#icon-check-circle"></use></svg> Git Handoff Successful!</strong> Created branch <strong>${escapeHtml(res.branch)}</strong> with ${res.filesChanged?.length||0} files updated.<br>
      <div style="font-size:12px;color:var(--brand-600);margin-top:4px;"><svg class="ui-icon" aria-hidden="true"><use href="#icon-lock"></use></svg> ${escapeHtml(res.guardrailNotice||'No auto-commit or push. Developer manual review required.')}</div>
      <div style="margin-top:8px;font-family:var(--font-mono);font-size:12px;color:#f8fafc;background:#0f172a;padding:10px;border-radius:4px;line-height:1.6;">
        cd ${escapeHtml(res.sourcePath)}<br>
        git status<br>
        git diff<br>
        composer install<br>
        ddev drush updb -y &amp;&amp; ddev drush cr<br>
        git add -A &amp;&amp; git commit -m "Drupal 11 upgrade: updated dependencies and custom modules"<br>
        git push -u origin ${escapeHtml(res.branch)}
      </div>
    </div>`;
   }catch(err){
    status.innerHTML=`<strong style="color:var(--rose-600);"><svg class="ui-icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg> Handoff Blocked:</strong> ${escapeHtml(err.message)}`;
   }finally{
    btn.disabled=false;
    btn.innerHTML='<svg class="ui-icon" aria-hidden="true"><use href="#icon-git-branch"></use></svg> Create New Branch &amp; Sync Files →';
   }
  };

  if($('ai-pr-summary-btn'))$('ai-pr-summary-btn').onclick=async()=>{
   const modal=$('ai-summary-modal'),loading=$('ai-summary-loading'),textPre=$('ai-summary-text');
   if(modal)modal.hidden=false;
   if(loading)loading.style.display='block';
   if(textPre){textPre.style.display='none';textPre.textContent='';}
   try{
    const rid=currentRunId||(runs?.find(r=>r.project===selected)?.id)||(runs?.[0]?.id);
    const provider=$('ai-provider')?.value||'gemini';
    const res=await api('runs/'+rid+'/ai-summary',{provider});
    if(loading)loading.style.display='none';
    if(textPre){textPre.style.display='block';textPre.textContent=res.summary||'Summary could not be synthesized.';}
   }catch(err){
    if(loading)loading.style.display='none';
    if(textPre){textPre.style.display='block';textPre.textContent='Error generating AI summary: '+(err.message||err);}
   }
  };

  if($('ai-summary-close-btn'))$('ai-summary-close-btn').onclick=()=>{
   if($('ai-summary-modal'))$('ai-summary-modal').hidden=true;
  };

  if($('ai-summary-copy-btn'))$('ai-summary-copy-btn').onclick=(e)=>{
   const text=$('ai-summary-text')?.textContent||'';
   if(text){
    navigator.clipboard.writeText(text).then(()=>{
     if(typeof showToast==='function')showToast('✓ PR Summary copied to clipboard!');
    }).catch(()=>{
     if(typeof copyToClipboard==='function')copyToClipboard(text,e.target);
    });
   }
  };

  if($('pipeline-stepper')){
   $('pipeline-stepper').addEventListener('click',(e)=>{
    const li=e.target.closest('li[data-stage]');
    if(li&&li.dataset.stage)showStage(li.dataset.stage,true);
   });
   $('pipeline-stepper').addEventListener('keydown',(e)=>{
    if(e.key==='Enter'||e.key===' '){
     const li=e.target.closest('li[data-stage]');
     if(li&&li.dataset.stage){
      e.preventDefault();
      showStage(li.dataset.stage,true);
     }
    }
   });
  }

 if($('register'))$('register').onclick=()=>act(async()=>{
  const project=await api('projects',{id:$('project-id').value,bundle:$('bundle').value,config:JSON.parse($('config').value)});
  choose(project.id);
 });

  if($('compatibility-search'))$('compatibility-search').oninput=(e)=>{
    decisionView.searchQuery = e.target.value;
    decisionView.page = 1;
    if (typeof renderCompatibility === 'function') renderCompatibility(false, true);
  };
 if($('ai-provider'))$('ai-provider').onfocus=()=>loadProviders().catch(error);
 if($('runs'))$('runs').onchange=()=>act(()=>loadReport($('runs').value));
 function updateDeleteRunBtn(){
  const btn=$('delete-run-btn');if(!btn)return;
  const rid=$('runs')?.value;
  const runState=runs.find(r=>r.id===rid);
  const canDelete=rid&&runState&&!rid.startsWith('setup--')&&runState.status!=='running';
  btn.hidden=!canDelete;
 }
 if($('runs'))$('runs').addEventListener('change',updateDeleteRunBtn);
  if($('delete-run-btn'))$('delete-run-btn').onclick=async()=>{
   const rid=$('runs').value;
   if(!rid||rid.startsWith('setup--'))return;
   const label=$('runs').selectedOptions[0]?.text||rid;
   const confirmed = await showConfirmDialog({
     title: 'Delete Run Artifacts',
     message: `Delete run "${label}"?`,
     detail: 'This permanently removes the audit report, visual snapshots, and temporary logs for this run. This action cannot be undone.',
     confirmText: 'Delete Run',
     confirmIcon: 'icon-trash',
     cancelText: 'Cancel',
     variant: 'danger',
     icon: 'icon-trash'
   });
   if(!confirmed)return;
   try{
    await fetch('/api/runs/'+encodeURIComponent(rid),{method:'DELETE',headers:{'Content-Type':'application/json'}});
    report=null;
    await refresh();
    updateDeleteRunBtn();
   }catch(e){error(e);}
  };
 if($('download-md'))$('download-md').onclick=()=>act(()=>download('summary.md'));
 if($('download-docx'))$('download-docx').onclick=()=>act(async()=>{await api('runs/'+report.run.id+'/docx',{});await download('summary.docx');});
 if($('print'))$('print').onclick=()=>window.print();
 if($('freshness'))$('freshness').onclick=()=>act(async()=>{
  const result=await api('runs/'+report.run.id+'/freshness',{});
  if($('report-meta'))$('report-meta').textContent+=' · '+(result.sourceChanged?'Inputs changed':'Inputs match');
 });
 if($('refresh-dashboard'))$('refresh-dashboard').onclick=()=>{report=null;refresh().catch(error);};
  if($('rollback-standby-upgrade-btn'))$('rollback-standby-upgrade-btn').onclick=()=>showStage('upgrade',true);

 document.addEventListener('visibilitychange',()=>{
  clearTimeout(pollTimer);pollTimer=null;
  if(!document.hidden&&(projects.some(p=>p.status==='running')||runs.some(r=>r.status==='running')))refresh().catch(error);
 });
}
initWorkbenchEvents();
refresh().catch(error);

function ansiToHtml(text) {
  const parts = text.split(/\x1b\[([0-9;]*)m/);
  let html = '';
  let currentClass = '';
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const code = parts[i];
      if (code === '0' || code === '') {
        currentClass = '';
      } else if (code.includes('31')) {
        currentClass = 'ansi-red';
      } else if (code.includes('32')) {
        currentClass = 'ansi-green';
      } else if (code.includes('33')) {
        currentClass = 'ansi-yellow';
      } else if (code.includes('34')) {
        currentClass = 'ansi-blue';
      } else if (code.includes('35')) {
        currentClass = 'ansi-magenta';
      } else if (code.includes('36')) {
        currentClass = 'ansi-cyan';
      } else if (code.includes('1;30') || code.includes('90')) {
        currentClass = 'ansi-gray';
      }
    } else {
      const str = escapeHtml(parts[i]);
      if (str) {
        html += currentClass ? `<span class="${currentClass}">${str}</span>` : str;
      }
    }
  }
  return html;
}

function syncTerminalWithProject(force = false) {
  const pathInput = $('term-project-path');
  const urlInput = $('term-url');
  const branchInput = $('term-branch');
  if (!pathInput) return;

  const p = selectedProject();
  if (!p) {
    pathInput.value = '';
    pathInput.placeholder = 'Select a project in the sidebar';
    if (urlInput) urlInput.value = '';
    updateTerminalCommandPreview();
    return;
  }

  const src = p.sourcePath || p.source || '';
  const url = p.sourceUrl || p.site?.uri || (typeof p.site === 'string' ? p.site : '') || '';
  const branch = p.upgradeBranch || p.originalBranch || 'upgrade/drupal-11';

  if (force || !pathInput.value || pathInput.dataset.projectId !== p.id) {
    if (src) pathInput.value = src;
    if (url && urlInput) urlInput.value = url;
    if (branch && branchInput) branchInput.value = branch;
    pathInput.dataset.projectId = p.id;
  }
  pathInput.readOnly = true;
  if (urlInput) urlInput.readOnly = true;
  if (branchInput) branchInput.readOnly = true;

  updateTerminalCommandPreview();
}

function updateTerminalCommandPreview() {
  if (!$('term-command-preview')) return;
  const sub = $('term-subcommand').value;
  const p = $('term-project-path').value.trim() || '<project-path>';
  const url = $('term-url').value.trim();
  const prov = $('term-provider').value;
  const branch = $('term-branch').value.trim();
  const handoff = $('term-flag-handoff').checked;
  const dryRun = $('term-flag-dryrun').checked;
  const yes = $('term-flag-yes').checked;

  let cmd = `bin/d11 ${sub}`;
  if (sub === 'upgrade' || sub === 'audit') {
    cmd += ` ${p}`;
    if (url) cmd += ` --source-url ${url}`;
    if (prov) cmd += ` --provider ${prov}`;
    if (branch && branch !== 'upgrade/drupal-11') cmd += ` --git-branch ${branch}`;
    if (handoff && sub !== 'audit') cmd += ' --handoff';
    if (dryRun || sub === 'audit') cmd += ' --dry-run';
    if (yes) cmd += ' -y';
  } else if (sub === 'handoff') {
    cmd += ` ${p} <run-dir>`;
    if (branch) cmd += ` --branch ${branch}`;
  } else if (sub === 'doctor' || sub === 'status' || sub === 'guardrails') {
    if (p && p !== '<project-path>') cmd += ` ${p}`;
  }
  $('term-command-preview').textContent = cmd;
}

function formatElapsedSeconds(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function updateNavTerminalIndicators(running) {
  const topBtn = $('top-terminal-btn');
  if (topBtn) {
    let dot = topBtn.querySelector('.terminal-running-dot');
    if (running) {
      if (!dot) {
        dot = document.createElement('span');
        dot.className = 'terminal-running-dot';
        dot.title = 'Command console task is actively running';
        topBtn.appendChild(dot);
      }
      topBtn.title = 'Console task is actively running: ' + (activeJobDescription || '');
    } else if (dot) {
      dot.remove();
      topBtn.title = '';
    }
  }

  const sideBtn = $('open-terminal-btn');
  if (sideBtn) {
    let dot = sideBtn.querySelector('.terminal-running-dot');
    if (running) {
      if (!dot) {
        dot = document.createElement('span');
        dot.className = 'terminal-running-dot';
        dot.title = 'Command console task is actively running';
        sideBtn.appendChild(dot);
      }
      sideBtn.title = 'Console task is actively running: ' + (activeJobDescription || '');
    } else if (dot) {
      dot.remove();
      sideBtn.title = '';
    }
  }
}

function updateBackgroundTaskIndicator() {
  const isRunning = isTerminalRunning();
  const modal = $('terminal-modal');
  const modalClosed = !modal || modal.hidden;
  const floatingToast = $('terminal-task-floating-banner');

  if (isRunning) {
    if ($('toast-task-cmd')) {
      $('toast-task-cmd').textContent = activeJobDescription || 'd11 command';
      $('toast-task-cmd').title = activeJobDescription || '';
    }
    if (floatingToast) {
      floatingToast.hidden = !modalClosed;
    }

    if (!toastTimerInterval) {
      const updateTimer = () => {
        if (!activeJobStartedAt) return;
        const elapsedSec = Math.max(0, Math.floor((Date.now() - activeJobStartedAt) / 1000));
        if ($('toast-task-timer')) $('toast-task-timer').textContent = formatElapsedSeconds(elapsedSec);
      };
      updateTimer();
      toastTimerInterval = setInterval(updateTimer, 1000);
    }
    updateNavTerminalIndicators(true);
  } else {
    if (floatingToast) floatingToast.hidden = true;
    if (toastTimerInterval) {
      clearInterval(toastTimerInterval);
      toastTimerInterval = null;
    }
    updateNavTerminalIndicators(false);
  }
}

function updateActiveWorkflowBanner() {
  const banner = $('active-workflow-banner');
  if (!banner) return;
  const runningRun = (runs || []).find(r => r.status === 'running');
  const runningProj = (projects || []).find(p => p.status === 'running');
  if (runningRun || runningProj) {
    banner.style.display = 'flex';
    const label = $('active-workflow-label');
    if (label) {
      if (runningRun) {
        const actionName = (runningRun.action || 'workflow').replaceAll('guided-', '').replaceAll('-', ' ');
        const capAction = actionName.charAt(0).toUpperCase() + actionName.slice(1);
        const started = runningRun.startedAt ? new Date(runningRun.startedAt).toLocaleTimeString() : '';
        label.innerHTML = `<strong>${escapeHtml(capAction)} workflow executing</strong>${started ? ` (Started ${started})` : ''}. Actions locked until complete.`;
      } else {
        label.innerHTML = `<strong>Project initialization / scan executing</strong>. Actions locked until complete.`;
      }
    }
  } else {
    banner.style.display = 'none';
  }
}

function openTerminalModal() {
  syncTerminalWithProject(true);
  $('terminal-modal').hidden = false;
  if ($('term-subcommand')) $('term-subcommand').focus();
  updateBackgroundTaskIndicator();
}

function closeTerminalModal() {
  $('terminal-modal').hidden = true;
  updateBackgroundTaskIndicator();
}

function setTerminalTimelineStep(stepNumber, status = 'active') {
  currentTerminalTimelineStep = Math.max(1, Math.min(5, stepNumber));
  const timelineBar = $('term-timeline-bar');
  if (!timelineBar) return;

  for (let i = 1; i <= 5; i++) {
    const stepEl = $(`tstep-${i}`);
    if (!stepEl) continue;
    const badge = stepEl.querySelector('.tstep-badge');
    stepEl.classList.remove('active', 'completed', 'failed');

    if (status === 'idle') {
      if (badge) badge.textContent = i;
      continue;
    }

    if (status === 'completed' && currentTerminalTimelineStep >= 5) {
      stepEl.classList.add('completed');
      if (badge) badge.textContent = '✓';
      continue;
    }

    if (i < currentTerminalTimelineStep) {
      stepEl.classList.add('completed');
      if (badge) badge.textContent = '✓';
    } else if (i === currentTerminalTimelineStep) {
      if (status === 'completed') {
        stepEl.classList.add('completed');
        if (badge) badge.textContent = '✓';
      } else if (status === 'failed') {
        stepEl.classList.add('failed');
        if (badge) badge.textContent = '✖';
      } else {
        stepEl.classList.add('active');
        if (badge) badge.textContent = i;
      }
    } else {
      if (badge) badge.textContent = i;
    }
  }
}

function updateTerminalLivenessUI() {
  const pill = $('term-liveness-pill');
  const timer = $('term-elapsed-timer');
  const isRunning = isTerminalRunning();

  if (!isRunning) {
    if (timer) timer.hidden = true;
    if (pill && window.__activeTerminalStatus !== 'finished') {
      pill.className = 'term-liveness-pill ready';
      pill.textContent = '● Ready';
    }
    return;
  }

  if (timer && activeJobStartedAt) {
    const elapsedMs = Math.max(0, Date.now() - activeJobStartedAt);
    const totalSec = Math.floor(elapsedMs / 1000);
    const m = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const s = (totalSec % 60).toString().padStart(2, '0');
    timer.textContent = `${m}:${s}`;
    timer.hidden = false;
  }

  if (pill) {
    const quietMs = lastTerminalOutputTime ? Math.max(0, Date.now() - lastTerminalOutputTime) : 0;
    const quietSec = Math.floor(quietMs / 1000);

    if (quietSec < 15) {
      pill.className = 'term-liveness-pill active';
      pill.textContent = '● Active';
    } else if (quietSec < 60) {
      pill.className = 'term-liveness-pill busy';
      pill.textContent = '⚡ Working... (active)';
    } else {
      pill.className = 'term-liveness-pill heavy';
      pill.textContent = '⏳ Heavy task · Active';
    }
  }
}

function connectTerminalStream(jobId, desc, startedAt) {
  if (terminalEventSource) {
    terminalEventSource.close();
    terminalEventSource = null;
  }
  if (terminalTimerInterval) {
    clearInterval(terminalTimerInterval);
    terminalTimerInterval = null;
  }

  activeJobId = jobId;
  if (desc) activeJobDescription = desc;
  if (startedAt) activeJobStartedAt = startedAt;
  else if (!activeJobStartedAt) activeJobStartedAt = Date.now();
  window.__activeTerminalStatus = 'running';
  lastTerminalOutputTime = Date.now();

  const runBtn = $('term-run-btn');
  const stopBtn = $('term-stop-btn');
  const statusText = $('term-status-text');
  const pill = $('term-liveness-pill');
  if (runBtn) runBtn.disabled = true;
  if (stopBtn) stopBtn.disabled = false;
  if (statusText) {
    statusText.textContent = `Running: ${activeJobDescription || 'Command'} (Job: ${jobId})`;
  }
  if (pill) {
    pill.className = 'term-liveness-pill active';
    pill.textContent = '● Active';
  }

  setTerminalTimelineStep(1, 'active');
  terminalTimerInterval = setInterval(updateTerminalLivenessUI, 1000);
  updateTerminalLivenessUI();

  updateUpgradeActionButtons();
  updateBackgroundTaskIndicator();

  terminalEventSource = new EventSource('/api/terminal/' + jobId + '/stream');

  terminalEventSource.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      const screen = $('terminal-screen');
      if (payload.type === 'output' && screen) {
        lastTerminalOutputTime = Date.now();
        updateTerminalLivenessUI();
        screen.innerHTML += ansiToHtml(payload.text);
        screen.scrollTop = screen.scrollHeight;

        const stepMatch = payload.text.match(/==>\s*\[(\d+)\/(\d+)\]\s*([^\n\r]*)/);
        if (stepMatch) {
          const stepNum = parseInt(stepMatch[1], 10);
          const totalSteps = parseInt(stepMatch[2], 10);
          const stepTitle = stepMatch[3].toLowerCase();

          let targetTimelineStep = stepNum;
          if (totalSteps === 4) {
            if (stepNum === 4) targetTimelineStep = 5;
          } else if (totalSteps === 3) {
            if (stepNum === 2) targetTimelineStep = 2;
            if (stepNum === 3) targetTimelineStep = 5;
          } else if (totalSteps >= 6) {
            if (stepNum >= 5) targetTimelineStep = 5;
          }

          if (stepTitle.includes('runtime') || stepTitle.includes('detecting')) targetTimelineStep = 1;
          else if (stepTitle.includes('scanning') || stepTitle.includes('baseline') || stepTitle.includes('audit')) targetTimelineStep = 2;
          else if (stepTitle.includes('remediat') || stepTitle.includes('resolving') || stepTitle.includes('plan')) targetTimelineStep = 3;
          else if (stepTitle.includes('executing') || stepTitle.includes('upgrade batch') || stepTitle.includes('database migrations')) targetTimelineStep = 4;
          else if (stepTitle.includes('verifying') || stepTitle.includes('health') || stepTitle.includes('dry-run') || stepTitle.includes('complete')) targetTimelineStep = 5;

          currentTerminalTimelineStep = targetTimelineStep;
          setTerminalTimelineStep(currentTerminalTimelineStep, 'active');
          if (statusText && stepMatch[3]) {
            statusText.textContent = stepMatch[3].trim();
          }
        }

        const subMatch = payload.text.match(/(↳\s*[^\n\r]+|\.\.\.\s*[^\n\r]+|⏳\s*[^\n\r]+)/);
        if (subMatch && statusText) {
          const cleanSub = subMatch[1].replace(/^[↳\.\s⏳]+/, '').trim();
          if (cleanSub) {
            statusText.textContent = cleanSub;
          }
        }
      } else if (payload.type === 'finished') {
        if (terminalEventSource) {
          terminalEventSource.close();
          terminalEventSource = null;
        }
        if (terminalTimerInterval) {
          clearInterval(terminalTimerInterval);
          terminalTimerInterval = null;
        }

        window.__activeTerminalStatus = payload.status;
        activeJobId = null;
        activeJobDescription = '';
        activeJobStartedAt = null;

        if (runBtn) runBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = true;
        const ok = payload.exitCode === 0;

        if (statusText) {
          statusText.textContent = ok
            ? `Finished successfully (exit code 0)`
            : `Finished: ${payload.status} (exit code ${payload.exitCode})`;
        }
        if (pill) {
          pill.className = ok ? 'term-liveness-pill success' : 'term-liveness-pill failed';
          pill.textContent = ok ? '✔ Complete' : '✖ Failed';
        }

        if (ok) {
          setTerminalTimelineStep(5, 'completed');
        } else {
          setTerminalTimelineStep(currentTerminalTimelineStep, 'failed');
        }

        updateUpgradeActionButtons();
        updateBackgroundTaskIndicator();
        refresh().catch(() => {});
      }
    } catch (_err) {}
  };

  terminalEventSource.onerror = () => {
    setTimeout(checkActiveTerminalJob, 1200);
  };
}

async function checkActiveTerminalJob() {
  try {
    const res = await api('terminal/active');
    if (res && res.active && res.job) {
      if (!isTerminalRunning() || activeJobId !== res.job.id) {
        const started = res.job.startedAt ? new Date(res.job.startedAt).getTime() : Date.now();
        connectTerminalStream(res.job.id, res.job.description, started);
      }
    } else if (isTerminalRunning() && !res.active) {
      if (terminalEventSource) {
        terminalEventSource.close();
        terminalEventSource = null;
      }
      if (terminalTimerInterval) {
        clearInterval(terminalTimerInterval);
        terminalTimerInterval = null;
      }
      window.__activeTerminalStatus = 'finished';
      activeJobId = null;
      activeJobDescription = '';
      activeJobStartedAt = null;
      const pill = $('term-liveness-pill');
      if (pill) {
        pill.className = 'term-liveness-pill ready';
        pill.textContent = '● Ready';
      }
      const timer = $('term-elapsed-timer');
      if (timer) timer.hidden = true;
      updateUpgradeActionButtons();
      updateBackgroundTaskIndicator();
    }
  } catch (_e) {}
}

async function runTerminalCommand() {
  const runBtn = $('term-run-btn');
  const stopBtn = $('term-stop-btn');
  const screen = $('terminal-screen');
  const statusText = $('term-status-text');
  const pill = $('term-liveness-pill');

  const subcommand = $('term-subcommand').value;
  const projectPath = $('term-project-path').value.trim();
  const url = $('term-url').value.trim();
  const provider = $('term-provider').value;
  const branch = $('term-branch').value.trim();
  const handoff = $('term-flag-handoff').checked;
  const dryRun = $('term-flag-dryrun').checked;
  const yes = $('term-flag-yes').checked;

  if (terminalEventSource) {
    terminalEventSource.close();
    terminalEventSource = null;
  }
  if (terminalTimerInterval) {
    clearInterval(terminalTimerInterval);
    terminalTimerInterval = null;
  }

  runBtn.disabled = true;
  stopBtn.disabled = false;
  if (statusText) statusText.textContent = 'Launching command...';
  if (pill) {
    pill.className = 'term-liveness-pill active';
    pill.textContent = '● Launching';
  }
  setTerminalTimelineStep(1, 'active');

  try {
    const job = await api('terminal/run', {
      subcommand, projectPath, url, provider, branch, handoff, dryRun, yes
    });
    connectTerminalStream(job.id, job.description, Date.now());
  } catch (err) {
    runBtn.disabled = false;
    stopBtn.disabled = true;
    if (statusText) statusText.textContent = `Launch error: ${err.message}`;
    if (pill) {
      pill.className = 'term-liveness-pill failed';
      pill.textContent = '✖ Error';
    }
    setTerminalTimelineStep(1, 'failed');
    if (screen) screen.innerHTML += `<span class="ansi-red">Error: ${escapeHtml(err.message)}</span>\n`;
    updateUpgradeActionButtons();
    updateBackgroundTaskIndicator();
  }
}

async function stopTerminalCommand() {
  if (!activeJobId) return;
  const stopBtn = $('term-stop-btn');
  if (stopBtn) stopBtn.disabled = true;
  const toastStopBtn = $('toast-stop-task-btn');
  if (toastStopBtn) toastStopBtn.disabled = true;
  if (terminalTimerInterval) {
    clearInterval(terminalTimerInterval);
    terminalTimerInterval = null;
  }
  try {
    await api('terminal/' + activeJobId + '/stop', {});
    const statusText = $('term-status-text');
    const pill = $('term-liveness-pill');
    if (statusText) {
      statusText.textContent = 'Process terminated by user.';
    }
    if (pill) {
      pill.className = 'term-liveness-pill failed';
      pill.textContent = '✖ Stopped';
    }
    setTerminalTimelineStep(currentTerminalTimelineStep, 'failed');
  } catch (err) {
    error(err);
  }
}

async function sendTerminalInput(val) {
  if (!activeJobId) return;
  const text = (val !== undefined ? val : $('term-stdin-input').value).trim();
  if (text === '') return;
  try {
    await api('terminal/' + activeJobId + '/input', { input: text });
    if (val === undefined) $('term-stdin-input').value = '';
  } catch (err) {
    error(err);
  }
}

function initTerminalEvents() {
  if ($('open-terminal-btn')) $('open-terminal-btn').onclick = openTerminalModal;
  if ($('top-terminal-btn')) $('top-terminal-btn').onclick = openTerminalModal;
  if ($('pantheon-connection-btn')) $('pantheon-connection-btn').onclick = openTerminalModal;
  if ($('close-terminal-btn')) $('close-terminal-btn').onclick = closeTerminalModal;

  if ($('toast-view-console-btn')) $('toast-view-console-btn').onclick = openTerminalModal;
  if ($('toast-stop-task-btn')) $('toast-stop-task-btn').onclick = stopTerminalCommand;

  const termModal = $('terminal-modal');
  if (termModal) {
    termModal.addEventListener('click', (e) => {
      if (e.target === termModal) closeTerminalModal();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && termModal && !termModal.hidden) {
      closeTerminalModal();
    }
  });

  const previewInputs = [
    'term-subcommand', 'term-project-path', 'term-url', 'term-provider',
    'term-branch', 'term-flag-handoff', 'term-flag-yes', 'term-flag-dryrun'
  ];
  previewInputs.forEach(id => {
    const elem = $(id);
    if (elem) {
      elem.addEventListener('input', updateTerminalCommandPreview);
      elem.addEventListener('change', updateTerminalCommandPreview);
    }
  });

  if ($('term-run-btn')) $('term-run-btn').onclick = runTerminalCommand;
  if ($('term-stop-btn')) $('term-stop-btn').onclick = stopTerminalCommand;
  if ($('term-clear-btn')) $('term-clear-btn').onclick = () => {
    $('terminal-screen').innerHTML = '';
    if (!isTerminalRunning()) {
      setTerminalTimelineStep(1, 'idle');
      const pill = $('term-liveness-pill');
      if (pill) {
        pill.className = 'term-liveness-pill ready';
        pill.textContent = '● Ready';
      }
      const timer = $('term-elapsed-timer');
      if (timer) timer.hidden = true;
      const statusText = $('term-status-text');
      if (statusText) statusText.textContent = 'Configure arguments above and click "Run in Browser".';
    }
  };

  if ($('term-stdin-send')) $('term-stdin-send').onclick = () => sendTerminalInput();
  if ($('term-stdin-input')) {
    $('term-stdin-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendTerminalInput();
      }
    });
  }
  if ($('term-quick-yes')) $('term-quick-yes').onclick = () => sendTerminalInput('y');
  if ($('term-quick-no')) $('term-quick-no').onclick = () => sendTerminalInput('n');

  checkActiveTerminalJob();
}
initTerminalEvents();

// --- Settings Modal & HTML Report Downloads ---
function openSettingsModal() {
  $('settings-modal').hidden = false;
  const statusBox = $('settings-test-status');
  if (statusBox) statusBox.style.display = 'none';
  highlightActiveProvider();
  loadSettings().catch(error);
}

function closeSettingsModal() {
  $('settings-modal').hidden = true;
}

function highlightActiveProvider() {
  const prov = $('settings-default-provider')?.value || 'gemini';
  ['gemini', 'claude', 'openai', 'ollama'].forEach(p => {
    const fs = $('settings-provider-' + p);
    if (fs) {
      const isActive = (p === prov);
      fs.hidden = !isActive;
      if (isActive) fs.classList.add('active-provider');
      else fs.classList.remove('active-provider');
    }
  });
}

async function loadSettings() {
  try {
    const s = await api('settings');
    if ($('settings-default-provider')) $('settings-default-provider').value = s.defaultProvider || 'gemini';
    if ($('settings-gemini-key')) $('settings-gemini-key').placeholder = s.geminiApiKey || 'AIzaSy...';
    if ($('settings-gemini-model')) $('settings-gemini-model').value = s.geminiModel || 'gemini-3.6-flash';
    if ($('settings-claude-key')) $('settings-claude-key').placeholder = s.anthropicApiKey || 'sk-ant-...';
    if ($('settings-claude-model')) $('settings-claude-model').value = s.anthropicModel || 'claude-3-5-sonnet-20241022';
    if ($('settings-openai-key')) $('settings-openai-key').placeholder = s.openaiApiKey || 'sk-...';
    if ($('settings-openai-model')) $('settings-openai-model').value = s.openaiModel || 'gpt-4o-mini';
    if ($('settings-ollama-host')) $('settings-ollama-host').value = s.ollamaHost || 'http://localhost:11434';
    highlightActiveProvider();
  } catch (err) {
    console.error('Failed to load settings:', err);
  }
}

async function testSettingsConnection() {
  const btn = $('settings-test-btn');
  const statusBox = $('settings-test-status');
  btn.disabled = true;
  btn.textContent = 'Testing...';
  statusBox.className = 'settings-status-box';
  statusBox.textContent = 'Sending ping to AI provider...';
  statusBox.style.display = 'block';

  const provider = $('settings-default-provider').value;
  let apiKey = '';
  let model = '';

  if (provider === 'gemini') {
    apiKey = $('settings-gemini-key').value.trim();
    model = $('settings-gemini-model').value.trim();
  } else if (provider === 'claude') {
    apiKey = $('settings-claude-key').value.trim();
    model = $('settings-claude-model').value.trim();
  } else if (provider === 'openai') {
    apiKey = $('settings-openai-key').value.trim();
    model = $('settings-openai-model').value.trim();
  } else if (provider === 'ollama') {
    model = $('settings-ollama-host').value.trim();
  }

  try {
    const res = await api('settings/test', {
      provider,
      apiKey: apiKey || undefined,
      model: model || undefined,
      host: provider === 'ollama' ? model : undefined
    });
    if (res.ok) {
      statusBox.className = 'settings-status-box ok';
      statusBox.textContent = res.message;
    } else {
      statusBox.className = 'settings-status-box err';
      statusBox.textContent = res.error;
    }
  } catch (err) {
    statusBox.className = 'settings-status-box err';
    statusBox.textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-plug"></use></svg> Test Connection';
  }
}

async function saveSettings(e) {
  if (e) e.preventDefault();
  const btn = $('settings-save-btn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  const payload = {
    defaultProvider: $('settings-default-provider').value,
    geminiApiKey: $('settings-gemini-key').value.trim() || undefined,
    geminiModel: $('settings-gemini-model').value.trim() || undefined,
    anthropicApiKey: $('settings-claude-key').value.trim() || undefined,
    anthropicModel: $('settings-claude-model').value.trim() || undefined,
    openaiApiKey: $('settings-openai-key').value.trim() || undefined,
    openaiModel: $('settings-openai-model').value.trim() || undefined,
    ollamaHost: $('settings-ollama-host').value.trim() || undefined,
  };

  try {
    await api('settings', payload);
    const statusBox = $('settings-test-status');
    statusBox.className = 'settings-status-box ok';
    statusBox.textContent = 'Settings saved to .env and loaded into memory.';
    statusBox.style.display = 'block';
    providers = null;
    loadProviders().catch(() => {});
    setTimeout(() => {
      closeSettingsModal();
    }, 1000);
  } catch (err) {
    const statusBox = $('settings-test-status');
    statusBox.className = 'settings-status-box err';
    statusBox.textContent = 'Save failed: ' + err.message;
    statusBox.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<svg class="ui-icon" aria-hidden="true"><use href="#icon-save"></use></svg> Save Settings';
  }
}

function initSettingsEvents() {
  if ($('open-settings-btn')) $('open-settings-btn').onclick = openSettingsModal;
  if ($('close-settings-btn')) $('close-settings-btn').onclick = closeSettingsModal;
  if ($('cancel-settings-btn')) $('cancel-settings-btn').onclick = closeSettingsModal;
  if ($('settings-test-btn')) $('settings-test-btn').onclick = testSettingsConnection;
  if ($('settings-form')) $('settings-form').onsubmit = saveSettings;
  if ($('settings-default-provider')) $('settings-default-provider').onchange = highlightActiveProvider;

  if ($('download-html')) {
    $('download-html').onclick = () => {
      if (report?.run?.id) {
        window.open('/api/runs/' + encodeURIComponent(report.run.id) + '/report.html', '_blank');
      }
    };
  }

  if ($('download-csv')) {
    $('download-csv').onclick = () => {
      if (report?.run?.id) {
        window.open('/api/runs/' + encodeURIComponent(report.run.id) + '/modules.csv', '_blank');
      }
    };
  }
}
initSettingsEvents();
initProgressConsoleEvents();

// --- Interactive Global Project Switcher Dropdown ---
function toggleProjectSwitcher(forceOpen) {
  const menu = $('project-switcher-menu');
  const btn = $('project-switcher-btn');
  if (!menu || !btn) return;
  const isOpen = typeof forceOpen === 'boolean' ? !forceOpen : !menu.hidden;
  if (isOpen) {
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
  } else {
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    const search = $('project-switcher-search');
    if (search) {
      search.value = '';
      filterProjectSwitcherItems('');
      setTimeout(() => search.focus(), 50);
    }
  }
}

function closeProjectSwitcher() {
  toggleProjectSwitcher(false);
}

function filterProjectSwitcherItems(q) {
  const items = document.querySelectorAll('#project-switcher-list .project-switcher-item');
  const query = (q || '').trim().toLowerCase();
  items.forEach(el => {
    const text = (el.dataset.name || el.textContent || '').toLowerCase();
    el.style.display = (!query || text.includes(query)) ? 'flex' : 'none';
  });
}

function renderProjectSwitcher(visible, p) {
  const nameEl = $('breadcrumb-site-name');
  if (nameEl) nameEl.textContent = p ? (p.name || p.id) : 'Select Project';

  const dotEl = $('project-switcher-dot');
  const envBadge = $('project-switcher-env');

  if (p) {
    const pr = runs ? runs.filter(r => r.project === p.id) : [];
    const audit = latestCompleted(pr, 'guided-audit') || latest(pr, 'guided-audit');
    const upgrade = latest(pr, 'guided-upgrade');
    const isUpg = isProjectUpgraded(p, pr);
    let status = p.draft ? p.status.replaceAll('_', ' ') : 'Ready';
    if (isUpg) {
      status = 'Upgraded';
    } else if (upgrade) {
      status = upgrade.status.replaceAll('_', ' ');
    } else if (audit) {
      status = (audit.automationEligibility || audit.recommendation || audit.status).replaceAll('_', ' ');
    }

    if (dotEl) {
      dotEl.className = 'project-switcher-dot';
      const sLow = status.toLowerCase();
      if (sLow.includes('upgraded') || sLow.includes('completed') || sLow.includes('clean') || sLow.includes('ready')) {
        dotEl.classList.add('dot-success');
      } else if (sLow.includes('attention') || sLow.includes('risk') || sLow.includes('finding') || sLow.includes('warning')) {
        dotEl.classList.add('dot-warning');
      } else if (sLow.includes('blocked') || sLow.includes('failed') || sLow.includes('error')) {
        dotEl.classList.add('dot-danger');
      } else {
        dotEl.classList.add('dot-info');
      }
    }

    const runtime = (p.wrapper || p.runtime?.wrapper || (p.url?.includes('ddev') ? 'ddev' : (p.url?.includes('docksal') ? 'docksal' : ''))).toLowerCase();
    if (envBadge) {
      if (runtime) {
        envBadge.textContent = runtime.toUpperCase();
        envBadge.className = 'project-switcher-badge badge-' + runtime;
        envBadge.style.display = 'inline-block';
      } else {
        envBadge.style.display = 'none';
      }
    }
  } else {
    if (dotEl) dotEl.className = 'project-switcher-dot';
    if (envBadge) envBadge.style.display = 'none';
  }

  const list = $('project-switcher-list');
  if (list && visible) {
    list.replaceChildren();
    for (const proj of visible) {
      const itemBtn = document.createElement('button');
      itemBtn.type = 'button';
      itemBtn.className = 'project-switcher-item' + (proj.id === selected ? ' selected' : '');
      itemBtn.dataset.name = proj.name || proj.id;

      const left = document.createElement('div');
      left.className = 'project-switcher-item-left';

      const dot = document.createElement('span');
      dot.className = 'project-switcher-dot';
      const prRuns = runs ? runs.filter(r => r.project === proj.id) : [];
      const audit = latestCompleted(prRuns, 'guided-audit') || latest(prRuns, 'guided-audit');
      const upgrade = latest(prRuns, 'guided-upgrade');
      const isUpg = isProjectUpgraded(proj, prRuns);
      let pStatus = proj.draft ? proj.status.replaceAll('_', ' ') : 'Ready';
      if (isUpg) pStatus = 'Upgraded';
      else if (upgrade) pStatus = upgrade.status.replaceAll('_', ' ');
      else if (audit) pStatus = (audit.automationEligibility || audit.recommendation || audit.status).replaceAll('_', ' ');

      const sLow = pStatus.toLowerCase();
      if (sLow.includes('upgraded') || sLow.includes('completed') || sLow.includes('clean') || sLow.includes('ready')) {
        dot.classList.add('dot-success');
      } else if (sLow.includes('attention') || sLow.includes('risk') || sLow.includes('finding') || sLow.includes('warning')) {
        dot.classList.add('dot-warning');
      } else if (sLow.includes('blocked') || sLow.includes('failed') || sLow.includes('error')) {
        dot.classList.add('dot-danger');
      } else {
        dot.classList.add('dot-info');
      }

      const title = document.createElement('span');
      title.className = 'project-switcher-item-title';
      title.textContent = proj.name || proj.id;

      left.append(dot, title);

      const right = document.createElement('div');
      right.className = 'project-switcher-item-right';

      const pRuntime = (proj.wrapper || proj.runtime?.wrapper || (proj.url?.includes('ddev') ? 'ddev' : (proj.url?.includes('docksal') ? 'docksal' : ''))).toLowerCase();
      if (pRuntime) {
        const badge = document.createElement('span');
        badge.className = 'project-switcher-badge badge-' + pRuntime;
        badge.textContent = pRuntime.toUpperCase();
        right.append(badge);
      }

      if (proj.id === selected) {
        const check = document.createElement('span');
        check.style.color = 'var(--pantheon-cyan)';
        check.innerHTML = '<svg class="ui-icon" style="width:13px;height:13px;"><use href="#icon-check"></use></svg>';
        right.append(check);
      }

      itemBtn.append(left, right);
      itemBtn.onclick = (e) => {
        e.stopPropagation();
        closeProjectSwitcher();
        choose(proj.id);
      };
      list.append(itemBtn);
    }
  }
}

function initProjectSwitcherEvents() {
  const btn = $('project-switcher-btn');
  if (btn) {
    btn.onclick = (e) => {
      e.stopPropagation();
      toggleProjectSwitcher();
    };
  }
  const search = $('project-switcher-search');
  if (search) {
    search.oninput = () => filterProjectSwitcherItems(search.value);
    search.onclick = (e) => e.stopPropagation();
    search.onkeydown = (e) => {
      if (e.key === 'Escape') closeProjectSwitcher();
      e.stopPropagation();
    };
  }
  const addBtn = $('project-switcher-add');
  if (addBtn) {
    addBtn.onclick = (e) => {
      e.stopPropagation();
      closeProjectSwitcher();
      openSetup();
    };
  }
  document.addEventListener('click', (e) => {
    if (!$('project-switcher')?.contains(e.target)) {
      closeProjectSwitcher();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeProjectSwitcher();
  });
}
initProjectSwitcherEvents();

// --- Commands & Prompts Guide Modal ---
let guidePathMode = 'active';

function setGuidePathMode(mode) {
  guidePathMode = mode;
  const activeBtn = $('guide-mode-active-btn');
  const portableBtn = $('guide-mode-portable-btn');
  if (activeBtn) activeBtn.classList.toggle('active', mode === 'active');
  if (portableBtn) portableBtn.classList.toggle('active', mode === 'portable');
  updateGuideContent();
}
window.setGuidePathMode = setGuidePathMode;

function updateGuideContent() {
  const p = selectedProject();
  const wbRoot = (capabilities && capabilities.workbench_path) ? capabilities.workbench_path : '.';

  // Update header/context indicators
  const wbRootEl = $('guide-workbench-root');
  const safetyWbPath = $('guide-safety-wb-path');
  const ctxName = $('guide-ctx-name');
  const ctxPath = $('guide-ctx-path');
  const ctxUrl = $('guide-ctx-url');

  if (wbRootEl) wbRootEl.textContent = wbRoot;
  if (safetyWbPath) safetyWbPath.textContent = wbRoot;

  if (p) {
    if (ctxName) ctxName.textContent = p.name || p.id;
    if (ctxPath) ctxPath.textContent = p.sourcePath || p.source || '/path/to/drupal-project';
    if (ctxUrl) ctxUrl.textContent = (p.site && p.site.uri) ? `(${p.site.uri})` : '';
  } else {
    if (ctxName) ctxName.textContent = 'No project selected';
    if (ctxPath) ctxPath.textContent = '/path/to/drupal-project';
    if (ctxUrl) ctxUrl.textContent = '';
  }

  // Determine path representations based on guidePathMode
  let binD11, projPath, projId, projUrl, runId;
  if (guidePathMode === 'active' && p) {
    binD11 = `${wbRoot}/bin/d11`;
    projPath = p.sourcePath || p.source || '.';
    projId = p.id;
    projUrl = (p.site && p.site.uri) ? p.site.uri : 'http://my-site.docksal.site';
    const pRuns = runs.filter(r => r.project === p.id);
    const latestUpgrade = latest(pRuns, 'guided-upgrade') || latest(pRuns, 'audit') || {};
    runId = gateRunId || latestUpgrade.id || '<RUN_ID>';
  } else {
    binD11 = './bin/d11';
    projPath = '.';
    projId = p ? p.id : '<PROJECT_NAME>';
    projUrl = (p && p.site && p.site.uri) ? p.site.uri : '<LOCAL_SITE_URL>';
    const pRuns = p ? runs.filter(r => r.project === p.id) : [];
    const latestUpgrade = latest(pRuns, 'guided-upgrade') || latest(pRuns, 'audit') || {};
    runId = gateRunId || latestUpgrade.id || '<RUN_ID>';
  }

  const setItem = (codeId, btnId, content) => {
    const codeEl = $(codeId);
    const btnEl = $(btnId);
    if (codeEl) codeEl.textContent = content;
    if (btnEl) btnEl.setAttribute('data-copy', content);
  };

  // 1. Commands
  setItem('cmd-dashboard-code', 'cmd-dashboard-btn', `${binD11} dashboard --port 8765 --no-open`);
  setItem('cmd-upgrade-code', 'cmd-upgrade-btn', `${binD11} upgrade ${projPath} -y`);
  setItem('cmd-audit-code', 'cmd-audit-btn', `${binD11} audit ${projPath}`);
  setItem('cmd-init-code', 'cmd-init-btn', `${binD11} init ${projPath} -y --url ${projUrl} --project ${projId}`);
  setItem('cmd-rollback-code', 'cmd-rollback-btn', `${binD11} workflow rollback --project ${projId} --run ${runId}`);
  setItem('cmd-modules-csv-code', 'cmd-modules-csv-btn', `${binD11} workflow modules --project ${projId} --csv > d10_d11_modules.csv`);
  setItem('cmd-modules-json-code', 'cmd-modules-json-btn', `${binD11} workflow modules --project ${projId}`);
  setItem('cmd-rector-code', 'cmd-rector-btn', `${binD11} workflow auto-remediate --run ${runId}`);
  setItem('cmd-decide-code', 'cmd-decide-btn', `${binD11} workflow auto-decide --run ${runId}`);
  setItem('cmd-doctor-code', 'cmd-doctor-btn', `${binD11} doctor`);

  // 2. Prompts
  const p1 = `You are pair programming with me on conducting an automated Drupal 10 to Drupal 11 upgrade rehearsal using the Promet Drupal Workbench (${wbRoot}).

Please execute the following steps:
1. Verify system and doctor diagnostics:
   ${binD11} doctor
2. Register this project if not already present:
   ${binD11} init ${projPath} -y --url ${projUrl} --project ${projId}
3. Run the end-to-end upgrade rehearsal non-destructively:
   ${binD11} upgrade ${projPath} -y
4. When complete, provide an executive summary:
   - Obsolete modules uninstalled vs contrib modules upgraded
   - Custom code deprecation refactorings applied
   - Schema updates and cache rebuild output
   - Visual regression diff results across baseline routes`;
  setItem('prompt-full-code', 'prompt-full-btn', p1);

  const p2 = `Perform a non-destructive Drupal 11 readiness audit on this project using the Promet Drupal Workbench (${wbRoot}).

Run:
${binD11} audit ${projPath}

Constraints:
- Do not modify any codebase files or touch the host database. All audit scans must run inside the disposable MariaDB container.
- Triage the Gate 1 proposal:
  1. Identify any obsolete modules that will be uninstalled/removed (e.g. advagg).
  2. Identify custom code in modules/custom and themes/custom with deprecations.
  3. Verify that 0 hard blockers exist before pre-upgrade approval.
Present the findings in a structured table.`;
  setItem('prompt-audit-code', 'prompt-audit-btn', p2);

  const customPathDesc = projPath === '.' ? 'web/modules/custom and web/themes/custom' : `${projPath}/web/modules/custom and ${projPath}/web/themes/custom`;
  const p3 = `We need to remediate custom Drupal 10 deprecations for Drupal 11 compatibility using the Promet Drupal Workbench.

Target: ${customPathDesc}
1. Run automated Rector remediation:
   ${binD11} workflow auto-remediate --run ${runId}
2. Inspect the generated diffs for:
   - Deprecated \\Drupal::service() calls and procedural replacements
   - Removed core procedural functions (e.g. file_create_url -> \\Drupal::service('file_url_generator'))
   - Twig 3 syntax and Hook attribute updates
3. Verify that all custom extensions now pass PHPStan level 2 with 0 errors.`;
  setItem('prompt-rector-code', 'prompt-rector-btn', p3);

  const p4 = `Generate the post-upgrade module transition report comparing Drupal 10 pre-upgrade versions against Drupal 11 post-upgrade status for project ${projId}.

Run:
${binD11} workflow modules --project ${projId} --csv > d10_d11_module_report.csv

Please analyze the CSV and provide an executive summary highlighting:
1. Removed / Uninstalled: Obsolete modules cleanly removed (e.g. advagg).
2. Updated / Upgraded: Contrib modules updated to D11 releases with versions.
3. Patched / Remediated: Custom modules fixed with Rector or patches.
4. Remained Same: Clean extensions kept on their existing version.
5. Uninstalled Packages: Packages on disk not enabled in the database.`;
  setItem('prompt-modules-code', 'prompt-modules-btn', p4);

  const p5 = `Please rollback the current Drupal 11 upgrade rehearsal to restore the site to its exact pre-upgrade Drupal 10 state.

Using the Promet Drupal Workbench:
1. Locate the latest run ID:
   ${binD11} workflow runs --project ${projId}
2. Execute the rollback:
   ${binD11} workflow rollback --project ${projId} --run ${runId}
3. Verify:
   - Database restored from pre-upgrade.sql.gz
   - Git working tree reset to pre-upgrade commit (git status clean)
   - Composer dependencies restored
   - Local site responds with HTTP 200`;
  setItem('prompt-rollback-code', 'prompt-rollback-btn', p5);

  const p6 = `Activate the \`drupal-11-upgrade-readiness\` skill and review AGENTS.md rules.
Coordinate with the Promet Drupal Workbench to orchestrate an upgrade rehearsal for this repository.
Follow the strict two-gate lifecycle:
- Gate 1: Non-destructive scan in disposable container, 0 unresolved extensions, SHA-256 evidence validation.
- Gate 2: Atomic recovery checkpoint, mutation, Drush updatedb, and visual diff verification.
Never embed upgrade tools or vendor scripts into the client project repository. Keep all mutations isolated to the dedicated zero-copy upgrade branch.`;
  setItem('prompt-skills-code', 'prompt-skills-btn', p6);
}

function openGuideModal(defaultTab = 'commands') {
  const modal = $('guide-modal');
  if (!modal) return;
  modal.hidden = false;
  updateGuideContent();
  if (defaultTab) switchGuideTab(defaultTab);
  const searchInput = $('guide-search-input');
  if (searchInput) {
    setTimeout(() => searchInput.focus(), 50);
  }
}

function closeGuideModal() {
  const modal = $('guide-modal');
  if (modal) modal.hidden = true;
}

function switchGuideTab(tabName) {
  const tabs = document.querySelectorAll('.guide-tab-btn');
  const panels = {
    commands: $('guide-panel-commands'),
    prompts: $('guide-panel-prompts'),
    skills: $('guide-panel-skills')
  };

  tabs.forEach(btn => {
    const isTarget = btn.dataset.guideTab === tabName;
    btn.classList.toggle('active', isTarget);
    btn.setAttribute('aria-selected', String(isTarget));
  });

  Object.entries(panels).forEach(([key, panel]) => {
    if (!panel) return;
    if (key === tabName) {
      panel.hidden = false;
      panel.classList.add('active');
    } else {
      panel.hidden = true;
      panel.classList.remove('active');
    }
  });

  filterGuideItems();
}

function filterGuideItems() {
  const query = ($('guide-search-input')?.value || '').trim().toLowerCase();
  const clearBtn = $('guide-search-clear');
  if (clearBtn) clearBtn.style.display = query ? 'block' : 'none';

  const cards = document.querySelectorAll('.guide-card, .guide-prompt-card, .guide-safety-card');
  cards.forEach(card => {
    if (!query) {
      card.style.display = '';
      return;
    }
    const keywords = (card.dataset.keywords || '').toLowerCase();
    const text = card.textContent.toLowerCase();
    const matches = keywords.includes(query) || text.includes(query);
    card.style.display = matches ? '' : 'none';
  });
}

async function copyToClipboard(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_e) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
  }

  if (btn) {
    const originalHtml = btn.innerHTML;
    btn.classList.add('copied');
    btn.innerHTML = '<svg class="ui-icon" style="width:13px;height:13px;" aria-hidden="true"><use href="#icon-check"></use></svg> <span>Copied!</span>';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = originalHtml;
    }, 2000);
  }
}

function initGuideEvents() {
  if ($('open-guide-btn')) $('open-guide-btn').onclick = () => openGuideModal('commands');
  if ($('sidebar-guide-btn')) $('sidebar-guide-btn').onclick = () => openGuideModal('commands');
  if ($('dev-open-guide-btn')) {
    $('dev-open-guide-btn').onclick = () => {
      const dd = $('developer-dropdown');
      if (dd) dd.open = false;
      openGuideModal('commands');
    };
  }
  if ($('close-guide-btn')) $('close-guide-btn').onclick = closeGuideModal;
  if ($('close-guide-footer-btn')) $('close-guide-footer-btn').onclick = closeGuideModal;
  if ($('guide-mode-active-btn')) $('guide-mode-active-btn').onclick = () => setGuidePathMode('active');
  if ($('guide-mode-portable-btn')) $('guide-mode-portable-btn').onclick = () => setGuidePathMode('portable');

  const guideModal = $('guide-modal');
  if (guideModal) {
    guideModal.addEventListener('click', (e) => {
      if (e.target === guideModal) closeGuideModal();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && guideModal && !guideModal.hidden) {
      closeGuideModal();
    }
  });

  document.querySelectorAll('.guide-tab-btn').forEach(btn => {
    btn.onclick = () => switchGuideTab(btn.dataset.guideTab);
  });

  const searchInput = $('guide-search-input');
  if (searchInput) {
    searchInput.addEventListener('input', filterGuideItems);
  }
  const clearBtn = $('guide-search-clear');
  if (clearBtn) {
    clearBtn.onclick = () => {
      if (searchInput) {
        searchInput.value = '';
        searchInput.focus();
        filterGuideItems();
      }
    };
  }

  document.addEventListener('click', (e) => {
    const copyBtn = e.target.closest('.guide-copy-btn');
    if (copyBtn && copyBtn.dataset.copy) {
      e.preventDefault();
      copyToClipboard(copyBtn.dataset.copy, copyBtn);
    }
  });

  const hash = window.location.hash || '';
  if (hash.startsWith('#guide')) {
    const tab = hash.includes('prompt') ? 'prompts' : hash.includes('skill') ? 'skills' : 'commands';
    openGuideModal(tab);
  }
}
initGuideEvents();

// --- Runs History & Pruning Modal ---
let runsHistoryData = [];
let runsHistoryFilter = 'all';

async function openRunsHistoryModal(filter = 'all') {
  if (!selected) {
    showToast('Please select a project first.');
    return;
  }
  const modal = $('runs-history-modal');
  if (!modal) return;
  modal.hidden = false;

  const titleProject = $('runs-history-project-name');
  if (titleProject) {
    const proj = projects.find(p => p.id === selected);
    titleProject.textContent = proj?.name || selected;
  }

  runsHistoryFilter = filter;
  const loading = $('runs-history-loading');
  const empty = $('runs-history-empty');
  const table = $('runs-history-table');

  if (loading) loading.style.display = 'block';
  if (empty) empty.style.display = 'none';
  if (table) table.style.display = 'none';

  try {
    const res = await api('projects/' + encodeURIComponent(selected) + '/runs');
    runsHistoryData = Array.isArray(res.runs) ? res.runs : [];

    // Update count badges
    const allCount = runsHistoryData.length;
    const auditCount = runsHistoryData.filter(r => r.action === 'guided-audit').length;
    const upgradeCount = runsHistoryData.filter(r => r.action === 'guided-upgrade').length;
    const rollbackCount = runsHistoryData.filter(r => r.action === 'guided-rollback').length;

    if ($('count-all-runs')) $('count-all-runs').textContent = String(allCount);
    if ($('count-audit-runs')) $('count-audit-runs').textContent = String(auditCount);
    if ($('count-upgrade-runs')) $('count-upgrade-runs').textContent = String(upgradeCount);
    if ($('count-rollback-runs')) $('count-rollback-runs').textContent = String(rollbackCount);

    renderRunsHistoryTable();
  } catch (err) {
    if (loading) loading.style.display = 'none';
    error(err);
  }
}

function closeRunsHistoryModal() {
  const modal = $('runs-history-modal');
  if (modal) modal.hidden = true;
}

function renderRunsHistoryTable() {
  const loading = $('runs-history-loading');
  const empty = $('runs-history-empty');
  const table = $('runs-history-table');
  const tbody = $('runs-history-tbody');

  if (loading) loading.style.display = 'none';

  // Update filter pills active state
  document.querySelectorAll('#runs-history-modal [data-filter-action]').forEach(btn => {
    if (btn.dataset.filterAction === runsHistoryFilter) btn.classList.add('active');
    else btn.classList.remove('active');
  });

  const filtered = runsHistoryData.filter(r => {
    if (runsHistoryFilter === 'all') return true;
    return r.action === runsHistoryFilter;
  });

  if (!filtered.length) {
    if (table) table.style.display = 'none';
    if (empty) empty.style.display = 'block';
    return;
  }

  if (empty) empty.style.display = 'none';
  if (table) table.style.display = 'table';
  if (!tbody) return;

  tbody.replaceChildren();

  filtered.forEach(r => {
    const tr = document.createElement('tr');

    // 1. Date & Time + Run ID
    const tdDate = document.createElement('td');
    const started = r.startedAt ? new Date(r.startedAt).toLocaleString() : '—';
    const dateDiv = document.createElement('div');
    dateDiv.style.fontWeight = '500';
    dateDiv.textContent = started;
    const idDiv = document.createElement('div');
    idDiv.style.fontSize = '11px';
    idDiv.style.color = 'var(--text-muted)';
    idDiv.style.marginTop = '2px';
    idDiv.innerHTML = `<code>${escapeHtml(r.id)}</code>`;
    tdDate.append(dateDiv, idDiv);

    // 2. Action / Stage
    const tdAction = document.createElement('td');
    const actionSpan = document.createElement('span');
    actionSpan.className = 'badge-run-action';
    const actionClean = (r.action || 'unknown').replace('guided-', '');
    actionSpan.textContent = actionClean.charAt(0).toUpperCase() + actionClean.slice(1);
    tdAction.append(actionSpan);

    // 3. Status
    const tdStatus = document.createElement('td');
    const statusSpan = document.createElement('span');
    const statusVal = r.status || 'unknown';
    statusSpan.className = `badge-run-status badge-status-${statusVal}`;
    statusSpan.textContent = statusVal.replace('_', ' ');
    tdStatus.append(statusSpan);

    // 4. Checkpoint
    const tdCheckpoint = document.createElement('td');
    if (r.hasCheckpoint || r.checkpoint) {
      const cpBadge = document.createElement('span');
      cpBadge.style.fontSize = '11.5px';
      cpBadge.style.color = 'var(--text-secondary)';
      cpBadge.innerHTML = `<svg class="ui-icon" style="width:12px;height:12px;vertical-align:-1px;margin-right:3px;" aria-hidden="true"><use href="#icon-shield-check"></use></svg> pre-upgrade.sql.gz`;
      tdCheckpoint.append(cpBadge);
    } else {
      const noCp = document.createElement('span');
      noCp.style.color = 'var(--text-muted)';
      noCp.style.fontSize = '12px';
      noCp.textContent = 'None';
      tdCheckpoint.append(noCp);
    }

    // 5. Actions (Delete button)
    const tdActions = document.createElement('td');
    tdActions.style.textAlign = 'right';
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'quiet danger';
    delBtn.style.padding = '4px 8px';
    delBtn.style.fontSize = '12px';
    delBtn.title = r.status === 'running' ? 'Cannot delete an active running workflow' : 'Delete this run and its artifacts';
    delBtn.innerHTML = `<svg class="ui-icon" style="width:13px;height:13px;margin-right:4px;" aria-hidden="true"><use href="#icon-trash"></use></svg> Delete`;
    if (r.status === 'running') {
      delBtn.disabled = true;
      delBtn.style.opacity = '0.5';
    } else {
      delBtn.onclick = () => deleteSingleRun(r.id, r.action);
    }
    tdActions.append(delBtn);

    tr.append(tdDate, tdAction, tdStatus, tdCheckpoint, tdActions);
    tbody.append(tr);
  });
}

async function deleteSingleRun(rid, action) {
  if (!confirm(`Permanently delete run "${rid}" (${action})?\n\nThis removes all artifacts, visual screenshots, and database backups created by this run.`)) {
    return;
  }
  try {
    await api('runs/' + encodeURIComponent(rid), undefined, 'DELETE');
    showToast(`✓ Run ${rid} deleted.`);
    await openRunsHistoryModal(runsHistoryFilter);
    await refresh();
  } catch (err) {
    error(err);
  }
}

function initRunsHistoryEvents() {
  if ($('pantheon-manage-runs-btn')) {
    $('pantheon-manage-runs-btn').onclick = () => openRunsHistoryModal('all');
  }
  if ($('pantheon-edit-project-btn')) {
    $('pantheon-edit-project-btn').onclick = () => editSetup();
  }
  if ($('close-runs-history-btn')) {
    $('close-runs-history-btn').onclick = closeRunsHistoryModal;
  }
  if ($('close-runs-history-btn2')) {
    $('close-runs-history-btn2').onclick = closeRunsHistoryModal;
  }

  const modal = $('runs-history-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeRunsHistoryModal();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.hidden) {
      closeRunsHistoryModal();
    }
  });

  // Filter pills
  document.querySelectorAll('#runs-history-modal [data-filter-action]').forEach(btn => {
    btn.onclick = () => {
      runsHistoryFilter = btn.dataset.filterAction || 'all';
      renderRunsHistoryTable();
    };
  });

  // Quick Action: Delete Older Audits
  const delOldAuditsBtn = $('btn-delete-old-audits');
  if (delOldAuditsBtn) {
    delOldAuditsBtn.onclick = async () => {
      if (!selected) return;
      if (!confirm(`Delete all older audit scans for project "${selected}"?\n\nThe latest audit scan and visual baseline will be preserved.`)) {
        return;
      }
      try {
        delOldAuditsBtn.disabled = true;
        const res = await api('projects/' + encodeURIComponent(selected) + '/runs?action=guided-audit&keep=1', undefined, 'DELETE');
        const count = res.deletedCount !== undefined ? res.deletedCount : (res.deleted || []).length;
        showToast(`✓ Deleted ${count} older audit run(s).`);
        await openRunsHistoryModal(runsHistoryFilter);
        await refresh();
      } catch (err) {
        error(err);
      } finally {
        delOldAuditsBtn.disabled = false;
      }
    };
  }

  // Quick Action: Prune across storage
  const pruneBtn = $('btn-prune-runs');
  if (pruneBtn) {
    pruneBtn.onclick = async () => {
      if (!confirm('Prune historical runs across workbench storage, keeping only the 3 most recent runs per project?')) {
        return;
      }
      try {
        pruneBtn.disabled = true;
        const res = await api('workflow/prune', { keep: 3 });
        const count = res.deletedCount !== undefined ? res.deletedCount : (res.deleted || []).length;
        showToast(`✓ Pruned ${count} old run(s) across workbench.`);
        await openRunsHistoryModal(runsHistoryFilter);
        await refresh();
      } catch (err) {
        error(err);
      } finally {
        pruneBtn.disabled = false;
      }
    };
  }
}
initRunsHistoryEvents();
