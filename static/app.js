/* =========================================================
 * 配置
 * ========================================================= */
const API_BASE = "";
const GRACE_MS = 2500;       // 倒计时到 0 后等一会儿再通知后端（后端有 2 秒余量）
const ROOM_POLL_MS = 2000;   // 在房间里每 2 秒问一次服务器房间的状态
const RING_CIRCUMFERENCE = 2 * Math.PI * 19; // 圆环周长，和 SVG 里的 r="19" 对应

/* =========================================================
 * 浏览器本地存储：玩家 ID 和昵称
 * ========================================================= */
const PLAYER_KEY = "wordchain_player_id";
const NICKNAME_KEY = "wordchain_nickname";

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

function loadNickname() {
  try { return localStorage.getItem(NICKNAME_KEY) || ""; } catch { return ""; }
}
function saveNickname(name) {
  try { localStorage.setItem(NICKNAME_KEY, name); } catch { /* 存不了就算了 */ }
}

/* =========================================================
 * API 层
 * ========================================================= */
const api = {
  // 单人游戏
  listSessions: () => request("GET", "/api/sessions"),
  createSession: (settings) => request("POST", "/api/sessions", settings),
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),
  getMessages: (id) => request("GET", `/api/sessions/${id}/messages`),
  getState: (id) => request("GET", `/api/sessions/${id}/state`),
  chat: (id, message) => request("POST", `/api/sessions/${id}/chat`, { message }),
  // 房间
  createRoom: (settings) => request("POST", "/api/rooms", settings),
  getRoom: (code) => request("GET", `/api/rooms/${code}`),
  joinRoom: (code, nickname) => request("POST", `/api/rooms/${code}/join`, { nickname }),
  startRoom: (code) => request("POST", `/api/rooms/${code}/start`, {}),
  endRoom: (code) => request("POST", `/api/rooms/${code}/end`, {}),
  getLeaderboard: (code) => request("GET", `/api/rooms/${code}/leaderboard`),
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
    // 优先显示后端返回的提示，比如 "Room not found. Check the code and try again."
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
  nickname: $("nicknameInput"), roomCodeInput: $("roomCodeInput"),
  btnJoinRoom: $("btnJoinRoom"), btnCreateRoom: $("btnCreateRoom"),
  roomBar: $("roomBar"), roomLabel: $("roomLabel"), roomStatus: $("roomStatus"),
  btnStart: $("btnStart"), btnEnd: $("btnEnd"), boardLink: $("boardLink"),
  lobby: $("lobby"), lobbyText: $("lobbyText"), lobbyPlayers: $("lobbyPlayers"),
  results: $("results"), resultsBody: $("resultsBody"),
  modal: $("modal"), modalText: $("modalText"), modalOk: $("modalOk"), modalCancel: $("modalCancel"),
  sidebar: $("sidebar"), backdrop: $("backdrop"), btnMenu: $("btnMenu"), btnCloseMenu: $("btnCloseMenu"),
};
const state = {
  sessions: [], currentId: null, sending: false,
  // 当前这一局的游戏状态
  status: "idle",      // "idle" / "waiting" / "playing" / "won" / "lost"
  rounds: null,
  winRounds: 10,
  turnLimit: 10,
  deadline: 0,         // 这一回合的截止时间（毫秒时间戳）
  timerId: null,
  // 当前所在的房间
  roomCode: null,
  isHost: false,
  pollId: null,
};

function toast(text) {
  els.toast.textContent = text;
  els.toast.style.display = "block";
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (els.toast.style.display = "none"), 3500);
}

// 手机上的抽屉菜单：打开 / 关闭（电脑上菜单一直显示，这两个函数没有影响）
function openMenu() {
  els.sidebar.classList.add("open");
  els.backdrop.hidden = false;
}
function closeMenu() {
  els.sidebar.classList.remove("open");
  els.backdrop.hidden = true;
}

