import json
import os
import random
from datetime import datetime

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import FileResponse

from mangum import Mangum
app = FastAPI(title="wordchain")


# ---------- 数据模型 ----------
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


class GameSession(Session):
    """存进文件的完整数据：比返回给前端的 Session 多了游戏状态"""
    used_words: list[str]


class OkResponse(BaseModel):
    ok: bool


class ChatRequest(BaseModel):
    message: str


class ChatReply(BaseModel):
    reply: str

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Lambda 上只有 /tmp 可以写文件；本地还是用项目里的 sessions 文件夹
ON_LAMBDA = "AWS_LAMBDA_FUNCTION_NAME" in os.environ
if ON_LAMBDA:
    SESSIONS_DIR = "/tmp/sessions"
else:
    SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# 英文词典：启动时读一次，放进 set 里，查询很快
# 如果还没下载 words.txt，就先跳过“是不是真单词”的检查
WORDS_PATH = os.path.join(BASE_DIR, "words.txt")
if os.path.exists(WORDS_PATH):
    with open(WORDS_PATH, encoding="utf-8") as f:
        DICTIONARY = {line.strip().lower() for line in f if line.strip()}
else:
    DICTIONARY = set()
    print("WARNING: words.txt not found, dictionary check is disabled")


# ---------- 存储 ----------
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


# ---------- 游戏规则 ----------
def check_word(word: str, used_words: list[str]) -> str | None:
    """检查一个词是否合规。合规返回 None，不合规返回错误提示。"""
    last_word = used_words[-1]

    if not word.isalpha():
        return "Please type one English word (letters only)."
    if len(word) < 3:
        return "Words need at least 3 letters. Try a longer one!"
    if word[0] != last_word[-1]:
        return f"'{word}' doesn't start with '{last_word[-1]}'. Try again!"
    if DICTIONARY and word not in DICTIONARY:
        return f"'{word}' isn't in my dictionary. Try a real English word!"
    if word in used_words:
        return f"'{word}' was already used. Try another word!"
    return None


# ---------- AI ----------
if not os.environ.get("ANTHROPIC_API_KEY"):
    print("WARNING: ANTHROPIC_API_KEY is not set, AI will not work")

client = anthropic.Anthropic()  # 自动从环境变量 ANTHROPIC_API_KEY 读取 key
MODEL = "claude-haiku-5-5"

# 开局词：从这些常用词里随机挑一个
START_WORDS = ["apple", "banana", "house", "river", "music", "garden", "pencil",
               "window", "tiger", "ocean", "planet", "candle", "forest", "rabbit",
               "coffee", "school", "mountain", "dragon", "summer", "bridge"]


def ai_next_word(used_words: list[str]) -> str | None:
    """让 Claude 接下一个词，并用同样的规则检查。试 3 次都不合规就返回 None。"""
    last_letter = used_words[-1][-1]
    prompt = (
        "We are playing an English word chain game. "
        f"Give me ONE common English word that starts with '{last_letter}'. "
        f"Do not use any of these words: {', '.join(used_words)}. "
        "Reply with the word only, nothing else."
    )

    for _ in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError:
            raise HTTPException(status_code=503, detail="AI is unavailable, please try again")

        word = resp.content[0].text.strip().lower().strip(".,!?'\"")
        if check_word(word, used_words) is None:  # AI 的词也要过同样的规则
            return word
    return None


# ---------- 接口 ----------
@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


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
def create_session():
    now = datetime.now()
    sid = generate_session_id(now)

    first_word = random.choice(START_WORDS)  # 每局随机一个开局词

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
    save_session(data)
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


# 聊天：玩家出一个词，AI 接下一个
@app.post("/api/sessions/{sid}/chat", response_model=ChatReply)
def chat(sid: str, req: ChatRequest):
    data = load_session(sid)
    word = req.message.strip().lower()  # 去掉空格、转小写，"Egg " 也算 "egg"
    data["messages"].append({"role": "user", "content": req.message})

    error = check_word(word, data["used_words"])
    if error:
        reply = error
    else:
        data["used_words"].append(word)
        ai_word = ai_next_word(data["used_words"])
        if ai_word is None:
            reply = f"I can't think of a word starting with '{word[-1]}'. You win!"
        else:
            data["used_words"].append(ai_word)
            reply = (f"Nice! My word is '{ai_word}'. "
                     f"Your turn: a word starting with '{ai_word[-1]}'.")

    data["messages"].append({"role": "assistant", "content": reply})
    save_session(data)
    return {"reply": reply}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    # Lambda 的入口：Mangum 把 Lambda 的请求翻译成 FastAPI 能懂的格式
    handler = Mangum(app)