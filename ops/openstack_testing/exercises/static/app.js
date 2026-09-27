const RISK_LABEL = { none: "no risk", low: "low risk", medium: "medium risk" };

let EXERCISES = [];
let CURRENT = 0;
let RIGHT_TAB = "commands";
const LOADED = {}; // exercise id -> { hint?: str, solution?: str }

async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

function renderProgressBar() {
  const bar = document.getElementById("progress-bar");
  bar.innerHTML = "";
  EXERCISES.forEach((ex, i) => {
    const seg = document.createElement("div");
    seg.className = "progress-seg" + (ex.completed ? " done" : "") + (i === CURRENT ? " current" : "");
    seg.title = ex.title;
    seg.addEventListener("click", () => goTo(i));
    bar.appendChild(seg);
  });
  document.getElementById("progress-label").textContent = `${CURRENT + 1} / ${EXERCISES.length}`;
}

function leftContentHtml(ex) {
  return `
    <div class="card-head">
      <h2 class="card-title">${ex.title}</h2>
      <div class="badges">
        <span class="badge tier">tier ${ex.tier}</span>
        <span class="badge risk-${ex.risk}">${RISK_LABEL[ex.risk]}</span>
        ${ex.completed ? '<span class="badge done">done</span>' : ""}
      </div>
    </div>
    <p class="goal">${ex.goal}</p>
    <div class="task">${ex.task}</div>
    <div class="row">
      <button class="primary" data-action="init">Init / Reset</button>
      <button data-action="check">Check</button>
    </div>
    ${ex.has_question ? `
    <div class="question-block">
      <div class="question-prompt">${ex.question_prompt}</div>
      <div class="question-row">
        <textarea data-role="answer-input" placeholder="your answer" rows="4"></textarea>
        <button class="small" data-action="answer">Submit answer</button>
      </div>
    </div>` : ""}
    <div class="result" data-role="result" style="display:none"></div>
  `;
}

function commandsHtml(commands) {
  if (!commands.length) return '<p class="no-options">No specific commands for this one -- see the task description.</p>';
  return commands.map(c => `
    <div class="cmd-card">
      <div class="cmd-name">${c.name}</div>
      <div class="cmd-desc">${c.description}</div>
      ${c.options.length ? `
      <table class="cmd-opts">
        ${c.options.map(([flag, meaning]) => `<tr><td>${flag}</td><td>${meaning}</td></tr>`).join("")}
      </table>` : '<div class="no-options">No arguments needed.</div>'}
    </div>
  `).join("");
}

function showResult(kind, text) {
  const el = document.querySelector('[data-role="result"]');
  if (!el) return;
  el.style.display = "block";
  el.className = `result ${kind}`;
  el.textContent = text;
}

function wireLeftActions(ex) {
  const initBtn = document.querySelector('[data-action="init"]');
  const checkBtn = document.querySelector('[data-action="check"]');
  const answerBtn = document.querySelector('[data-action="answer"]');
  if (initBtn) initBtn.addEventListener("click", () => handleInit(ex));
  if (checkBtn) checkBtn.addEventListener("click", () => handleCheck(ex));
  if (answerBtn) answerBtn.addEventListener("click", () => handleAnswer(ex));
}

async function handleInit(ex) {
  if (!ex.warned) {
    const proceed = window.confirm(`This will affect real resources on your OpenStack cluster:\n\n${ex.warning}\n\nContinue?`);
    if (!proceed) return;
    await api(`/api/exercises/${ex.id}/warned`, { method: "POST" });
    ex.warned = true;
  }
  showResult("info", "running init...");
  const res = await api(`/api/exercises/${ex.id}/init`, { method: "POST" });
  showResult(res.ok ? "pass" : "fail", res.message);
}

async function handleCheck(ex) {
  showResult("info", "checking...");
  const res = await api(`/api/exercises/${ex.id}/check`, { method: "POST" });
  showResult(res.passed ? "pass" : "fail", res.message);
  if (res.passed && !ex.completed) {
    ex.completed = true;
    renderProgressBar();
    renderLeftContent();
  }
}