// 手机键盘适配：键盘弹出时，把游戏界面的高度设成“键盘上方剩下的高度”，
// 页面固定在顶部不被推上去，聊天区自动滚到最新的消息
function scrollToLatest() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function fitToViewport() {
  const vv = window.visualViewport;
  if (!vv) return;
  const root = document.documentElement.style;
  // 高度：键盘上方剩下的可见高度
  root.setProperty("--app-height", `${vv.height}px`);
  // 位置：iPhone 键盘弹出时会把可见区域往下偏移，界面跟着偏移，正好贴在可见区域里
  root.setProperty("--app-top", `${vv.offsetTop}px`);
  // 可见区域明显变矮，说明键盘弹出来了
  document.body.classList.toggle("kb-open", vv.height < window.innerHeight * 0.8);
  scrollToLatest();
}

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", fitToViewport);
  window.visualViewport.addEventListener("scroll", fitToViewport);
}

// 页面内的确认弹窗：返回 Promise，点确定是 true，点取消是 false
function confirmDialog(message, okText = "OK", danger = false) {
  return new Promise((resolve) => {
    els.modalText.textContent = message;
    els.modalOk.textContent = okText;
    els.modalOk.classList.toggle("danger", danger);
    els.modal.hidden = false;
    els.modalOk.focus();

    const close = (result) => {
      els.modal.hidden = true;
      els.modalOk.onclick = null;
      els.modalCancel.onclick = null;
      els.modal.onclick = null;
      document.removeEventListener("keydown", onKey);
      resolve(result);
    };
    const onKey = (e) => {
      if (e.key === "Escape") close(false);
      if (e.key === "Enter") close(true);
    };
    els.modalOk.onclick = () => close(true);
    els.modalCancel.onclick = () => close(false);
    els.modal.onclick = (e) => { if (e.target === els.modal) close(false); }; // 点弹窗外面也算取消
    document.addEventListener("keydown", onKey);
  });
}

