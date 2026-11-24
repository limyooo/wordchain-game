import json
import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import FileResponse

app = FastAPI(title="wordchain")


# ---------- 返回数据的格式 ----------
class Message(BaseModel):
    role: str          # "user" 或 "assistant"
    content: str


class SessionInfo(BaseModel):
    """会话列表里每一项"""
    id: str
    name: str
    created_at: str


class Session(SessionInfo):
    """新建会话时返回：在 SessionInfo 基础上多了 messages"""
    messages: list[Message]


class OkResponse(BaseModel):
    ok: bool
app.mount("/static", StaticFiles(directory="static"), name="static")

# 存放会话的目录（相对 main.py，而不是相对启动时的 cwd）
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


def session_path(sid: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def generate_session_id(now: datetime) -> str:
    base = now.strftime("%Y-%m-%d_%H-%M-%S")
    sid, n = base, 1
    while os.path.exists(session_path(sid)):  # 同一秒内重复创建时加后缀
        sid = f"{base}_{n}"
        n += 1
    return sid


def save_session(data: dict) -> None:
    with open(session_path(data["id"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_session(sid: str) -> dict:
    path = session_path(sid)
    # 防止 id 里带 ../ 之类读到目录外的文件
    if os.path.dirname(os.path.abspath(path)) != SESSIONS_DIR or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="session not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/")
def root():
    return FileResponse("static/index.html")


# 会话列表：最新的在前
@app.get("/api/sessions", response_model=list[SessionInfo])
def list_sessions():
    sessions = []
    for filename in os.listdir(SESSIONS_DIR):
        if filename.endswith(".json"):
            data = load_session(filename[:-5])
            sessions.append({"id": data["id"], "name": data["name"], "created_at": data["created_at"]})
    sessions.sort(key=lambda s: s["created_at"], reverse=True)
    return sessions


# 新建会话
@app.post("/api/sessions", response_model=Session)
# 新建会话
@app.post("/api/sessions", response_model=Session)
def create_session():
    now = datetime.now()
    # 1. 生成会话 id / 名称
    sid = generate_session_id(now)

    # TODO: 之后换成让 AI 随机出第一个词
    first_word = "apple"

    data = {
        "id": sid,
        "name": sid,
        "created_at": now.isoformat(),
        "used_words": [first_word],  # 本局用过的词，最后一个就是“上一个词”
        "messages": [{
            "role": "assistant",
            "content": f"Let's play! My word is '{first_word}'. "
                       f"Your turn: give me a word starting with '{first_word[-1]}'.",
        }],
    }
    # 2. 存到文件
    save_session(data)
    # 3. 返回 JSON
    return data


# 删除会话
@app.delete("/api/sessions/{sid}", response_model=OkResponse)
def delete_session(sid: str):
    load_session(sid)  # 不存在会返回 404
    os.remove(session_path(sid))
    return {"ok": True}


# 获取某个会话的消息
@app.get("/api/sessions/{sid}/messages", response_model=list[Message])
def get_messages(sid: str):
    return load_session(sid)["messages"]

# TODO: POST /api/sessions/{sid}/chat  body: {message} -> {reply}（需要接 AI）


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
