"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels = {queued:"채점 대기",running:"채점 중",accepted:"정답",wrong_answer:"오답",compile_error:"컴파일 오류",time_limit:"시간 초과",memory_limit:"메모리 초과",runtime_error:"실행 오류",output_limit:"출력 초과",system_error:"채점기 오류"};
const badge = (status) => `<span class="badge ${esc(status)}">${esc(labels[status] || status)}</span>`;
const number = (n, digits=2) => n == null ? "—" : Number(n).toLocaleString("ko-KR", {maximumFractionDigits:digits,minimumFractionDigits:digits});
const time = (n) => new Date(n*1000).toLocaleString("ko-KR",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
const state = {user:null, problems:[], problem:null, selected:null, tab:"problem", authMode:"login", entries:[], drafts:new Map(), newProblem:true, globalOpen:true, admin:null};
let toastTimer, polling=false;

async function api(path, options={}) {
  const headers = {"X-Requested-With":"SortingLab",...options.headers};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path,{...options,headers,credentials:"same-origin"});
  let body;
  try { body = await response.json(); } catch { throw new Error("서버의 응답을 읽지 못했습니다."); }
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(x => x.msg).join(" / ") : body.detail;
    throw new Error(detail || `요청에 실패했습니다. (${response.status})`);
  }
  return body;
}
function message(selector, value) { $(selector).textContent=value; $(selector).hidden=!value; }
function toast(value) { clearTimeout(toastTimer); message("#toast",value); toastTimer=setTimeout(()=>$("#toast").hidden=true,4500); }
function showTab(tab) {
  if (tab==="admin" && state.user?.role!=="admin") return;
  state.tab=tab;
  $$('[data-tab]').forEach(b=>b.classList.toggle("active",b.dataset.tab===tab));
  $$('.panel').forEach(p=>p.hidden=p.id!==`panel-${tab}`);
  if(tab==="leaderboard") loadBoard().catch(e=>toast(e.message));
  if(tab==="history") loadHistory().catch(e=>toast(e.message));
  if(tab==="admin") loadAdmin().catch(e=>toast(e.message));
}
function updateUser() {
  const admin=state.user?.role==="admin";
  $("#auth-button").textContent=state.user ? `${state.user.nickname} · 로그아웃` : "로그인 ↗";
  $("#admin-tab").hidden=!admin;
  $("#export-button").hidden=!admin;
  $("#history-title").textContent=admin ? "전체 제출 기록" : "나의 제출 기록";
  if(state.tab==="admin"&&!admin) showTab("problem");
}
async function loadOverview() {
  const overview=await api("/api/problems");
  state.problems=overview.problems;
  state.globalOpen=overview.submissions_open;
  const worker=overview.worker;
  const ready=["ready","running"].includes(worker.status);
  $("#worker-indicator").classList.toggle("blocked",!ready);
  $("#worker-indicator span").textContent=worker.status==="running" ? "채점 중" : ready ? "채점기 연결됨" : "채점기 준비 중";
  message("#worker-notice",!ready ? "채점기가 준비 중입니다. 제출한 코드는 대기열에 보관되며, 연결되면 순서대로 채점됩니다." : !overview.submissions_open ? "전체 제출 접수가 마감되었습니다." : "");
  $("#problem-count").textContent=String(state.problems.length).padStart(2,"0");
  $("#problem-list").innerHTML=state.problems.length ? state.problems.map((p,i)=>`<button class="problem-nav ${p.id===state.selected?"active":""}" data-problem="${p.id}"><span class="mono">${String(i+1).padStart(2,"0")}</span><span>${esc(p.title)}<span class="nav-state">${p.is_open?"접수 중":"접수 마감"} · 테스트 ${p.test_count}개</span></span></button>`).join("") : '<p class="footnote">아직 등록된 문제가 없습니다.</p>';
  $("#limit-time").textContent=`${overview.limits.time_seconds} s`;
  $("#limit-memory").textContent=`${overview.limits.memory_mb} MiB`;
  $("#limit-repeat").textContent=`${overview.limits.repetitions} 회`;
  if(!state.selected&&state.problems.length) {
    const queryId=Number(new URL(location.href).searchParams.get("problem"));
    await selectProblem(state.problems.some(p=>p.id===queryId)?queryId:state.problems[0].id);
  }
}
async function selectProblem(id) {
  if(state.selected) state.drafts.set(state.selected,$("#source-code").value);
  state.selected=Number(id);
  state.problem=null;
  const url=new URL(location.href); url.searchParams.set("problem",id); history.replaceState(null,"",url);
  if(state.drafts.has(state.selected)) $("#source-code").value=state.drafts.get(state.selected);
  else $("#source-code").value='#include "csv_sort.h"\n\nvoid student_sort(rows *table) {\n    // 문제의 조건에 맞게 table->rows를 정렬하세요.\n\n}\n';
  updateLines();
  $$("[data-problem]").forEach(b=>b.classList.toggle("active",Number(b.dataset.problem)===state.selected));
  await loadProblem(true);
  if(state.tab==="leaderboard") await loadBoard();
  if(state.tab==="history") await loadHistory();
}
async function loadProblem(fillForm=false) {
  if(!state.selected) return;
  const id=state.selected;
  const p=await api(`/api/problems/${id}`);
  if(state.selected!==id) return;
  state.problem=p;
  $("#no-problem").hidden=true; $("#problem-content").hidden=false;
  $("#problem-title").textContent=p.title;
  $("#problem-description").textContent=p.description;
  $("#problem-number").textContent=`PROBLEM ${String(state.problems.findIndex(x=>x.id===id)+1).padStart(2,"0")}`;
  $("#problem-open").textContent=p.is_open&&state.globalOpen ? "접수 중" : "접수 마감";
  $("#problem-open").classList.toggle("closed",!p.is_open||!state.globalOpen);
  $("#profile-label").textContent=p.profile ? `PROFILE ${p.profile.slice(0,8).toUpperCase()}` : "";
  const publicCases=p.testcases.filter(c=>c.is_public);
  const privateCount=p.testcases.length-publicCases.length;
  $("#dataset-list").innerHTML=publicCases.map(c=>`<div class="dataset-row"><span class="file-icon">CSV</span><div class="dataset-info"><strong>${esc(c.title)}</strong><small>${number(c.row_count,0)} ROWS · ${c.column_count} COLUMNS</small></div><a href="/api/problems/${id}/testcases/${c.id}/input.csv" aria-label="${esc(c.title)} 원본 다운로드">↓</a></div>`).join("")+(privateCount?`<p class="footnote">+ 비공개 테스트 ${privateCount}개</p>`:"")+(!p.testcases.length?'<p class="muted">채점 데이터 등록을 기다리고 있습니다.</p>':"");
  if(state.user?.role==="admin") {
    renderAdminCases();
    if(fillForm) fillProblemForm(p);
  }
}
async function loadBoard() {
  if(!state.selected) { state.entries=[]; renderBoard(); return; }
  const id=state.selected, response=await api(`/api/problems/${id}/leaderboard`);
  if(state.selected!==id) return;
  state.entries=response.entries;
  renderBoard();
}
function renderBoard() {
  const entries=state.entries, accepted=entries.filter(e=>e.status==="accepted");
  $("#metric-participants").innerHTML=`${entries.length}<small>명</small>`;
  $("#metric-accepted").innerHTML=`${accepted.length}<small>명</small>`;
  $("#metric-fastest").innerHTML=accepted.length?`${number(Math.min(...accepted.map(e=>e.time_ms)))}<small>ms</small>`:"—";
  $("#metric-memory").innerHTML=accepted.length?`${number(Math.min(...accepted.map(e=>e.memory_kb))/1024)}<small>MiB</small>`:"—";
  $("#leaderboard-title").textContent=state.problem?.title||"순위표";
  $("#export-button").href=`/api/problems/${state.selected}/leaderboard.csv`;
  $("#leaderboard-empty").hidden=entries.length>0;
  $("#leaderboard-body").innerHTML=entries.map(e=>`<tr><td><span class="rank-chip ${e.rank===1?"first":""}">${e.rank??"—"}</span></td><td>${esc(e.nickname)}${e.status!=="accepted"?`<br>${badge(e.status)}`:""}</td><td class="mono">${e.passed} / ${e.total}</td><td class="mono">${number(e.time_ms)}</td><td class="mono">${number(e.memory_kb==null?null:e.memory_kb/1024)}</td><td><strong>${number(e.score)}</strong></td></tr>`).join("");
}
async function loadHistory() {
  const body=$("#history-body");
  if(!state.user){body.innerHTML="";$("#history-empty").hidden=false;return;}
  const id=state.selected;
  const result=await api(`/api/submissions${id?`?problem_id=${id}`:""}`);
  if(state.selected!==id) return;
  $("#history-empty").hidden=result.submissions.length>0;
  body.innerHTML=result.submissions.map(s=>`<tr><td>#${s.id}</td><td>${esc(s.nickname)}</td><td>${badge(s.status)}${s.profile&&state.problem?.profile&&s.profile!==state.problem.profile?'<br><small class="muted">이전 측정 기준</small>':""}</td><td class="mono">${number(s.result?.time_ms)} ms / ${number(s.result?.memory_kb==null?null:s.result.memory_kb/1024)} MiB</td><td>${time(s.created_at)}</td><td><button class="text-button" data-detail="${s.id}">상세 ↗</button></td></tr>`).join("");
}
async function showDetail(id) {
  const s=await api(`/api/submissions/${id}`), r=s.result;
  $("#detail-title").textContent=`제출 #${id}`;
  const current=state.entries.find(e=>e.submission_id===Number(id));
  $("#detail-content").innerHTML=`<div class="detail-summary">${badge(s.status)}<span>${r?`${r.passed??0} / ${r.total??"—"} 통과`:"순차 채점을 기다리고 있습니다."}</span><span>${time(s.created_at)}</span></div>${s.profile&&s.profile!==state.problem?.profile?'<p class="footnote">이전 측정 기준의 결과입니다. 현재 순위표에는 포함되지 않습니다.</p>':""}${r?.message?`<pre class="detail-message">${esc(r.message)}</pre>`:""}${r?.time_ms!=null?`<div class="detail-summary"><span>CPU <b>${number(r.time_ms)} ms</b></span><span>Peak RSS <b>${number(r.memory_kb/1024)} MiB</b></span></div>`:""}${current?`<p class="detail-ranks">종합 ${current.rank??"—"}위 · 시간 ${current.time_rank??"—"}위 · 메모리 ${current.memory_rank??"—"}위</p>`:""}${(r?.cases||[]).map(c=>`<div class="detail-case"><div><strong>${esc(c.name)}</strong>${badge(c.status)}</div><small>${number(c.rows,0)} rows × ${c.columns} cols ${c.time_ms!=null?` / median ${number(c.time_ms)} ms / MAD ${number(c.mad_ms)} ms / ${number(c.memory_kb/1024)} MiB`:""}</small>${c.message?`<p>${esc(c.message)}</p>`:""}${c.samples_ms?`<details><summary>반복 측정값 보기</summary><p class="mono">${c.samples_ms.map(x=>number(x)).join(" · ")} ms</p></details>`:""}</div>`).join("")}<details><summary>제출한 소스 코드 보기</summary><pre><code>${esc(s.source)}</code></pre></details>${state.user?.role==="admin"&&!["queued","running"].includes(s.status)?`<button class="button outline small" data-rejudge="${s.id}">현재 데이터로 재채점 ↻</button>`:""}`;
  if(!$("#detail-dialog").open) $("#detail-dialog").showModal();
}
async function loadAdmin() {
  if(state.user?.role!=="admin") return;
  const a=await api("/api/admin/settings"); state.admin=a;
  $("#invite-code").textContent=a.enrollment_code;
  $("#toggle-submissions").textContent=a.submissions_open?"접수 마감하기":"접수 재개하기";
  $("#admin-worker").textContent=a.worker.message;
  $("#student-count").textContent=`${a.users.length}명`;
  $("#student-list").innerHTML=a.users.length?a.users.map(u=>`<span>${esc(u.nickname)}<small>${esc(u.student_id)}</small></span>`).join(""):'<p class="footnote">등록된 학생이 없습니다.</p>';
  if(state.problem&&state.newProblem) fillProblemForm(state.problem);
  renderAdminCases();
}
function fillProblemForm(p) {
  state.newProblem=false;
  const form=$("#problem-form");
  form.elements.title.value=p.title;form.elements.description.value=p.description;form.elements.is_open.checked=!!p.is_open;
  $("#problem-form-title").textContent="선택한 문제 수정";
}
function renderAdminCases() {
  $("#admin-case-list").innerHTML=(state.problem?.testcases||[]).map(c=>`<div class="admin-case"><span>${esc(c.title)} · ${c.is_public?"원본 공개":"비공개"}<br><small class="muted">${number(c.row_count,0)}행 × ${c.column_count}열</small></span><button class="text-button" data-delete-case="${c.id}">삭제</button></div>`).join("");
}
async function readFile(file,max=32*1024*1024) {
  if(!file) throw new Error("파일을 선택하세요.");
  if(file.size>max) throw new Error(`파일 크기는 ${max/1024/1024} MiB 이하여야 합니다.`);
  try{return new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer());}catch{throw new Error("UTF-8 인코딩 파일을 선택하세요.");}
}
function updateLines() { $("#line-count").textContent=`${$("#source-code").value.split("\n").length} lines`; }
function authMode(mode) {
  state.authMode=mode;
  const register=mode==="register";
  $$(".register-only").forEach(e=>{e.hidden=!register;e.querySelector("input").required=register;});
  $$("[data-auth-mode]").forEach(b=>b.classList.toggle("active",b.dataset.authMode===mode));
  $("#auth-title").textContent=register?"같이 시작해볼까요?":"다시 만나 반가워요.";
  $("#auth-submit").textContent=register?"수업 참여하기 →":"로그인 →";
  $("#auth-form").elements.password.autocomplete=register?"new-password":"current-password";
  message("#auth-error","");
}
async function busy(button,fn) { button.disabled=true;try{await fn();}finally{button.disabled=false;} }

