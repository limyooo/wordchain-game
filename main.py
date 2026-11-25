import json
import os
import random
import time
from datetime import datetime

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from mangum import Mangum
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

app = FastAPI(title="wordchain")

# ---------- 游戏设置（默认值，玩家可以在新建游戏时修改） ----------
DEFAULT_TURN_LIMIT = 10  # 每回合限时（秒）
DEFAULT_WIN_ROUNDS = 10  # 撑过这么多轮就赢
GRACE_SECONDS = 2        # 给网络延迟留一点余量


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
    """新建会话时返回：多了 messages 和这一局的设置"""
    messages: list[Message]
    turn_limit: int = DEFAULT_TURN_LIMIT
    win_rounds: int = DEFAULT_WIN_ROUNDS


class GameSession(Session):
    """存进文件的完整数据：比返回给前端的 Session 多了游戏状态"""
    used_words: list[str]
    status: str             # "playing" / "won" / "lost"
    rounds: int             # 玩家成功接上的词数
    turn_started_at: float  # 这一回合开始的时间（秒）


class OkResponse(BaseModel):
    ok: bool


class NewGameRequest(BaseModel):
    """新建游戏时玩家的设置，Field 会自动检查范围"""
    rounds: int = Field(DEFAULT_WIN_ROUNDS, ge=3, le=50)
    seconds: int = Field(DEFAULT_TURN_LIMIT, ge=5, le=60)


class ChatRequest(BaseModel):
    message: str


class ChatReply(BaseModel):
    reply: str
    status: str   # "playing" / "won" / "lost"，前端用来决定要不要继续倒计时
    rounds: int
    turn_limit: int


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
def load_api_key() -> str | None:
    """本地：从环境变量读取。Lambda 上：从 Secrets Manager 读取。"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    secret_arn = os.environ.get("ANTHROPIC_SECRET_ARN")
    if secret_arn:
        import boto3  # Lambda 自带 boto3，本地不需要安装
        resp = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        return resp["SecretString"]
    print("WARNING: no API key found, AI will not work")
    return None


client = anthropic.Anthropic(api_key=load_api_key())
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


# 新建会话：可以带上玩家的设置，不带就用默认值
@app.post("/api/sessions", response_model=Session)
def create_session(req: NewGameRequest | None = None):
    settings = req or NewGameRequest()
    now = datetime.now()
    sid = generate_session_id(now)

    first_word = random.choice(START_WORDS)  # 每局随机一个开局词

    data = {
        "id": sid,
        "name": sid,
        "created_at": now.isoformat(),
        "used_words": [first_word],  # 本局用过的词，最后一个就是“上一个词”
        "status": "playing",
        "rounds": 0,
        "turn_limit": settings.seconds,
        "win_rounds": settings.rounds,
        "turn_started_at": time.time(),  # 第一回合从现在开始计时
        "messages": [{
            "role": "assistant",
            "content": f"Let's play! My word is '{first_word}'. "
                       f"Your turn: give me a word starting with '{first_word[-1]}'. "
                       f"You have {settings.seconds} seconds per turn. "
                       f"Survive {settings.rounds} rounds to win!",
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
    # 旧存档没有这些字段，给默认值
    data.setdefault("status", "playing")
    data.setdefault("rounds", 0)
    data.setdefault("turn_started_at", time.time())
    data.setdefault("turn_limit", DEFAULT_TURN_LIMIT)
    data.setdefault("win_rounds", DEFAULT_WIN_ROUNDS)
    turn_limit = data["turn_limit"]
    win_rounds = data["win_rounds"]

    def result(reply: str) -> dict:
        """记下 AI 的回复、保存、返回给前端"""
        data["messages"].append({"role": "assistant", "content": reply})
        save_session(data)
        return {"reply": reply, "status": data["status"],
                "rounds": data["rounds"], "turn_limit": turn_limit}

    # 1. 游戏已经结束：不再接受新的词
    if data["status"] != "playing":
        return {"reply": "This game is over. Click 'New game' to play again!",
                "status": data["status"], "rounds": data["rounds"], "turn_limit": turn_limit}

    word = req.message.strip().lower()  # 去掉空格、转小写，"Egg " 也算 "egg"
    if word:
        data["messages"].append({"role": "user", "content": req.message})

    # 2. 先看有没有超时（后端判定，前端改不了）
    elapsed = time.time() - data["turn_started_at"]
    if elapsed > turn_limit + GRACE_SECONDS:
        data["status"] = "lost"
        return result(f"Time's up! You took more than {turn_limit} seconds. "
                      f"You survived {data['rounds']} rounds. Game over!")

    # 3. 检查规则：答错只提示，计时不重置
    error = check_word(word, data["used_words"])
    if error:
        return result(error)

    data["used_words"].append(word)
    data["rounds"] += 1

    # 4. 撑过设定的轮数：胜利
    if data["rounds"] >= win_rounds:
        data["status"] = "won"
        return result(f"Amazing! You survived {win_rounds} rounds. You win! 🎉")

    # 5. 让 AI 接词
    ai_word = ai_next_word(data["used_words"])
    if ai_word is None:
        data["status"] = "won"
        return result(f"I can't think of a word starting with '{word[-1]}'. You win! 🎉")

    data["used_words"].append(ai_word)
    data["turn_started_at"] = time.time()  # AI 说完话，新回合开始计时
    return result(f"Nice! ({data['rounds']}/{win_rounds}) My word is '{ai_word}'. "
                  f"Your turn: a word starting with '{ai_word[-1]}'.")


# Lambda 的入口：Mangum 把 Lambda 的请求翻译成 FastAPI 能懂的格式
handler = Mangum(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)