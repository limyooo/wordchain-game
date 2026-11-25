import json
import logging
import os
import random
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from mangum import Mangum
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse

# ---------- 日志 ----------
# 本地：basicConfig 会把日志打印到 Terminal
# Lambda：运行环境已经配置好了日志输出，basicConfig 不会重复配置，日志自动进 CloudWatch
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("wordchain")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(title="wordchain")

# ---------- 游戏设置 ----------
DEFAULT_TURN_LIMIT = 10  # 每回合限时（秒）
DEFAULT_WIN_ROUNDS = 10  # 撑过这么多轮就赢
GRACE_SECONDS = 2        # 给网络延迟留一点余量
SESSION_TTL_DAYS = 30    # 游戏记录保留多少天（只对 DynamoDB 生效）

# ---------- 房间设置 ----------
ROOM_TTL_SECONDS = 24 * 3600                     # 房间保留 1 天
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 去掉了容易看错的 0 O 1 I L
ROOM_CODE_LENGTH = 4
# 用来“算出”房间游戏 ID 的命名空间：同一个人在同一个房间，算出来的 ID 永远一样
ROOM_GAME_NAMESPACE = uuid.UUID("6f1c2b8e-3d4a-4f5b-9c7d-1e2f3a4b5c6d")


def utc_now_iso() -> str:
    """带时区的 UTC 时间，前端会转换成玩家的本地时间显示"""
    return datetime.now(timezone.utc).isoformat()


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
    """新建/加入游戏时返回"""
    messages: list[Message]
    turn_limit: int = DEFAULT_TURN_LIMIT
    win_rounds: int = DEFAULT_WIN_ROUNDS
    status: str = "playing"        # "waiting" / "playing" / "won" / "lost"
    room_code: str | None = None   # 单人游戏没有房间码


class OkResponse(BaseModel):
    ok: bool


class NewGameRequest(BaseModel):
    """新建单人游戏 / 创建房间时的设置，Field 会自动检查范围"""
    rounds: int = Field(DEFAULT_WIN_ROUNDS, ge=3, le=50)
    seconds: int = Field(DEFAULT_TURN_LIMIT, ge=5, le=60)


class JoinRoomRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=20)


class ChatRequest(BaseModel):
    message: str = Field("", max_length=50)  # 一个单词不会超过 50 个字母


class ChatReply(BaseModel):
    reply: str
    status: str   # "waiting" / "playing" / "won" / "lost"
    rounds: int
    turn_limit: int


class GameState(BaseModel):
    """一局游戏现在的状态，前端用它来恢复倒计时"""
    status: str
    rounds: int
    win_rounds: int
    turn_limit: int
    seconds_left: float | None  # 这一回合还剩几秒；没在玩就是 None
    room_code: str | None


class RoomInfo(BaseModel):
    code: str
    status: str                 # "waiting" / "playing" / "finished"
    rounds: int
    seconds: int
    is_host: bool               # 我是不是房主
    players: list[str]          # 已加入的昵称
    my_game_id: str | None      # 我在这个房间的游戏 ID（还没加入就是 None）


class LeaderboardRow(BaseModel):
    rank: int
    nickname: str
    status: str
    rounds: int
    late: bool
    seconds_used: float | None  # 从自己开始算起用了多久；还没开始就是 None


class Leaderboard(BaseModel):
    code: str
    status: str
    win_rounds: int
    rows: list[LeaderboardRow]


