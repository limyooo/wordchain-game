/* =========================================================
 * 配置
 * API_BASE 留空表示和页面同源（后端在 localhost:8000 同时托管这个页面）
 * ========================================================= */
const API_BASE = "";
const GRACE_MS = 2500; // 倒计时到 0 后等一会儿再通知后端（后端有 2 秒余量）
const RING_CIRCUMFERENCE = 2 * Math.PI * 19; // 圆环周长，和 SVG 里的 r="19" 对应

/* =========================================================
 * 玩家 ID：第一次打开时随机生成，存在浏览器里，关掉浏览器也还在
 * ========================================================= */
const PLAYER_KEY = "wordchain_player_id";

function getPlayerId() {
  try {
    let id = localStorage.getItem(PLAYER_KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(PLAYER_KEY, id);
    }
    return id;
  } catch {
    // 无痕模式等情况下 localStorage 用不了：这次访问内有效，关掉就没了
    getPlayerId.fallback ??= crypto.randomUUID();
    return getPlayerId.fallback;
  }
}
const PLAYER_ID = getPlayerId();

/* =========================================================
 * API 层
 * ========================================================= */
const api = {
  // GET /api/sessions -> [{id, name, created_at}]（只有自己的）
  listSessions: () => request("GET", "/api/sessions"),

  // POST /api/sessions  body: {rounds, seconds}
  // -> {id, name, created_at, messages, turn_limit, win_rounds}
  createSession: (settings) => request("POST", "/api/sessions", settings),

  // DELETE /api/sessions/{id} -> {ok: true}
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),

  // GET /api/sessions/{id}/messages -> [{role, content}]
  getMessages: (id) => request("GET", `/api/sessions/${id}/messages`),

  // POST /api/sessions/{id}/chat  body: {message}
  // -> {reply, status, rounds, turn_limit}
  chat: (id, message) => request("POST", `/api/sessions/${id}/chat`, { message }),
};