// 后端存的是 UTC 时间，这里转成玩家自己的本地时间
function formatLocal(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function sessionLabel(s) {
  if (s.name.startsWith("Room ")) return "🏠 " + s.name;
  return "🔗 " + formatLocal(s.created_at);
}

/* =========================================================
 * 倒计时
 * ========================================================= */
function startTimer(secondsLeft) {
  state.deadline = Date.now() + secondsLeft * 1000;
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

// 显示一段状态文字（等待、胜利、失败），收起圆环
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

// 根据后端的 state 接口，恢复这一局的画面（倒计时、输赢、等待）
function applyState(st) {
  state.status = st.status;
  state.rounds = st.rounds;
  state.winRounds = st.win_rounds;
  state.turnLimit = st.turn_limit;
  updateProgress();

  if (st.status === "playing") {
    startTimer(st.seconds_left ?? st.turn_limit);
    setInputEnabled(true);
  } else if (st.status === "waiting") {
    showEndText("⏳ Waiting for host", "");
    setInputEnabled(false);
  } else if (st.status === "won") {
    showEndText("🎉 You win!", "done");
    setInputEnabled(false);
  } else if (st.status === "lost") {
    showEndText("💀 Game over", "warn");
    setInputEnabled(false);
  }
}

// 根据 /chat 的回复更新页面
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
  } else if (res.status === "waiting") {
    showEndText("⏳ Waiting for host", "");
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
 * 房间：轮询、房间栏、等待大厅
 * ========================================================= */
function startRoomPolling(code) {
  stopRoomPolling();
  state.roomCode = code;
  els.roomBar.hidden = false;
  els.roomLabel.textContent = `Room ${code}`;
  els.boardLink.href = `/static/board.html?code=${code}`;
  pollRoom();
  state.pollId = setInterval(pollRoom, ROOM_POLL_MS);
}

function stopRoomPolling() {
  clearInterval(state.pollId);
  state.pollId = null;
  state.roomCode = null;
  state.isHost = false;
  els.roomBar.hidden = true;
  els.lobby.hidden = true;
  els.results.hidden = true;
}

async function pollRoom() {
  const code = state.roomCode;
  const sid = state.currentId;
  if (!code) return;
  try {
    const room = await api.getRoom(code);
    if (state.roomCode !== code) return; // 等待期间已经切换到别的游戏了
    state.isHost = room.is_host;
    renderRoom(room);

    // 房主刚点了 Start：把自己这局切换成进行中，开始倒计时
    if (state.status === "waiting" && room.status !== "waiting") {
      const st = await api.getState(sid);
      if (state.currentId !== sid) return;
      renderMessages(await api.getMessages(sid));
      applyState(st);
      renderRoom(room);
    }

    // 房主点了 End：直接在页面上显示最终排行榜
    if (room.status === "finished") {
      await showResults(code);
    }
  } catch (e) {
    console.warn("Room poll failed:", e.message); // 偶尔失败不打扰玩家，下次再试
  }
}

function renderRoom(room) {
  const n = room.players.length;
  const people = `${n} player${n === 1 ? "" : "s"}`;
  els.roomStatus.textContent =
    room.status === "waiting" ? `Waiting · ${people}` :
    room.status === "playing" ? `Live · ${people}` : `Ended · ${people}`;
  els.roomStatus.className = "room-status " + room.status;

  els.btnStart.hidden = !(room.is_host && room.status === "waiting");
  els.btnEnd.hidden = !(room.is_host && room.status === "playing");

  // 等待大厅：只在自己这局还没开始时显示
  els.lobby.hidden = state.status !== "waiting";
  if (!els.lobby.hidden) {
    els.lobbyText.textContent = room.is_host
      ? `You're the host. Share the code ${room.code}, then click Start when everyone has joined.`
      : `Waiting for the host to start… Share the code ${room.code} with your friends!`;
    els.lobbyPlayers.innerHTML = "";
    for (const name of room.players) {
      const chip = document.createElement("span");
      chip.className = "player-chip";
      chip.textContent = name; // 用 textContent 防止 XSS
      els.lobbyPlayers.appendChild(chip);
    }
  }
}

/* =========================================================
 * 最终排行榜（房主点 End 之后显示在页面上）
 * ========================================================= */
const RESULT_STATUS = { playing: "Playing", won: "🎉 Won", lost: "💀 Out", waiting: "Waiting" };

function formatDuration(sec) {
  if (sec === null || sec === undefined) return "–";
  const s = Math.floor(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function resultCell(text, cls) {
  const td = document.createElement("td");
  td.textContent = text; // 用 textContent 防止 XSS
  if (cls) td.className = cls;
  return td;
}

async function showResults(code) {
  const board = await api.getLeaderboard(code);
  if (state.roomCode !== code) return; // 已经切换到别的游戏了
  const me = els.nickname.value.trim();

  els.resultsBody.innerHTML = "";
  for (const r of board.rows) {
    const tr = document.createElement("tr");
    if (r.nickname === me) tr.className = "me"; // 高亮自己那一行
    tr.appendChild(resultCell(r.rank, "rank"));
    const nameCell = resultCell(r.nickname);
    if (r.late) {
      const badge = document.createElement("span");
      badge.className = "late-badge";
      badge.textContent = "Late";
      nameCell.appendChild(badge);
    }
    tr.appendChild(nameCell);
    tr.appendChild(resultCell(`${r.rounds} / ${board.win_rounds}`));
    tr.appendChild(resultCell(RESULT_STATUS[r.status] || r.status, "st-" + r.status));
    tr.appendChild(resultCell(formatDuration(r.seconds_used)));
    els.resultsBody.appendChild(tr);
  }
  els.results.hidden = false;
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
    btn.textContent = sessionLabel(s);
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

function readNickname() {
  const name = els.nickname.value.trim();
  if (!name) {
    toast("Please enter a nickname first.");
    els.nickname.focus();
    return null;
  }
  saveNickname(name);
  return name;
}

function setRoomButtonsDisabled(on) {
  els.btnJoinRoom.disabled = on;
  els.btnCreateRoom.disabled = on;
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

// 新建单人游戏
async function newSession() {
  els.btnNew.disabled = true;
  try {
    const s = await api.createSession(readSettings());
    closeMenu(); // 手机上：开始游戏后收起菜单
    stopRoomPolling();
    resetGameStatus();
    state.sessions.unshift({ id: s.id, name: s.name, created_at: s.created_at });
    state.currentId = s.id;
    els.name.textContent = "Solo game";
    renderSessions();
    renderMessages(s.messages || []);
    applyState({ status: "playing", rounds: 0, win_rounds: s.win_rounds,
                 turn_limit: s.turn_limit, seconds_left: s.turn_limit });
  } catch (e) { toast(e.message); }
  finally { els.btnNew.disabled = false; }
}

// 加入房间（创建房间之后也会调用它，让房主也加入）
async function joinRoom(code, nickname) {
  const s = await api.joinRoom(code, nickname);
  if (!state.sessions.some((x) => x.id === s.id)) {
    state.sessions.unshift({ id: s.id, name: s.name, created_at: s.created_at });
  }
  await openSession(s.id);
}

async function onJoinClick() {
  const code = els.roomCodeInput.value.trim().toUpperCase();
  if (code.length !== 4) {
    toast("Room codes have 4 characters.");
    els.roomCodeInput.focus();
    return;
  }
  const nickname = readNickname();
  if (!nickname) return;
  setRoomButtonsDisabled(true);
  try { await joinRoom(code, nickname); }
  catch (e) { toast(e.message); }
  finally { setRoomButtonsDisabled(false); }
}

async function onCreateRoomClick() {
  const nickname = readNickname();
  if (!nickname) return;
  setRoomButtonsDisabled(true);
  try {
    const room = await api.createRoom(readSettings());
    els.roomCodeInput.value = room.code;
    await joinRoom(room.code, nickname); // 房主也加入，一起玩
  } catch (e) { toast(e.message); }
  finally { setRoomButtonsDisabled(false); }
}

async function openSession(id) {
  closeMenu(); // 手机上：选好游戏后收起菜单
  stopRoomPolling();
  resetGameStatus();
  state.currentId = id;
  const s = state.sessions.find((x) => x.id === id);
  els.name.textContent = s ? (s.name.startsWith("Room ") ? s.name : "Solo game · " + formatLocal(s.created_at)) : id;
  renderSessions();
  try {
    renderMessages(await api.getMessages(id));
    const st = await api.getState(id); // 后端算好还剩几秒，倒计时能接着走
    if (state.currentId !== id) return;
    applyState(st);
    if (st.room_code) startRoomPolling(st.room_code);
  } catch (e) {
    toast(e.message);
    // 存档已经不在了（比如过期了）：从列表里移除
    if (e.message.startsWith("Game not found")) {
      state.sessions = state.sessions.filter((x) => x.id !== id);
      state.currentId = null;
      renderSessions();
    }
  }
}

async function removeSession(id) {
  if (!(await confirmDialog("Delete this game?", "Delete", true))) return;
  try {
    await api.deleteSession(id);
    state.sessions = state.sessions.filter((s) => s.id !== id);
    if (state.currentId === id) {
      state.currentId = null;
      stopRoomPolling();
      resetGameStatus();
      els.name.textContent = "No game selected";
      els.messages.innerHTML = '<div class="welcome"><div class="big">🔤</div>Start a solo game, or join a classroom room on the left</div>';
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

async function onStartClick() {
  els.btnStart.disabled = true;
  try {
    await api.startRoom(state.roomCode);
    await pollRoom(); // 马上刷新一次，房主自己的游戏也立刻开始
  } catch (e) { toast(e.message); }
  finally { els.btnStart.disabled = false; }
}

async function onEndClick() {
  if (!(await confirmDialog("End the game for everyone? No one else will be able to join.", "End game", true))) return;
  els.btnEnd.disabled = true;
  try {
    await api.endRoom(state.roomCode);
    await pollRoom();
  } catch (e) { toast(e.message); }
  finally { els.btnEnd.disabled = false; }
}

/* =========================================================
 * 绑定事件
 * ========================================================= */
els.btnNew.onclick = newSession;
els.btnMenu.onclick = openMenu;
els.btnCloseMenu.onclick = closeMenu;
els.backdrop.onclick = closeMenu;
els.send.onclick = send;
els.btnJoinRoom.onclick = onJoinClick;
els.btnCreateRoom.onclick = onCreateRoomClick;
els.btnStart.onclick = onStartClick;
els.btnEnd.onclick = onEndClick;
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing) send(); // isComposing：输入法选字时按回车不发送
});
// 点输入框时，等键盘弹出来（大约 0.3 秒），再适配一次，确保能看到最新的消息
els.input.addEventListener("focus", () => setTimeout(fitToViewport, 300));
els.roomCodeInput.addEventListener("input", () => {
  els.roomCodeInput.value = els.roomCodeInput.value.toUpperCase(); // 房间码自动转大写
});
els.roomCodeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing) onJoinClick();
});

els.nickname.value = loadNickname(); // 记住上次用的昵称
fitToViewport();
loadSessions();