# ---------- 玩家身份 ----------
def get_player_id(
    x_player_id: str = Header(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$"),
) -> str:
    """从请求头 X-Player-Id 读取玩家 ID。格式不对 FastAPI 会自动返回 422。"""
    return x_player_id


def short(player_id: str) -> str:
    """日志里只记录玩家 ID 的前 8 位，够用来排查问题，又不会暴露完整 ID"""
    return player_id[:8]


# ---------- 请求日志 + 兜底的异常处理 ----------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """每个请求记录一行：方法、路径、状态码、耗时。没想到的错误也在这里兜住。"""
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        ms = (time.perf_counter() - start) * 1000
        logger.exception("Unhandled error on %s %s (%.0f ms)", request.method, request.url.path, ms)
        return JSONResponse(status_code=500,
                            content={"detail": "Something went wrong on our side. Please try again."})
    ms = (time.perf_counter() - start) * 1000
    logger.info("%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code, ms)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """我们主动抛出的错误（404、503 等）：5xx 记 ERROR，4xx 记 WARNING"""
    level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    logger.log(level, "HTTP %d on %s %s: %s", exc.status_code, request.method, request.url.path, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """请求格式不对（比如轮数填了 1000）：返回一句看得懂的话"""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "header")) or "request"
    msg = f"Invalid {field}: {first.get('msg', 'bad input')}"
    logger.warning("Validation error on %s %s: %s", request.method, request.url.path, msg)
    return JSONResponse(status_code=422, content={"detail": msg})


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# 英文词典：启动时读一次，放进 set 里，查询很快
WORDS_PATH = os.path.join(BASE_DIR, "words.txt")
if os.path.exists(WORDS_PATH):
    with open(WORDS_PATH, encoding="utf-8") as f:
        DICTIONARY = {line.strip().lower() for line in f if line.strip()}
    logger.info("Dictionary loaded: %d words", len(DICTIONARY))
else:
    DICTIONARY = set()
    logger.warning("words.txt not found, dictionary check is disabled")


# ---------- 存储 ----------
# 云上：Lambda 有 TABLE_NAME / ROOMS_TABLE_NAME 环境变量，用 DynamoDB
# 本地：没有这两个变量，用项目里的 sessions / rooms 文件夹
TABLE_NAME = os.environ.get("TABLE_NAME")
ROOMS_TABLE_NAME = os.environ.get("ROOMS_TABLE_NAME")
PLAYER_INDEX = "player-created-index"
ROOM_INDEX = "room-created-index"

if TABLE_NAME:
    import boto3  # Lambda 自带 boto3，本地不需要安装
    from boto3.dynamodb.conditions import Key

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)
    rooms_table = dynamodb.Table(ROOMS_TABLE_NAME) if ROOMS_TABLE_NAME else None
    logger.info("Storage: DynamoDB tables %s, %s", TABLE_NAME, ROOMS_TABLE_NAME)
else:
    SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
    ROOMS_DIR = os.path.join(BASE_DIR, "rooms")
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(ROOMS_DIR, exist_ok=True)
    logger.info("Storage: local files in %s and %s", SESSIONS_DIR, ROOMS_DIR)


def plain(value):
    """DynamoDB 读出来的数字是 Decimal，转回 Python 的 int 或 float"""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def to_dynamo(value):
    """DynamoDB 不接受 Python 的 float，要转成 Decimal"""
    return Decimal(str(value)) if isinstance(value, float) else value


def session_path(sid: str) -> str:
    """本地文件存储：会话文件的路径"""
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def save_session(data: dict) -> None:
    if TABLE_NAME:
        item = {
            "id": data["id"],
            "player_id": data["owner"],        # 给索引 1 用：按玩家查找
            "created_at": data["created_at"],  # 两个索引都用它排序
            "name": data["name"],
            "status": data.get("status", "playing"),
            "rounds": data.get("rounds", 0),
            "late": bool(data.get("late", False)),
            "expires_at": int(time.time()) + SESSION_TTL_DAYS * 86400,
            "data": json.dumps(data, ensure_ascii=False),  # 整局游戏存成一段文字
        }
        # 只有房间游戏才有这些字段；没有 room_code 就不会进索引 2（稀疏索引）
        for key in ("room_code", "nickname", "started_at", "finished_at"):
            if data.get(key) is not None:
                item[key] = to_dynamo(data[key])
        try:
            table.put_item(Item=item)
        except Exception:
            logger.exception("DynamoDB put_item failed for session %s", data["id"][:8])
            raise HTTPException(status_code=500, detail="Could not save the game. Please try again.")
        return

    try:
        with open(session_path(data["id"]), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.exception("Failed to save session %s", data["id"][:8])
        raise HTTPException(status_code=500, detail="Could not save the game. Please try again.")


def load_session(sid: str) -> dict:
    not_found = HTTPException(status_code=404, detail="Game not found. It may have expired.")

    if TABLE_NAME:
        try:
            item = table.get_item(Key={"id": sid}).get("Item")
        except Exception:
            logger.exception("DynamoDB get_item failed for session %s", sid[:8])
            raise HTTPException(status_code=500, detail="Could not load the game. Please try again.")
        if not item:
            raise not_found
        return json.loads(item["data"])  # 把文字还原成整局游戏

    path = session_path(sid)
    # 防止 id 里带 ../ 之类读到目录外的文件
    if os.path.dirname(os.path.abspath(path)) != SESSIONS_DIR or not os.path.exists(path):
        raise not_found
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.error("Save file is corrupted: %s", path)
        raise HTTPException(status_code=500, detail="This game's save file is damaged. Please start a new game.")


def delete_session_data(sid: str) -> None:
    if TABLE_NAME:
        try:
            table.delete_item(Key={"id": sid})
        except Exception:
            logger.exception("DynamoDB delete_item failed for session %s", sid[:8])
            raise HTTPException(status_code=500, detail="Could not delete the game. Please try again.")
        return
    os.remove(session_path(sid))


def list_player_sessions(player_id: str) -> list[dict]:
    """列出某个玩家的游戏，最新的在前"""
    if TABLE_NAME:
        try:
            resp = table.query(
                IndexName=PLAYER_INDEX,
                KeyConditionExpression=Key("player_id").eq(player_id),
                ScanIndexForward=False,  # 按 created_at 倒序，最新的在前
                Limit=50,
            )
        except Exception:
            logger.exception("DynamoDB query failed for player %s", short(player_id))
            raise HTTPException(status_code=500, detail="Could not load your games. Please try again.")
        return [{"id": i["id"], "name": i["name"], "created_at": i["created_at"]} for i in resp["Items"]]

    sessions = []
    for filename in os.listdir(SESSIONS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            data = load_session(filename[:-5])
        except HTTPException:
            continue  # 损坏的存档跳过
        if data.get("owner") == player_id:
            sessions.append({"id": data["id"], "name": data["name"], "created_at": data["created_at"]})
    sessions.sort(key=lambda s: s["created_at"], reverse=True)
    return sessions


def load_owned_session(sid: str, player_id: str) -> dict:
    """读取会话，并确认它属于这个玩家。不属于就当作不存在（返回 404），不暴露别人的游戏。"""
    data = load_session(sid)
    if data.get("owner") != player_id:
        logger.warning("Player %s tried to access game %s owned by someone else", short(player_id), sid[:8])
        raise HTTPException(status_code=404, detail="Game not found. It may have expired.")
    return data


# ---------- 房间存储 ----------
def room_path(code: str) -> str:
    return os.path.join(ROOMS_DIR, f"{code}.json")


def save_room(room: dict, new: bool = False) -> bool:
    """保存房间。new=True 时，如果房间码已经存在就不覆盖，返回 False。"""
    if TABLE_NAME:
        item = {k: to_dynamo(v) for k, v in room.items() if v is not None}
        kwargs = {"Item": item}
        if new:
            # 条件写入：只有这个房间码还不存在时才写，防止两个房间撞码
            kwargs["ConditionExpression"] = "attribute_not_exists(code)"
        try:
            rooms_table.put_item(**kwargs)
            return True
        except rooms_table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        except Exception:
            logger.exception("DynamoDB put_item failed for room %s", room["code"])
            raise HTTPException(status_code=500, detail="Could not save the room. Please try again.")

    path = room_path(room["code"])
    if new and os.path.exists(path):
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(room, f, ensure_ascii=False, indent=2)
    return True


def normalize_code(code: str) -> str:
    """房间码不区分大小写，去掉空格；格式不对直接当作不存在"""
    code = code.strip().upper()
    if len(code) != ROOM_CODE_LENGTH or any(c not in ROOM_CODE_ALPHABET for c in code):
        raise HTTPException(status_code=404, detail="Room not found. Check the code and try again.")
    return code


def load_room(code: str) -> dict:
    code = normalize_code(code)
    not_found = HTTPException(status_code=404, detail="Room not found. Check the code and try again.")

    if TABLE_NAME:
        try:
            item = rooms_table.get_item(Key={"code": code}).get("Item")
        except Exception:
            logger.exception("DynamoDB get_item failed for room %s", code)
            raise HTTPException(status_code=500, detail="Could not load the room. Please try again.")
        if not item:
            raise not_found
        return {k: plain(v) for k, v in item.items()}

    path = room_path(code)
    if not os.path.exists(path):
        raise not_found
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_room_games(code: str) -> list[dict]:
    """列出房间里所有人的游戏（只要排行榜需要的字段），按加入顺序"""
    if TABLE_NAME:
        items = []
        kwargs = {"IndexName": ROOM_INDEX, "KeyConditionExpression": Key("room_code").eq(code)}
        try:
            while True:  # 结果太多时 DynamoDB 会分页，要一页一页读
                resp = table.query(**kwargs)
                items.extend(resp["Items"])
                if "LastEvaluatedKey" not in resp or len(items) >= 200:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except Exception:
            logger.exception("DynamoDB query failed for room %s", code)
            raise HTTPException(status_code=500, detail="Could not load the room. Please try again.")
        return [{
            "id": i["id"],
            "nickname": i.get("nickname", ""),
            "status": i.get("status", "playing"),
            "rounds": plain(i.get("rounds", 0)),
            "late": bool(i.get("late", False)),
            "started_at": plain(i.get("started_at")),
            "finished_at": plain(i.get("finished_at")),
        } for i in items]

    games = []
    for filename in os.listdir(SESSIONS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            data = load_session(filename[:-5])
        except HTTPException:
            continue
        if data.get("room_code") == code:
            games.append({
                "id": data["id"],
                "nickname": data.get("nickname", ""),
                "status": data.get("status", "playing"),
                "rounds": data.get("rounds", 0),
                "late": data.get("late", False),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "created_at": data["created_at"],
            })
    games.sort(key=lambda g: g["created_at"])
    return games


def room_game_id(code: str, player_id: str) -> str:
    """同一个人在同一个房间，算出来的游戏 ID 永远一样，所以每人只能有一局"""
    return uuid.uuid5(ROOM_GAME_NAMESPACE, f"{code}:{player_id}").hex


def room_info(room: dict, player_id: str) -> dict:
    games = list_room_games(room["code"])
    my_id = room_game_id(room["code"], player_id)
    return {
        "code": room["code"],
        "status": room["status"],
        "rounds": room["rounds"],
        "seconds": room["seconds"],
        "is_host": room["host_id"] == player_id,
        "players": [g["nickname"] for g in games],
        "my_game_id": my_id if any(g["id"] == my_id for g in games) else None,
    }


def require_host(room: dict, player_id: str) -> None:
    if room["host_id"] != player_id:
        raise HTTPException(status_code=403, detail="Only the host can do this.")


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


def start_message(first_word: str, seconds: int, rounds: int) -> dict:
    return {
        "role": "assistant",
        "content": f"Let's play! My word is '{first_word}'. "
                   f"Your turn: give me a word starting with '{first_word[-1]}'. "
                   f"You have {seconds} seconds per turn. "
                   f"Survive {rounds} rounds to win!",
    }


def fill_defaults(data: dict) -> None:
    """旧存档没有这些字段，给默认值"""
    data.setdefault("status", "playing")
    data.setdefault("rounds", 0)
    data.setdefault("turn_started_at", time.time())
    data.setdefault("turn_limit", DEFAULT_TURN_LIMIT)
    data.setdefault("win_rounds", DEFAULT_WIN_ROUNDS)


def activate_if_room_started(data: dict) -> None:
    """懒更新：房间游戏还在等待，但房主已经点了 Start，就在这里把这局切换成进行中"""
    if data.get("status") != "waiting" or not data.get("room_code"):
        return
    room = load_room(data["room_code"])
    if room["status"] == "waiting":
        return
    start = room.get("started_at") or time.time()
    data["status"] = "playing"
    data["started_at"] = start
    data["turn_started_at"] = start  # 第一回合从房主点 Start 的那一刻算起
    data["messages"].append(start_message(data["used_words"][0], data["turn_limit"], data["win_rounds"]))
    save_session(data)
    logger.info("Room game %s activated in room %s", data["id"][:8], data["room_code"])


# ---------- AI ----------
def load_api_key() -> str | None:
    """本地：从环境变量读取。Lambda 上：从 Secrets Manager 读取。"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        logger.info("API key loaded from environment variable")
        return key
    secret_arn = os.environ.get("ANTHROPIC_SECRET_ARN")
    if secret_arn:
        try:
            import boto3  # Lambda 自带 boto3，本地不需要安装
            resp = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
            logger.info("API key loaded from Secrets Manager")
            return resp["SecretString"]
        except Exception:
            # 只记录出错的类型，不记录 key 本身
            logger.exception("Failed to read API key from Secrets Manager")
            return None
    logger.warning("No API key found, AI will not work")
    return None


# timeout：单次调用最多等 10 秒；max_retries：网络抖动时自动重试 1 次
client = anthropic.Anthropic(api_key=load_api_key(), timeout=10.0, max_retries=1)
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

    for attempt in range(1, 4):
        start = time.perf_counter()
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            logger.error("Claude API: authentication failed, check the API key")
            raise HTTPException(status_code=503, detail="AI is not configured correctly.")
        except anthropic.RateLimitError:
            logger.error("Claude API: rate limited")
            raise HTTPException(status_code=503, detail="AI is busy right now. Please try again in a moment.")
        except anthropic.APITimeoutError:
            logger.error("Claude API: request timed out")
            raise HTTPException(status_code=503, detail="AI took too long to answer. Please try again.")
        except anthropic.APIError as e:
            logger.error("Claude API error: %s", type(e).__name__)
            raise HTTPException(status_code=503, detail="AI is unavailable, please try again.")

        ms = (time.perf_counter() - start) * 1000
        word = resp.content[0].text.strip().lower().strip(".,!?'\"")
        error = check_word(word, used_words)  # AI 的词也要过同样的规则
        logger.info("Claude attempt %d: '%s' in %.0f ms, tokens in=%d out=%d%s",
                    attempt, word, ms, resp.usage.input_tokens, resp.usage.output_tokens,
                    "" if error is None else f" (rejected: {error})")
        if error is None:
            return word

    logger.warning("Claude failed to give a valid word for '%s' after 3 attempts", last_letter)
    return None


# ---------- 接口：页面 ----------
@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


# ---------- 接口：单人游戏 ----------
# 会话列表：只返回自己的游戏，最新的在前
@app.get("/api/sessions", response_model=list[SessionInfo])
def list_sessions(player_id: str = Depends(get_player_id)):
    return list_player_sessions(player_id)


# 新建单人游戏：可以带上玩家的设置，不带就用默认值
@app.post("/api/sessions", response_model=Session)
def create_session(req: NewGameRequest | None = None, player_id: str = Depends(get_player_id)):
    settings = req or NewGameRequest()
    sid = uuid.uuid4().hex  # 随机 ID，别人猜不到
    first_word = random.choice(START_WORDS)
    now = time.time()

    data = {
        "id": sid,
        "name": "Solo game",
        "created_at": utc_now_iso(),
        "owner": player_id,
        "used_words": [first_word],  # 本局用过的词，最后一个就是“上一个词”
        "status": "playing",
        "rounds": 0,
        "turn_limit": settings.seconds,
        "win_rounds": settings.rounds,
        "started_at": now,
        "turn_started_at": now,  # 第一回合从现在开始计时
        "messages": [start_message(first_word, settings.seconds, settings.rounds)],
    }
    save_session(data)
    logger.info("Game %s created by player %s: first word '%s', %d rounds, %ds per turn",
                sid[:8], short(player_id), first_word, settings.rounds, settings.seconds)
    return data


# 删除会话：只能删自己的
@app.delete("/api/sessions/{sid}", response_model=OkResponse)
def delete_session(sid: str, player_id: str = Depends(get_player_id)):
    load_owned_session(sid, player_id)  # 不存在或不是自己的，返回 404
    delete_session_data(sid)
    logger.info("Game %s deleted by player %s", sid[:8], short(player_id))
    return {"ok": True}


# 获取某个会话的消息：只能看自己的
@app.get("/api/sessions/{sid}/messages", response_model=list[Message])
def get_messages(sid: str, player_id: str = Depends(get_player_id)):
    data = load_owned_session(sid, player_id)
    activate_if_room_started(data)
    return data["messages"]


# 一局游戏现在的状态：前端用来恢复倒计时，也用来在大厅里等待开始
@app.get("/api/sessions/{sid}/state", response_model=GameState)
def game_state(sid: str, player_id: str = Depends(get_player_id)):
    data = load_owned_session(sid, player_id)
    fill_defaults(data)
    activate_if_room_started(data)
    seconds_left = None
    if data["status"] == "playing":
        seconds_left = max(0.0, data["turn_limit"] - (time.time() - data["turn_started_at"]))
    return {
        "status": data["status"],
        "rounds": data["rounds"],
        "win_rounds": data["win_rounds"],
        "turn_limit": data["turn_limit"],
        "seconds_left": seconds_left,
        "room_code": data.get("room_code"),
    }


# 聊天：玩家出一个词，AI 接下一个（单人和房间游戏共用）
@app.post("/api/sessions/{sid}/chat", response_model=ChatReply)
def chat(sid: str, req: ChatRequest, player_id: str = Depends(get_player_id)):
    data = load_owned_session(sid, player_id)
    fill_defaults(data)
    activate_if_room_started(data)
    turn_limit = data["turn_limit"]
    win_rounds = data["win_rounds"]
    gid = sid[:8]  # 日志里用短 ID

    def result(reply: str) -> dict:
        """记下 AI 的回复、保存、返回给前端"""
        if data["status"] in ("won", "lost") and not data.get("finished_at"):
            data["finished_at"] = time.time()  # 排行榜用：什么时候结束的
        data["messages"].append({"role": "assistant", "content": reply})
        save_session(data)
        return {"reply": reply, "status": data["status"],
                "rounds": data["rounds"], "turn_limit": turn_limit}

    # 0. 房间还没开始：不接受单词
    if data["status"] == "waiting":
        return {"reply": "The game hasn't started yet. Waiting for the host...",
                "status": "waiting", "rounds": 0, "turn_limit": turn_limit}

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
        logger.info("Game %s lost: timed out after %.1fs (limit %ds), %d rounds",
                    gid, elapsed, turn_limit, data["rounds"])
        return result(f"Time's up! You took more than {turn_limit} seconds. "
                      f"You survived {data['rounds']} rounds. Game over!")

    # 3. 检查规则：答错只提示，计时不重置
    error = check_word(word, data["used_words"])
    if error:
        logger.info("Game %s: player word '%s' rejected (%s)", gid, word, error)
        return result(error)

    data["used_words"].append(word)
    data["rounds"] += 1
    logger.info("Game %s: player word '%s' accepted in %.1fs, round %d/%d",
                gid, word, elapsed, data["rounds"], win_rounds)

    # 4. 撑过设定的轮数：胜利
    if data["rounds"] >= win_rounds:
        data["status"] = "won"
        logger.info("Game %s won: survived %d rounds", gid, win_rounds)
        return result(f"Amazing! You survived {win_rounds} rounds. You win! 🎉")

    # 5. 让 AI 接词
    ai_word = ai_next_word(data["used_words"])
    if ai_word is None:
        data["status"] = "won"
        logger.info("Game %s won: AI could not continue from '%s'", gid, word[-1])
        return result(f"I can't think of a word starting with '{word[-1]}'. You win! 🎉")

    data["used_words"].append(ai_word)
    data["turn_started_at"] = time.time()  # AI 说完话，新回合开始计时
    return result(f"Nice! ({data['rounds']}/{win_rounds}) My word is '{ai_word}'. "
                  f"Your turn: a word starting with '{ai_word[-1]}'.")


# ---------- 接口：房间 ----------
# 创建房间：创建的人就是房主
@app.post("/api/rooms", response_model=RoomInfo)
def create_room(req: NewGameRequest | None = None, player_id: str = Depends(get_player_id)):
    settings = req or NewGameRequest()
    for _ in range(10):  # 万一随机到已经存在的房间码，就再换一个
        code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
        room = {
            "code": code,
            "host_id": player_id,
            "status": "waiting",
            "rounds": settings.rounds,
            "seconds": settings.seconds,
            "created_at": utc_now_iso(),
            "expires_at": int(time.time()) + ROOM_TTL_SECONDS,
        }
        if save_room(room, new=True):
            break
    else:
        raise HTTPException(status_code=503, detail="Could not create a room. Please try again.")

    logger.info("Room %s created by player %s: %d rounds, %ds per turn",
                code, short(player_id), settings.rounds, settings.seconds)
    return room_info(room, player_id)


# 查看房间：状态、已加入的人、我是不是房主
@app.get("/api/rooms/{code}", response_model=RoomInfo)
def get_room(code: str, player_id: str = Depends(get_player_id)):
    return room_info(load_room(code), player_id)


# 加入房间：只要房间还没结束就能加入；已经加入过就回到原来那局
@app.post("/api/rooms/{code}/join", response_model=Session)
def join_room(code: str, req: JoinRoomRequest, player_id: str = Depends(get_player_id)):
    room = load_room(code)
    code = room["code"]
    gid = room_game_id(code, player_id)

    # 已经加入过：直接返回原来那局（每人只能玩一局）
    try:
        existing = load_session(gid)
        activate_if_room_started(existing)
        logger.info("Player %s rejoined room %s", short(player_id), code)
        return existing
    except HTTPException as e:
        if e.status_code != 404:
            raise

    if room["status"] == "finished":
        raise HTTPException(status_code=409, detail="This room has ended.")

    nickname = req.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="Please enter a nickname.")

    first_word = random.choice(START_WORDS)  # 每人随机一个开局词
    playing = room["status"] == "playing"    # 房间已经开始：迟到加入，马上开始
    now = time.time()

    data = {
        "id": gid,
        "name": f"Room {code} · {nickname}",
        "created_at": utc_now_iso(),
        "owner": player_id,
        "room_code": code,
        "nickname": nickname,
        "late": playing,
        "used_words": [first_word],
        "status": "playing" if playing else "waiting",
        "rounds": 0,
        "turn_limit": room["seconds"],
        "win_rounds": room["rounds"],
        "started_at": now if playing else None,
        "turn_started_at": now if playing else None,
        "messages": [start_message(first_word, room["seconds"], room["rounds"])] if playing else [{
            "role": "assistant",
            "content": f"Welcome, {nickname}! You're in room {code}. "
                       "Waiting for the host to start the game...",
        }],
    }
    save_session(data)
    logger.info("Player %s joined room %s as '%s'%s",
                short(player_id), code, nickname, " (late)" if playing else "")
    return data


# 开始比赛：只有房主能调用
@app.post("/api/rooms/{code}/start", response_model=RoomInfo)
def start_room(code: str, player_id: str = Depends(get_player_id)):
    room = load_room(code)
    require_host(room, player_id)
    if room["status"] == "waiting":
        room["status"] = "playing"
        room["started_at"] = time.time()  # 所有人的第一回合都从这一刻开始
        save_room(room)
        logger.info("Room %s started", room["code"])
    return room_info(room, player_id)


# 结束比赛：只有房主能调用，之后不能再加入
@app.post("/api/rooms/{code}/end", response_model=RoomInfo)
def end_room(code: str, player_id: str = Depends(get_player_id)):
    room = load_room(code)
    require_host(room, player_id)
    if room["status"] != "finished":
        room["status"] = "finished"
        room["ended_at"] = time.time()
        save_room(room)
        logger.info("Room %s ended", room["code"])
    return room_info(room, player_id)


# 排行榜：不需要玩家 ID，投到大屏幕上的浏览器也能看
@app.get("/api/rooms/{code}/leaderboard", response_model=Leaderboard)
def leaderboard(code: str):
    room = load_room(code)
    now = time.time()
    rows = []
    for g in list_room_games(room["code"]):
        status = g["status"]
        started = g["started_at"]
        # 房间已经开始，但这个人还没刷新到：当作从房间开始时算起
        if status == "waiting" and room["status"] != "waiting":
            status, started = "playing", room.get("started_at")
        if started is None:
            seconds_used = None
        elif g["finished_at"]:
            seconds_used = g["finished_at"] - started
        else:
            seconds_used = now - started
        rows.append({"nickname": g["nickname"], "status": status, "rounds": g["rounds"],
                     "late": g["late"], "seconds_used": seconds_used})

    # 排名：赢了的在前 → 轮数多的在前 → 用时少的在前
    rows.sort(key=lambda r: (r["status"] != "won", -r["rounds"],
                             r["seconds_used"] if r["seconds_used"] is not None else float("inf")))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    return {"code": room["code"], "status": room["status"], "win_rounds": room["rounds"], "rows": rows}


# Lambda 的入口：Mangum 把 Lambda 的请求翻译成 FastAPI 能懂的格式
handler = Mangum(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)