/* =========================================================
 * 排行榜页面：适合投在大屏幕上，每 3 秒自动刷新
 * 网址：/static/board.html?code=K7QX
 * ========================================================= */
const REFRESH_MS = 3000;
const $ = (id) => document.getElementById(id);

const els = {
  room: $("boardRoom"), status: $("boardStatus"), form: $("codeForm"), codeInput: $("codeInput"),
  table: $("boardTable"), body: $("boardBody"), empty: $("boardEmpty"), toast: $("toast"),
};

let code = (new URLSearchParams(location.search).get("code") || "").trim().toUpperCase();
let timerId = null;

const STATUS_LABEL = { playing: "Playing", won: "🎉 Won", lost: "💀 Out", waiting: "Waiting" };
const ROOM_LABEL = { waiting: "Waiting to start", playing: "Live", finished: "Ended" };

function toast(text) {
  els.toast.textContent = text;
  els.toast.style.display = "block";
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (els.toast.style.display = "none"), 3500);
}

// 秒数显示成 分:秒
function formatTime(sec) {
  if (sec === null || sec === undefined) return "–";
  const s = Math.floor(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function cell(text, cls) {
  const td = document.createElement("td");
  td.textContent = text; // 用 textContent 防止有人在昵称里写网页代码
  if (cls) td.className = cls;
  return td;
}

function render(board) {
  els.room.textContent = `Room ${board.code}`;
  els.status.textContent = ROOM_LABEL[board.status] || board.status;
  els.status.className = "pill " + board.status;
  document.title = `Room ${board.code} · WordChain leaderboard`;

  els.body.innerHTML = "";
  for (const r of board.rows) {
    const tr = document.createElement("tr");
    tr.appendChild(cell(r.rank, "rank"));

    const nameCell = cell(r.nickname);
    if (r.late) {
      const badge = document.createElement("span");
      badge.className = "late-badge";
      badge.textContent = "Late";
      nameCell.appendChild(badge);
    }
    tr.appendChild(nameCell);

    tr.appendChild(cell(`${r.rounds} / ${board.win_rounds}`));
    tr.appendChild(cell(STATUS_LABEL[r.status] || r.status, "st-" + r.status));
    tr.appendChild(cell(formatTime(r.seconds_used)));
    els.body.appendChild(tr);
  }
  els.table.hidden = board.rows.length === 0;
  els.empty.hidden = board.rows.length !== 0;
}

async function refresh() {
  try {
    const res = await fetch(`/api/rooms/${code}/leaderboard`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    render(await res.json());
  } catch (e) {
    toast(e.message);
  }
}

function start() {
  els.form.hidden = true;
  history.replaceState(null, "", `?code=${code}`); // 网址带上房间码，方便分享和刷新
  refresh();
  clearInterval(timerId);
  timerId = setInterval(refresh, REFRESH_MS);
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const value = els.codeInput.value.trim().toUpperCase();
  if (value.length !== 4) {
    toast("Room codes have 4 characters.");
    return;
  }
  code = value;
  start();
});

if (code) {
  start();
} else {
  els.form.hidden = false; // 没有房间码：先让用户输入
  els.codeInput.focus();
}