async function handleAnswer(ex) {
  const input = document.querySelector('[data-role="answer-input"]');
  showResult("info", "checking answer...");
  const res = await api(`/api/exercises/${ex.id}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer: input.value }),
  });
  showResult(res.correct ? "pass" : "fail", res.message);
}

function renderLeftContent() {
  const ex = EXERCISES[CURRENT];
  const content = document.getElementById("left-content");
  content.innerHTML = leftContentHtml(ex);
  wireLeftActions(ex);
}

async function ensureLoaded(ex) {
  if (!LOADED[ex.id]) LOADED[ex.id] = {};
  const cache = LOADED[ex.id];
  if (RIGHT_TAB === "hint" && cache.hint === undefined) {
    cache.hint = (await api(`/api/exercises/${ex.id}/hint`)).hint;
  }
  if (RIGHT_TAB === "solution" && cache.solution === undefined) {
    cache.solution = (await api(`/api/exercises/${ex.id}/solution`)).solution;
  }
}

async function renderRightPanel() {
  const ex = EXERCISES[CURRENT];
  document.querySelectorAll(".right-tab-btn").forEach(b => b.classList.toggle("active", b.dataset.rtab === RIGHT_TAB));
  document.querySelectorAll(".right-tab-panel").forEach(p => p.classList.toggle("active", p.id === `rtab-${RIGHT_TAB}`));

  document.getElementById("rtab-commands").innerHTML = commandsHtml(ex.commands);

  if (RIGHT_TAB !== "commands") {
    await ensureLoaded(ex);
    const cache = LOADED[ex.id];
    if (RIGHT_TAB === "hint") document.getElementById("rtab-hint").innerHTML = cache.hint;
    if (RIGHT_TAB === "solution") document.getElementById("rtab-solution").innerHTML = cache.solution;
  }
}

function renderLesson() {
  RIGHT_TAB = "commands";
  renderProgressBar();
  renderLeftContent();
  document.getElementById("rtab-hint").innerHTML = "";
  document.getElementById("rtab-solution").innerHTML = "";
  renderRightPanel();
  document.getElementById("prev-btn").disabled = CURRENT === 0;
  document.getElementById("next-btn").disabled = CURRENT === EXERCISES.length - 1;
}

async function goTo(index) {
  CURRENT = Math.max(0, Math.min(EXERCISES.length - 1, index));
  await api("/api/position", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ index: CURRENT }),
  });
  renderLesson();
}

document.querySelectorAll(".right-tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    RIGHT_TAB = btn.dataset.rtab;
    renderRightPanel();
  });
});

document.getElementById("prev-btn").addEventListener("click", () => goTo(CURRENT - 1));
document.getElementById("next-btn").addEventListener("click", () => goTo(CURRENT + 1));

async function loadExercises() {
  EXERCISES = await api("/api/exercises");
  const pos = await api("/api/position");
  CURRENT = Math.max(0, Math.min(EXERCISES.length - 1, pos.index || 0));
  renderLesson();
}

function initTerminal() {
  const term = new Terminal({
    theme: { background: "#171a21", foreground: "#e6e8ec", cursor: "#4c8dff" },
    fontSize: 13, fontFamily: "ui-monospace, Menlo, Consolas, monospace", cursorBlink: true,
  });
  term.open(document.getElementById("terminal"));
  term.writeln("connecting to the OpenStack VM...");

  const statusEl = document.getElementById("term-status");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/terminal`);

  ws.onopen = () => { statusEl.textContent = "connected"; statusEl.className = "term-status up"; };
  ws.onclose = () => { statusEl.textContent = "disconnected"; statusEl.className = "term-status down"; term.writeln("\r\n[connection closed]"); };
  ws.onerror = () => { statusEl.textContent = "error"; statusEl.className = "term-status down"; };
  ws.onmessage = (ev) => term.write(ev.data);
  term.onData((data) => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
}

loadExercises();
initTerminal();