async function request(method, path, body) {
  const headers = { "X-Player-Id": PLAYER_ID }; // 每个请求都带上玩家 ID
  if (body) headers["Content-Type"] = "application/json";

  const res = await fetch(API_BASE + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // 优先显示后端返回的提示，比如 "Game not found. It may have expired."
    let msg = `Request failed (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data.detail) msg = data.detail;
    } catch { /* 返回的不是 JSON，就用默认提示 */ }
    throw new Error(msg);
  }
  return res.json();
}

/* =========================================================
 * 页面元素和状态
 * ========================================================= */
const $ = (id) => document.getElementById(id);
const els = {
  list: $("sessionList"), messages: $("messages"), name: $("sessionName"),
  input: $("input"), send: $("btnSend"), btnNew: $("btnNew"), toast: $("toast"),
  rounds: $("roundsInput"), seconds: $("secondsInput"),
  timer: $("timer"), progress: $("progress"),
  ring: $("timerRing"), ringFg: $("ringFg"), ringNum: $("ringNum"),
};
const state = {
  sessions: [], currentId: null, sending: false,
  // 当前这一局的游戏状态
  status: "idle",      // "idle" / "playing" / "won" / "lost"
  rounds: null,        // 已接上的词数；null 表示还不知道（打开的是旧游戏）
  winRounds: 10,
  turnLimit: 10,
  deadline: 0,         // 这一回合的截止时间（毫秒时间戳）
  timerId: null,
};

function toast(text) {
  els.toast.textContent = text;
  els.toast.style.display = "block";
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (els.toast.style.display = "none"), 3500);
}

/* =========================================================
 * 倒计时
 * ========================================================= */
function startTimer(seconds) {
  state.deadline = Date.now() + seconds * 1000;
  resumeTimer();
}

function resumeTimer() {
  pauseTimer();
  tick();
  state.timerId = setInterval(tick, 100);
}

function pauseTimer() {
  clearInterval(state.timerId);
  state.timerId = null;
}

function tick() {
  const leftMs = Math.max(0, state.deadline - Date.now());
  const remain = Math.ceil(leftMs / 1000);
  const ratio = leftMs / (state.turnLimit * 1000);

  els.ring.hidden = false;
  els.timer.textContent = "";
  els.ringNum.textContent = remain;
  els.ringFg.style.strokeDashoffset = RING_CIRCUMFERENCE * (1 - ratio);
  els.ring.classList.toggle("warn", remain <= 3);

  if (leftMs === 0) {
    pauseTimer();
    onTimeout();
  }
}

// 显示游戏结束的文字，收起圆环
function showEndText(text, cls) {
  pauseTimer();
  els.ring.hidden = true;
  els.timer.textContent = text;
  els.timer.className = cls;
}

// 时间到：先锁住输入，再通知后端记录失败
async function onTimeout() {
  if (state.status !== "playing" || state.sending) return;
  state.sending = true;
  setInputEnabled(false);
  showEndText("⏰ Time's up!", "warn");
  const typing = addTyping();
  setTimeout(async () => {
    try {
      const res = await api.chat(state.currentId, "");
      typing.remove();
      handleReply(res);
    } catch (e) {
      typing.remove();
      toast(e.message);
    } finally {
      state.sending = false;
    }
  }, GRACE_MS);
}

function updateProgress() {
  els.progress.textContent =
    state.rounds === null ? "" : `Round ${state.rounds}/${state.winRounds}`;
}

// 根据后端返回的状态更新页面
function handleReply(res) {
  addMessage("assistant", res.reply);
  const prevRounds = state.rounds;
  state.status = res.status;
  state.rounds = res.rounds;
  state.turnLimit = res.turn_limit;
  updateProgress();

  if (res.status === "won") {
    showEndText("🎉 You win!", "done");
    setInputEnabled(false);
  } else if (res.status === "lost") {
    showEndText("💀 Game over", "warn");
    setInputEnabled(false);
  } else if (prevRounds === null || res.rounds > prevRounds) {
    startTimer(res.turn_limit); // 接词成功：新回合，重新计时
    setInputEnabled(true);
  } else {
    resumeTimer();              // 答错了：计时继续，不重置
    setInputEnabled(true);
  }
}

/* =========================================================
 * 渲染
 * ========================================================= */
function renderSessions() {
  els.list.innerHTML = "";
  if (!state.sessions.length) {
    els.list.innerHTML = '<li class="empty-tip">No games yet</li>';
    return;
  }
  for (const s of state.sessions) {
    const li = document.createElement("li");
    li.className = "session-item" + (s.id === state.currentId ? " active" : "");
    const btn = document.createElement("button");
    btn.className = "session-btn";
    btn.textContent = "🔗 " + s.name;
    btn.onclick = () => openSession(s.id);
    const del = document.createElement("button");
    del.className = "btn-del";
    del.textContent = "✕";
    del.setAttribute("aria-label", "Delete " + s.name);
    del.onclick = () => removeSession(s.id);
    li.append(btn, del);
    els.list.appendChild(li);
  }
}

function addMessage(role, content) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + (role === "user" ? "user" : "ai");
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "🙂" : "🤖";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content; // 用 textContent 防止 XSS
  wrap.append(avatar, bubble);
  els.messages.appendChild(wrap);
  els.messages.scrollTop = els.messages.scrollHeight;
  return wrap;
}

function addTyping() {
  const el = addMessage("assistant", "");
  el.querySelector(".bubble").innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  return el;
}

function renderMessages(list) {
  els.messages.innerHTML = "";
  list.forEach((m) => addMessage(m.role, m.content));
}

function setInputEnabled(on) {
  els.input.disabled = !on;
  els.send.disabled = !on;
  if (on) els.input.focus();
}

function resetGameStatus() {
  pauseTimer();
  els.ring.hidden = true;
  state.status = "idle";
  state.rounds = null;
  els.timer.textContent = "";
  els.timer.className = "";
  updateProgress();
}

// 读取设置框，限制在后端允许的范围内
function readSettings() {
  const clamp = (v, min, max, def) => {
    const n = parseInt(v, 10);
    return Number.isNaN(n) ? def : Math.min(max, Math.max(min, n));
  };
  const rounds = clamp(els.rounds.value, 3, 50, 10);
  const seconds = clamp(els.seconds.value, 5, 60, 10);
  els.rounds.value = rounds;
  els.seconds.value = seconds;
  return { rounds, seconds };
}

/* =========================================================
 * 操作
 * ========================================================= */
async function loadSessions() {
  try {
    state.sessions = await api.listSessions();
    renderSessions();
  } catch (e) { toast(e.message); }
}

async function newSession() {
  els.btnNew.disabled = true;
  try {
    const s = await api.createSession(readSettings());
    resetGameStatus();
    state.sessions.unshift({ id: s.id, name: s.name, created_at: s.created_at });
    state.currentId = s.id;
    els.name.textContent = "Game: " + s.name;
    renderSessions();
    renderMessages(s.messages || []);

    state.status = "playing";
    state.rounds = 0;
    state.winRounds = s.win_rounds;
    state.turnLimit = s.turn_limit;
    updateProgress();
    startTimer(s.turn_limit);
    setInputEnabled(true);
  } catch (e) { toast(e.message); }
  finally { els.btnNew.disabled = false; }
}

async function openSession(id) {
  resetGameStatus();
  state.currentId = id;
  const s = state.sessions.find((x) => x.id === id);
  els.name.textContent = "Game: " + (s ? s.name : id);
  renderSessions();
  try {
    renderMessages(await api.getMessages(id));
    // 旧游戏不知道还剩多少时间，等发送下一个词时由后端判定
    state.status = "playing";
    setInputEnabled(true);
  } catch (e) {
    toast(e.message);
    // 存档已经不在了（比如 Lambda 上 /tmp 被清空）：从列表里移除
    if (e.message.startsWith("Game not found")) {
      state.sessions = state.sessions.filter((x) => x.id !== id);
      state.currentId = null;
      renderSessions();
    }
  }
}

async function removeSession(id) {
  if (!confirm("Delete this game?")) return;
  try {
    await api.deleteSession(id);
    state.sessions = state.sessions.filter((s) => s.id !== id);
    if (state.currentId === id) {
      state.currentId = null;
      resetGameStatus();
      els.name.textContent = "No game selected";
      els.messages.innerHTML = '<div class="welcome"><div class="big">🔤</div>Click “New game” on the left to start a chain</div>';
      setInputEnabled(false);
    }
    renderSessions();
  } catch (e) { toast(e.message); }
}

async function send() {
  const text = els.input.value.trim();
  if (!text || !state.currentId || state.sending || state.status !== "playing") return;
  state.sending = true;
  els.input.value = "";
  addMessage("user", text);
  setInputEnabled(false);
  pauseTimer(); // 等待回复时暂停显示；截止时间不变
  const typing = addTyping();
  try {
    const res = await api.chat(state.currentId, text);
    typing.remove();
    handleReply(res);
  } catch (e) {
    typing.remove();
    toast(e.message);
    resumeTimer();
    setInputEnabled(true);
  } finally {
    state.sending = false;
  }
}

els.btnNew.onclick = newSession;
els.send.onclick = send;
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing) send(); // isComposing：输入法选字时按回车不发送
});

loadSessions();