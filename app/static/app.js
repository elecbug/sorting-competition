"use strict";
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)], t=I18n.t;
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const badge=status=>`<span class="badge ${esc(status)}">${esc(t(status))}</span>`;
const number=(value,digits=2)=>value==null?"—":Number(value).toLocaleString(I18n.intl,{minimumFractionDigits:digits,maximumFractionDigits:digits});
const time=value=>new Date(value*1000).toLocaleString(I18n.intl,{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
const content=(problem,field)=>problem?.translations?.[I18n.locale]?.[field]||problem?.[field]||"";
const state={user:null,problems:[],problem:null,selected:null,tab:"problem",authMode:"login",entries:[],drafts:new Map(),newProblem:true,admin:null,globalOpen:true,problemTexts:{},editLanguage:"en",detailId:null};
const initialCode='#include "csv_sort.h"\n\nvoid student_sort(rows *table) {\n    // Sort table->rows according to the problem.\n\n}\n';
let toastTimer,polling=false;
async function api(path,options={}) {
  const headers={"X-Requested-With":"SortingLab",...options.headers};
  if(options.body!==undefined)headers["Content-Type"]="application/json";
  let response;try{response=await fetch(path,{...options,headers,credentials:"same-origin"});}catch{throw new Error(t("connectionLost"));}
  let body;try{body=await response.json();}catch{throw new Error(t("invalidResponse"));}
  if(!response.ok)throw new Error(Array.isArray(body.detail)?t("invalidForm"):I18n.server(body.detail||t("invalidResponse")));
  return body;
}
function message(selector,value){$(selector).textContent=value;$(selector).hidden=!value;}
function toast(value){clearTimeout(toastTimer);message("#toast",value);toastTimer=setTimeout(()=>$("#toast").hidden=true,4000);}
async function busy(button,fn){button.disabled=true;try{await fn();}finally{button.disabled=false;}}
function showTab(tab){
  if(tab==="admin"&&state.user?.role!=="admin")return;
  state.tab=tab;$$('[data-tab]').forEach(b=>b.classList.toggle("active",b.dataset.tab===tab));
  $$('.panel').forEach(p=>p.hidden=p.id!==`panel-${tab}`);
  const load={leaderboard:loadBoard,history:loadHistory,admin:loadAdmin}[tab];if(load)load().catch(e=>toast(e.message));
}
function updateUser(){
  const admin=state.user?.role==="admin";I18n.use(I18n.preferred,admin);
  $("#auth-button").textContent=state.user?`${state.user.nickname} · ${t("signOut")}`:t("signIn");
  $("#admin-tab").hidden=!admin;$("#export-button").hidden=!admin;
  $("#history-title").textContent=admin?"All submissions":t("mySubmissions");
  if(state.tab==="admin"&&!admin)showTab("problem");
}
async function loadOverview(){
  const o=await api("/api/problems");state.problems=o.problems;state.globalOpen=o.submissions_open;
  const ready=["ready","running"].includes(o.worker.status);
  $("#worker-indicator").classList.toggle("blocked",!ready);
  $("#worker-indicator span").textContent=t(o.worker.status==="running"?"running":ready?"judgeReady":"judgeWaiting");
  message("#worker-notice",!ready?t("workerNotice"):!o.submissions_open?t("allClosed"):"");
  $("#problem-count").textContent=state.problems.length;
  $("#problem-list").innerHTML=state.problems.length?state.problems.map((p,i)=>`<button class="problem-nav ${p.id===state.selected?"active":""}" data-problem="${p.id}"><span class="mono">${i+1}</span><span>${esc(content(p,"title"))}<span class="nav-state">${t(p.is_open?"open":"closed")} · ${t("testsCount",{count:p.test_count})}</span></span></button>`).join(""):`<p class="footnote">${t("noProblems")}</p>`;
  $("#limit-time").textContent=`${o.limits.time_seconds} s`;$("#limit-memory").textContent=`${o.limits.memory_mb} MiB`;$("#limit-repeat").textContent=o.limits.repetitions;
  if(!state.selected&&state.problems.length){const query=Number(new URL(location.href).searchParams.get("problem"));await selectProblem(state.problems.some(p=>p.id===query)?query:state.problems[0].id);}
}
async function selectProblem(id){
  if(state.selected)state.drafts.set(state.selected,$("#source-code").value);
  state.selected=Number(id);state.problem=null;
  const url=new URL(location.href);url.searchParams.set("problem",id);history.replaceState(null,"",url);
  $("#source-code").value=state.drafts.get(state.selected)??initialCode;updateLines();
  $$("[data-problem]").forEach(b=>b.classList.toggle("active",Number(b.dataset.problem)===state.selected));
  await loadProblem(true);if(state.tab==="leaderboard")await loadBoard();if(state.tab==="history")await loadHistory();
}
async function loadProblem(fillForm=false){
  if(!state.selected)return;const id=state.selected,p=await api(`/api/problems/${id}`);if(state.selected!==id)return;state.problem=p;
  $("#no-problem").hidden=true;$("#problem-content").hidden=false;
  $("#problem-title").textContent=content(p,"title");$("#problem-description").textContent=content(p,"description");
  $("#problem-number").textContent=t("problemNumber",{number:state.problems.findIndex(x=>x.id===id)+1});
  $("#problem-open").textContent=t(p.is_open&&state.globalOpen?"open":"closed");$("#problem-open").classList.toggle("closed",!p.is_open||!state.globalOpen);
  const publicCases=p.testcases.filter(c=>c.is_public),privateCount=p.testcases.length-publicCases.length;
  $("#dataset-list").innerHTML=publicCases.map(c=>`<div class="dataset-row"><span class="file-icon">CSV</span><div class="dataset-info"><strong>${esc(c.title)}</strong><small>${t("dataSize",{rows:number(c.row_count,0),columns:c.column_count})}</small></div><a href="/api/problems/${id}/testcases/${c.id}/input.csv" aria-label="${esc(t("downloadInput"))}">↓</a></div>`).join("")+(privateCount?`<p class="footnote">${t("privateTests",{count:privateCount})}</p>`:"")+(!p.testcases.length?`<p class="muted">${t("waitingData")}</p>`:"");
  if(state.user?.role==="admin"){renderAdminCases();if(fillForm)fillProblemForm(p);}
}
async function loadBoard(){
  if(!state.selected){state.entries=[];renderBoard();return;}const id=state.selected,r=await api(`/api/problems/${id}/leaderboard`);if(state.selected!==id)return;state.entries=r.entries;renderBoard();
}
function renderBoard(){
  const entries=state.entries,accepted=entries.filter(e=>e.status==="accepted");
  $("#metric-participants").textContent=entries.length;$("#metric-accepted").textContent=accepted.length;
  $("#metric-fastest").innerHTML=accepted.length?`${number(Math.min(...accepted.map(e=>e.time_ms)))}<small>ms</small>`:"—";
  $("#metric-memory").innerHTML=accepted.length?`${number(Math.min(...accepted.map(e=>e.memory_kb))/1024)}<small>MiB</small>`:"—";
  $("#leaderboard-title").textContent=content(state.problem,"title")||t("leaderboard");$("#export-button").href=`/api/problems/${state.selected}/leaderboard.csv`;
  $("#leaderboard-empty").hidden=entries.length>0;
  $("#leaderboard-body").innerHTML=entries.map(e=>`<tr><td><span class="rank-chip ${e.rank===1?"first":""}">${e.rank??"—"}</span></td><td>${esc(e.nickname)}${e.status!=="accepted"?`<br>${badge(e.status)}`:""}</td><td class="mono">${e.passed} / ${e.total}</td><td class="mono">${number(e.time_ms)}</td><td class="mono">${number(e.memory_kb==null?null:e.memory_kb/1024)}</td><td><strong>${number(e.score)}</strong></td></tr>`).join("");
}
async function loadHistory(){
  if(!state.user){$("#history-body").innerHTML="";$("#history-empty").hidden=false;return;}
  const id=state.selected,r=await api(`/api/submissions${id?`?problem_id=${id}`:""}`);if(state.selected!==id)return;
  $("#history-empty").hidden=r.submissions.length>0;
  $("#history-body").innerHTML=r.submissions.map(s=>`<tr><td>#${s.id}</td><td>${esc(s.nickname)}</td><td>${badge(s.status)}${s.profile&&state.problem?.profile&&s.profile!==state.problem.profile?`<br><small class="muted">${t("olderProfile")}</small>`:""}</td><td class="mono">${number(s.result?.time_ms)} ms / ${number(s.result?.memory_kb==null?null:s.result.memory_kb/1024)} MiB</td><td>${time(s.created_at)}</td><td><button class="text-button" data-detail="${s.id}">${t("details")}</button></td></tr>`).join("");
}
async function showDetail(id){
  const s=await api(`/api/submissions/${id}`),r=s.result;state.detailId=id;$("#detail-title").textContent=`${t("submission")} #${id}`;
  const current=state.entries.find(e=>e.submission_id===Number(id));
  $("#detail-content").innerHTML=`<div class="detail-summary">${badge(s.status)}<span>${r?t("passedCount",{passed:r.passed??0,total:r.total??"—"}):t("queued")}</span><span>${time(s.created_at)}</span></div>${s.profile&&s.profile!==state.problem?.profile?`<p class="footnote">${t("olderResult")}</p>`:""}${r?.message?`<pre class="detail-message">${esc(I18n.server(r.message))}</pre>`:""}${r?.time_ms!=null?`<div class="detail-summary"><span>CPU <b>${number(r.time_ms)} ms</b></span><span>Peak RSS <b>${number(r.memory_kb/1024)} MiB</b></span></div>`:""}${current?`<p class="detail-ranks">${t("rankSummary",{rank:current.rank??"—",time:current.time_rank??"—",memory:current.memory_rank??"—"})}</p>`:""}${(r?.cases||[]).map(c=>`<div class="detail-case"><div><strong>${esc(c.name)}</strong>${badge(c.status)}</div><small>${t("dataSize",{rows:number(c.rows,0),columns:c.columns})}${c.time_ms!=null?` / ${number(c.time_ms)} ms / MAD ${number(c.mad_ms)} ms / ${number(c.memory_kb/1024)} MiB`:""}</small>${c.message?`<p>${esc(I18n.server(c.message))}</p>`:""}${c.samples_ms?`<details><summary>${t("measurements")}</summary><p class="mono">${c.samples_ms.map(x=>number(x)).join(" · ")} ms</p></details>`:""}</div>`).join("")}<details><summary>${t("sourceView")}</summary><pre><code>${esc(s.source)}</code></pre></details>${state.user?.role==="admin"&&!["queued","running"].includes(s.status)?`<button class="button outline small" data-rejudge="${s.id}">Rejudge with current tests</button>`:""}`;
  if(!$("#detail-dialog").open)$("#detail-dialog").showModal();
}
async function loadAdmin(){
  if(state.user?.role!=="admin")return;const a=await api("/api/admin/settings");state.admin=a;
  $("#invite-code").textContent=a.enrollment_code;$("#toggle-submissions").textContent=a.submissions_open?"Close submissions":"Open submissions";
  $("#admin-worker").textContent=a.worker.message;$("#student-count").textContent=a.users.length;
  $("#student-list").innerHTML=a.users.length?a.users.map(u=>`<span>${esc(u.nickname)}<small>${esc(u.student_id)}</small></span>`).join(""):'<p class="footnote">No students registered.</p>';
  if(state.problem&&state.newProblem)fillProblemForm(state.problem);renderAdminCases();
}
function saveProblemText(){const f=$("#problem-form");state.problemTexts[state.editLanguage]={title:f.elements.title.value,description:f.elements.description.value};}
function displayProblemText(locale){
  state.editLanguage=locale;$("#problem-language").value=locale;const f=$("#problem-form"),text=state.problemTexts[locale]||{};
  f.elements.title.value=text.title||"";f.elements.description.value=text.description||"";
  f.elements.title.required=locale==="en";f.elements.description.required=locale==="en";
}
function fillProblemForm(p){
  state.newProblem=false;state.problemTexts={en:{title:p.title,description:p.description},...p.translations};displayProblemText("en");
  $("#problem-form").elements.is_open.checked=!!p.is_open;$("#problem-form-title").textContent="Edit selected problem";
}
function renderAdminCases(){
  $("#admin-case-list").innerHTML=(state.problem?.testcases||[]).map(c=>`<div class="admin-case"><span>${esc(c.title)} · ${c.is_public?"Public input":"Private input"}<br><small class="muted">${number(c.row_count,0)} rows × ${c.column_count} columns</small></span><button class="text-button" data-delete-case="${c.id}">Delete</button></div>`).join("");
}
async function readFile(file,max=32*1024*1024){
  if(!file)throw new Error(t("fileRequired"));if(file.size>max)throw new Error(t("fileSize",{size:max/1024/1024}));
  try{return new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer());}catch{throw new Error(t("utf8Required"));}
}
function updateLines(){$("#line-count").textContent=t("lines",{count:$("#source-code").value.split("\n").length});}
function authMode(mode){
  state.authMode=mode;const register=mode==="register",f=$("#auth-form");
  $$(".register-only").forEach(el=>{el.hidden=!register;el.querySelector("input").required=register;});
  $$("[data-auth-mode]").forEach(b=>b.classList.toggle("active",b.dataset.authMode===mode));
  $("#auth-title").textContent=t(register?"register":"signIn");$("#auth-submit").textContent=t(register?"register":"signIn");
  f.elements.password.autocomplete=register?"new-password":"current-password";f.elements.password.minLength=register?10:1;message("#auth-error","");
}
$("#language-select").addEventListener("change",async e=>{
  I18n.use(e.target.value);updateUser();authMode(state.authMode);updateLines();
  try{await loadOverview();await loadProblem();await loadBoard();if(state.user)await loadHistory();if($("#detail-dialog").open)await showDetail(state.detailId);}catch(err){toast(err.message);}
});
$("#auth-button").addEventListener("click",async()=>{
  try{if(state.user){await api("/api/logout",{method:"POST"});state.user=null;state.admin=null;updateUser();await loadOverview();await loadProblem();await loadHistory();toast(t("loggedOut"));}else{authMode("login");$("#auth-dialog").showModal();}}catch(e){toast(e.message);}
});
$("#guide-button").addEventListener("click",()=>$("#guide-dialog").showModal());
$("#auth-form").addEventListener("invalid",e=>e.target.setCustomValidity(t("invalidForm")),true);
$("#auth-form").addEventListener("input",e=>e.target.setCustomValidity?.(""));
$("#auth-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target;
  await busy($("#auth-submit"),async()=>{try{
    const login={student_id:f.elements.student_id.value.trim(),password:f.elements.password.value};
    if(state.authMode==="register")await api("/api/register",{method:"POST",body:JSON.stringify({...login,nickname:f.elements.nickname.value.trim(),enrollment_code:f.elements.enrollment_code.value.trim()})});
    const r=await api("/api/login",{method:"POST",body:JSON.stringify(login)});state.user=r.user;$("#auth-dialog").close();f.reset();updateUser();await loadOverview();await loadProblem(state.user.role==="admin");await loadHistory();
    if(state.user.role==="admin"){await loadAdmin();if(!state.problems.length)showTab("admin");}toast(t("loggedIn"));
  }catch(err){message("#auth-error",err.message);}});
});
$("#source-code").addEventListener("input",updateLines);
$("#source-code").addEventListener("keydown",e=>{if(e.key==="Tab"){e.preventDefault();e.target.setRangeText("    ",e.target.selectionStart,e.target.selectionEnd,"end");updateLines();}});
$("#file-button").addEventListener("click",()=>$("#source-file").click());
$("#source-file").addEventListener("change",async e=>{try{const file=e.target.files[0];if(!file)return;if(!file.name.toLowerCase().endsWith(".c"))throw new Error(t("cFileRequired"));$("#source-code").value=await readFile(file,65536);updateLines();toast(t("sourceLoaded"));}catch(err){toast(err.message);}finally{e.target.value="";}});
$("#submit-button").addEventListener("click",async e=>{
  if(!state.user){authMode("login");$("#auth-dialog").showModal();return;}
  await busy(e.currentTarget,async()=>{try{const source=$("#source-code").value;if(new TextEncoder().encode(source).length>65536)throw new Error(t("sourceTooLarge"));const r=await api("/api/submissions",{method:"POST",body:JSON.stringify({problem_id:state.selected,source})});message("#submit-message","");showTab("history");toast(t("submitted",{id:r.id}));}catch(err){message("#submit-message",err.message);}});
});
$("#new-problem-button").addEventListener("click",()=>{state.newProblem=true;state.problemTexts={};$("#problem-form").reset();displayProblemText("en");$("#problem-form-title").textContent="New problem";message("#problem-form-message","");$("#problem-form").elements.title.focus();});
$("#problem-language").addEventListener("change",e=>{saveProblemText();displayProblemText(e.target.value);});
$("#problem-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target,isNew=state.newProblem||!state.selected;saveProblemText();
  await busy(f.querySelector('[type="submit"]'),async()=>{try{
    const base=state.problemTexts.en;if(!base?.title?.trim()||!base?.description?.trim())throw new Error("English title and description are required.");
    const translations=Object.fromEntries(Object.entries(state.problemTexts).filter(([key])=>key!=="en"));
    const r=await api(isNew?"/api/admin/problems":`/api/admin/problems/${state.selected}`,{method:isNew?"POST":"PATCH",body:JSON.stringify({...base,is_open:f.elements.is_open.checked,translations})});
    await loadOverview();if(isNew)await selectProblem(r.id);else await loadProblem(true);message("#problem-form-message","");toast("Problem saved.");
  }catch(err){message("#problem-form-message",err.message);}});
});
$("#testcase-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target;
  await busy(f.querySelector('[type="submit"]'),async()=>{try{if(!state.selected)throw new Error("Select a problem first.");const [original,answer]=await Promise.all([readFile(f.elements.input_file.files[0]),readFile(f.elements.answer_file.files[0])]);await api(`/api/admin/problems/${state.selected}/testcases`,{method:"POST",body:JSON.stringify({title:f.elements.title.value,input_csv:original,answer_csv:answer,is_public:f.elements.is_public.checked})});f.reset();message("#testcase-form-message","");await loadOverview();await loadProblem();await loadBoard();toast("Input and answer validated and saved.");}catch(err){message("#testcase-form-message",err.message);}});
});
$("#copy-invite").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(state.admin.enrollment_code);toast("Class code copied.");}catch{toast("Select and copy the class code. Clipboard access requires HTTPS.");}});
$("#toggle-submissions").addEventListener("click",async e=>busy(e.currentTarget,async()=>{try{await api("/api/admin/submissions-open",{method:"POST",body:JSON.stringify({is_open:!state.admin.submissions_open})});await loadAdmin();await loadOverview();await loadProblem();}catch(err){toast(err.message);}}));
document.addEventListener("click",async e=>{
  const b=e.target.closest("button");if(!b)return;
  try{
    if(b.dataset.close)$(`#${b.dataset.close}`).close();if(b.dataset.tab)showTab(b.dataset.tab);if(b.dataset.authMode)authMode(b.dataset.authMode);
    if(b.dataset.problem)await selectProblem(b.dataset.problem);if(b.dataset.detail){await loadBoard();await showDetail(b.dataset.detail);}
    if(b.dataset.rejudge)await busy(b,async()=>{await api(`/api/admin/submissions/${b.dataset.rejudge}/rejudge`,{method:"POST"});$("#detail-dialog").close();await loadHistory();await loadBoard();toast("Rejudge queued.");});
    if(b.dataset.deleteCase&&confirm("Delete this test? This starts a new leaderboard profile."))await busy(b,async()=>{await api(`/api/admin/problems/${state.selected}/testcases/${b.dataset.deleteCase}`,{method:"DELETE"});await loadProblem();await loadBoard();toast("Test deleted.");});
  }catch(err){toast(err.message);}
});
async function init(){
  I18n.use(I18n.preferred);authMode("login");updateLines();
  try{state.user=(await api("/api/me")).user;updateUser();await loadOverview();if(state.user?.role==="admin"&&!state.problems.length)showTab("admin");}catch(e){message("#worker-notice",e.message);}
  setInterval(async()=>{if(document.hidden||polling)return;polling=true;try{await loadOverview();await loadProblem();if(state.tab==="leaderboard")await loadBoard();if(state.tab==="history")await loadHistory();}catch{message("#worker-notice",t("connectionLost"));}finally{polling=false;}},5000);
}
init();