$("#auth-button").addEventListener("click",async()=>{
  try { if(state.user){await api("/api/logout",{method:"POST"});state.user=null;state.admin=null;updateUser();await loadHistory();toast("로그아웃했습니다.");}else {authMode("login");$("#auth-dialog").showModal();} }catch(e){toast(e.message);}
});
$("#guide-button").addEventListener("click",()=>$("#guide-dialog").showModal());
document.addEventListener("click",async event=>{
  const target=event.target.closest("button"); if(!target) return;
  try {
    if(target.dataset.close) $(`#${target.dataset.close}`).close();
    if(target.dataset.tab) showTab(target.dataset.tab);
    if(target.dataset.authMode) authMode(target.dataset.authMode);
    if(target.dataset.problem) await selectProblem(target.dataset.problem);
    if(target.dataset.detail) {await loadBoard();await showDetail(target.dataset.detail);}
    if(target.dataset.rejudge) await busy(target,async()=>{await api(`/api/admin/submissions/${target.dataset.rejudge}/rejudge`,{method:"POST"});$("#detail-dialog").close();await loadHistory();await loadBoard();toast("재채점을 요청했습니다.");});
    if(target.dataset.deleteCase && confirm("테스트를 삭제하면 순위표의 측정 기준이 바뀝니다. 이 테스트를 삭제할까요?")) await busy(target,async()=>{await api(`/api/admin/problems/${state.selected}/testcases/${target.dataset.deleteCase}`,{method:"DELETE"});await loadProblem();await loadBoard();toast("테스트를 삭제했습니다.");});
  }catch(e){toast(e.message);}
});
$("#auth-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target;
  await busy($("#auth-submit"),async()=>{try{
    const login={student_id:f.elements.student_id.value.trim(),password:f.elements.password.value};
    if(state.authMode==="register") await api("/api/register",{method:"POST",body:JSON.stringify({...login,nickname:f.elements.nickname.value.trim(),enrollment_code:f.elements.enrollment_code.value.trim()})});
    const result=await api("/api/login",{method:"POST",body:JSON.stringify(login)});state.user=result.user;
    $("#auth-dialog").close();f.reset();updateUser();await loadProblem(state.user.role==="admin");await loadHistory();
    if(state.user.role==="admin") {await loadAdmin();if(!state.problems.length)showTab("admin");}
    toast("로그인했습니다.");
  }catch(err){message("#auth-error",err.message);}});
});
$("#source-code").addEventListener("input",updateLines);
$("#source-code").addEventListener("keydown",e=>{if(e.key==="Tab"){e.preventDefault();e.target.setRangeText("    ",e.target.selectionStart,e.target.selectionEnd,"end");updateLines();}});
$("#file-button").addEventListener("click",()=>$("#source-file").click());
$("#source-file").addEventListener("change",async e=>{try{const file=e.target.files[0];if(!file)return;if(!file.name.toLowerCase().endsWith(".c"))throw new Error(".c 파일을 선택하세요.");$("#source-code").value=await readFile(file,65536);updateLines();toast("소스 파일을 불러왔습니다.");}catch(err){toast(err.message);}finally{e.target.value="";}});
$("#submit-button").addEventListener("click",async e=>{
  if(!state.user){authMode("login");$("#auth-dialog").showModal();return;}
  await busy(e.currentTarget,async()=>{try{
    const source=$("#source-code").value;
    if(new TextEncoder().encode(source).length>65536)throw new Error("소스 코드는 64 KiB 이하여야 합니다.");
    const s=await api("/api/submissions",{method:"POST",body:JSON.stringify({problem_id:state.selected,source})});
    message("#submit-message","");showTab("history");toast(`제출 #${s.id} · 채점 대기열에 등록했습니다.`);
  }catch(err){message("#submit-message",err.message);}});
});
$("#new-problem-button").addEventListener("click",()=>{state.newProblem=true;$("#problem-form").reset();$("#problem-form-title").textContent="새 문제 등록";message("#problem-form-message","");$("#problem-form").elements.title.focus();});
$("#problem-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target,isNew=state.newProblem||!state.selected;
  await busy(f.querySelector('[type="submit"]'),async()=>{try{
    const payload={title:f.elements.title.value,description:f.elements.description.value,is_open:f.elements.is_open.checked};
    const result=await api(isNew?"/api/admin/problems":`/api/admin/problems/${state.selected}`,{method:isNew?"POST":"PATCH",body:JSON.stringify(payload)});
    await loadOverview();if(isNew) await selectProblem(result.id);else await loadProblem(true);
    message("#problem-form-message","");toast("문제를 저장했습니다.");
  }catch(err){message("#problem-form-message",err.message);}});
});
$("#testcase-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target;
  await busy(f.querySelector('[type="submit"]'),async()=>{try{
    if(!state.selected)throw new Error("먼저 문제를 등록하고 선택하세요.");
    const [original,answer]=await Promise.all([readFile(f.elements.input_file.files[0]),readFile(f.elements.answer_file.files[0])]);
    await api(`/api/admin/problems/${state.selected}/testcases`,{method:"POST",body:JSON.stringify({title:f.elements.title.value,input_csv:original,answer_csv:answer,is_public:f.elements.is_public.checked})});
    f.reset();message("#testcase-form-message","");await loadOverview();await loadProblem();await loadBoard();toast("원본·정답 CSV를 검증하고 등록했습니다.");
  }catch(err){message("#testcase-form-message",err.message);}});
});
$("#copy-invite").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(state.admin.enrollment_code);toast("참여 코드를 복사했습니다.");}catch{toast("참여 코드를 선택해 복사하세요. 클립보드는 HTTPS에서 사용할 수 있습니다.");}});
$("#toggle-submissions").addEventListener("click",async e=>{await busy(e.currentTarget,async()=>{try{await api("/api/admin/submissions-open",{method:"POST",body:JSON.stringify({is_open:!state.admin.submissions_open})});await loadAdmin();await loadOverview();await loadProblem();}catch(err){toast(err.message);}});});

async function init() {
  try {const me=await api("/api/me");state.user=me.user;updateUser();await loadOverview();updateLines();if(state.user?.role==="admin"&&!state.problems.length)showTab("admin");}
  catch(e){message("#worker-notice",`서비스 연결을 확인하세요. ${e.message}`);}
  setInterval(async()=>{
    if(document.hidden||polling) return;
    polling=true;
    try{await loadOverview();await loadProblem();if(state.tab==="leaderboard")await loadBoard();if(state.tab==="history")await loadHistory();}
    catch{message("#worker-notice","서버와 연결이 끊겼습니다. 자동으로 다시 연결합니다.");}
    finally{polling=false;}
  },5000);
}
init();
