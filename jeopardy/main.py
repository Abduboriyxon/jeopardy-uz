import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from game import manager, GameRoom

app = FastAPI(title="Jeopardy Uzbek")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOADS_DIR = STATIC_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ALLOWED_EXTENSIONS = {
    "image": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"],
    "video": [".mp4", ".webm", ".ogg"],
    "audio": [".mp3", ".ogg", ".wav", ".m4a"],
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def get_media_type(filename: str) -> Optional[str]:
    ext = Path(filename).suffix.lower()
    for mtype, exts in ALLOWED_EXTENSIONS.items():
        if ext in exts:
            return mtype
    return None


@app.get("/")
async def lobby():
    return FileResponse(str(TEMPLATES_DIR / "index.html"))


@app.get("/host")
async def host_page():
    return FileResponse(str(TEMPLATES_DIR / "host.html"))


@app.get("/play")
async def player_page():
    return FileResponse(str(TEMPLATES_DIR / "player.html"))


@app.post("/api/upload-media")
async def upload_media(file: UploadFile = File(...)):
    media_type = get_media_type(file.filename or "")
    if not media_type:
        raise HTTPException(400, "Fayl turi qo'llab-quvvatlanmaydi")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "Fayl hajmi 50MB dan oshmasligi kerak")
    ext = Path(file.filename).suffix.lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOADS_DIR / unique_name
    async with aiofiles.open(str(file_path), "wb") as f:
        await f.write(content)
    return {
        "url": f"/static/uploads/{unique_name}",
        "media_type": media_type,
        "filename": file.filename
    }


@app.post("/api/create-room")
async def create_room():
    code = manager.create_room()
    return {"room_code": code}


@app.get("/api/rooms")
async def list_rooms():
    return {
        "rooms": [
            {
                "code": code,
                "state": room.game_state,
                "players": len(room.players),
                "created_at": room.created_at
            }
            for code, room in manager.rooms.items()
        ]
    }


async def send_error(ws: WebSocket, code: str, message: str):
    try:
        await ws.send_text(json.dumps({
            "type": "error",
            "data": {"code": code, "message": message},
            "timestamp": time.time()
        }))
    except Exception:
        pass


@app.websocket("/ws/{room_code}/{username}")
async def websocket_endpoint(ws: WebSocket, room_code: str, username: str):
    await ws.accept()
    room_code = room_code.upper()
    room: Optional[GameRoom] = manager.get_room(room_code)
    role = None
    is_rejoining = False

    try:
        # Wait for join message
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)

        if msg.get("type") != "join":
            await send_error(ws, "PROTOCOL_ERROR", "Birinchi 'join' xabarini yuboring")
            await ws.close()
            return

        data = msg.get("data", {})
        role = data.get("role", "player")

        # Validate username
        if not username or len(username) > 20 or not username.replace("_", "").replace("-", "").isalnum():
            await send_error(ws, "INVALID_USERNAME", "Foydalanuvchi nomi 1-20 harf/raqamdan iborat bo'lishi kerak")
            await ws.close()
            return

        if role == "host":
            # Host creates or reclaims room
            if not room:
                await send_error(ws, "ROOM_NOT_FOUND", "Xona topilmadi")
                await ws.close()
                return
            if room.host_ws is not None:
                await send_error(ws, "HOST_TAKEN", "Bu xonada allaqachon olib boruvchi bor")
                await ws.close()
                return
            room.host_ws = ws
            room.host_username = username

        elif role in ("player", "spectator"):
            if not room:
                await send_error(ws, "ROOM_NOT_FOUND", "Xona topilmadi")
                await ws.close()
                return
            if room.game_state == "ended":
                await send_error(ws, "GAME_ENDED", "O'yin tugagan")
                await ws.close()
                return

            if role == "player":
                # Check reconnect
                disc = room.disconnected_players.get(username)
                if disc and disc["expires_at"] > time.time():
                    # Rejoin
                    is_rejoining = True
                    room.players[username] = {
                        "ws": ws,
                        "score": disc["score"],
                        "correct": disc["correct"],
                        "wrong": disc["wrong"],
                        "is_connected": True
                    }
                    del room.disconnected_players[username]
                elif username in room.players:
                    await send_error(ws, "USERNAME_TAKEN", "Bu foydalanuvchi nomi band")
                    await ws.close()
                    return
                elif len(room.players) >= 100:
                    await send_error(ws, "ROOM_FULL", "Xona to'lgan (max 100 o'yinchi)")
                    await ws.close()
                    return
                else:
                    room.players[username] = {
                        "ws": ws, "score": 0, "correct": 0, "wrong": 0, "is_connected": True
                    }
            else:
                room.spectators.append(ws)

        # Send welcome with full state
        await manager.send_to_ws(ws, {
            "type": "welcome",
            "data": {**room.get_full_state(), "your_role": role, "your_username": username, "is_rejoining": is_rejoining}
        })

        # Notify others of new player
        if role == "player" and not is_rejoining:
            await manager.broadcast(room, {
                "type": "player_joined",
                "data": {"username": username, "score": 0}
            })
        elif role == "player" and is_rejoining:
            await manager.broadcast(room, {
                "type": "player_rejoined",
                "data": {"username": username, "score": room.players[username]["score"]}
            })

        # Message loop
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_error(ws, "INVALID_JSON", "JSON formati noto'g'ri")
                continue

            mtype = msg.get("type")
            mdata = msg.get("data", {})

            # ---- HOST ACTIONS ----
            if mtype == "set_questions":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi savol o'rnata oladi")
                    continue
                categories = mdata.get("categories", [])
                if not categories:
                    await send_error(ws, "INVALID_DATA", "Kategoriyalar bo'sh")
                    continue
                room.categories = categories
                room.used_questions = set()
                await manager.broadcast(room, {
                    "type": "questions_updated",
                    "data": {"board": room.get_board_state(), "categories": [c["name"] for c in room.categories]}
                })

            elif mtype == "start_game":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi o'yinni boshlaydi")
                    continue
                if not room.categories:
                    await send_error(ws, "NO_QUESTIONS", "Avval savollarni kiriting")
                    continue
                if not room.players:
                    await send_error(ws, "NO_PLAYERS", "Kamida 1 o'yinchi kerak")
                    continue
                room.game_state = "active"
                room.started_at = time.time()
                await manager.broadcast(room, {
                    "type": "game_started",
                    "data": room.get_full_state()
                })

            elif mtype == "open_question":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi savol ochadi")
                    continue
                if room.game_state != "active":
                    await send_error(ws, "GAME_NOT_ACTIVE", "O'yin faol emas")
                    continue
                cat = mdata.get("category")
                pts = mdata.get("points")
                if (cat, pts) in room.used_questions:
                    await send_error(ws, "QUESTION_ALREADY_USED", "Bu savol allaqachon ishlatilgan")
                    continue
                q = room.find_question(cat, pts)
                if not q:
                    await send_error(ws, "QUESTION_NOT_FOUND", "Savol topilmadi")
                    continue
                # Cancel existing timers
                for t in [room.question_timer_task, room.answer_timer_task]:
                    if t:
                        t.cancel()
                room.current_question = {
                    "category": cat,
                    "points": pts,
                    "question": q["question"],
                    "answer": q["answer"],
                    "hint": q.get("hint"),
                    "media": q.get("media"),
                    "timer_seconds": 30
                }
                room.buzzer_open = True
                room.buzzer_winner = None
                room.locked_out_players = set()
                timer_secs = 30
                await manager.broadcast(room, {
                    "type": "question_opened",
                    "data": {
                        "category": cat,
                        "points": pts,
                        "question": q["question"],
                        "hint": q.get("hint"),
                        "media": q.get("media"),
                        "timer_seconds": timer_secs
                    }
                })
                room.question_timer_task = asyncio.create_task(
                    manager.start_question_timer(room, timer_secs)
                )

            elif mtype == "judge":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi baholaydi")
                    continue
                if not room.current_question:
                    await send_error(ws, "NO_ACTIVE_QUESTION", "Faol savol yo'q")
                    continue
                if not room.buzzer_winner:
                    await send_error(ws, "NO_BUZZER_WINNER", "Hech kim buzzer boselmagan")
                    continue
                correct = mdata.get("correct", False)
                winner = room.buzzer_winner
                pts = room.current_question["points"]
                cat = room.current_question["category"]
                answer = room.current_question.get("answer", "")

                # Cancel answer timer
                if room.answer_timer_task:
                    room.answer_timer_task.cancel()
                    room.answer_timer_task = None

                if correct:
                    if winner in room.players:
                        room.players[winner]["score"] += pts
                        room.players[winner]["correct"] = room.players[winner].get("correct", 0) + 1
                    await manager.broadcast(room, {
                        "type": "score_update",
                        "data": {"username": winner, "score": room.players[winner]["score"], "delta": pts}
                    })
                    # Close question
                    if room.question_timer_task:
                        room.question_timer_task.cancel()
                    room.used_questions.add((cat, pts))
                    room.current_question = None
                    room.buzzer_open = False
                    room.buzzer_winner = None
                    room.locked_out_players = set()
                    await manager.broadcast(room, {
                        "type": "question_closed",
                        "data": {"category": cat, "points": pts, "answer": answer, "status": "answered", "answered_by": winner}
                    })
                    if room.all_questions_used():
                        await manager._end_game(room)
                else:
                    if winner in room.players:
                        room.players[winner]["score"] -= pts
                        room.players[winner]["wrong"] = room.players[winner].get("wrong", 0) + 1
                    await manager.broadcast(room, {
                        "type": "score_update",
                        "data": {"username": winner, "score": room.players[winner]["score"], "delta": -pts}
                    })
                    room.locked_out_players.add(winner)
                    room.buzzer_winner = None
                    active_players = [u for u in room.players if u not in room.locked_out_players]
                    if not active_players:
                        if room.question_timer_task:
                            room.question_timer_task.cancel()
                        await manager._handle_question_expired(room)
                    else:
                        room.buzzer_open = True
                        await manager.broadcast(room, {
                            "type": "buzzer_reopened",
                            "data": {
                                "wrong_player": winner,
                                "locked_out": list(room.locked_out_players),
                                "delta": -pts
                            }
                        })
                        # Resume question timer
                        if room.question_timer_task:
                            room.question_timer_task.cancel()
                        room.question_timer_task = asyncio.create_task(
                            manager.start_question_timer(room, room.timer_seconds_remaining)
                        )

            elif mtype == "skip_question":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi o'tkazib yuboradi")
                    continue
                if not room.current_question:
                    await send_error(ws, "NO_ACTIVE_QUESTION", "Faol savol yo'q")
                    continue
                for t in [room.question_timer_task, room.answer_timer_task]:
                    if t:
                        t.cancel()
                cat = room.current_question["category"]
                pts = room.current_question["points"]
                answer = room.current_question.get("answer", "")
                room.used_questions.add((cat, pts))
                room.current_question = None
                room.buzzer_open = False
                room.buzzer_winner = None
                room.locked_out_players = set()
                await manager.broadcast(room, {
                    "type": "question_closed",
                    "data": {"category": cat, "points": pts, "answer": answer, "status": "skipped"}
                })
                if room.all_questions_used():
                    await manager._end_game(room)

            elif mtype == "adjust_score":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi ball o'zgartiradi")
                    continue
                target = mdata.get("username")
                delta = mdata.get("delta", 0)
                if target not in room.players:
                    await send_error(ws, "PLAYER_NOT_FOUND", "O'yinchi topilmadi")
                    continue
                room.players[target]["score"] += delta
                await manager.broadcast(room, {
                    "type": "score_update",
                    "data": {"username": target, "score": room.players[target]["score"], "delta": delta}
                })

            elif mtype == "kick_player":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "Faqat olib boruvchi o'yinchi chiqaradi")
                    continue
                target = mdata.get("username")
                if target in room.players:
                    kicked_ws = room.players[target].get("ws")
                    del room.players[target]
                    await manager.broadcast(room, {
                        "type": "player_left",
                        "data": {"username": target, "reason": "kicked"}
                    })
                    if kicked_ws:
                        try:
                            await kicked_ws.send_text(json.dumps({"type": "kicked", "data": {}}))
                            await kicked_ws.close()
                        except Exception:
                            pass

            elif mtype == "end_game":
                if role != "host":
                    await send_error(ws, "NOT_HOST", "")
                    continue
                for t in [room.question_timer_task, room.answer_timer_task]:
                    if t:
                        t.cancel()
                await manager._end_game(room)

            # ---- PLAYER ACTIONS ----
            elif mtype == "buzz":
                if role != "player":
                    await send_error(ws, "NOT_PLAYER", "Faqat o'yinchilar buzzer bosa oladi")
                    continue
                if not room.buzzer_open:
                    await send_error(ws, "BUZZER_CLOSED", "Hozir buzzer bosib bo'lmaydi")
                    continue
                if username in room.locked_out_players:
                    await send_error(ws, "ALREADY_LOCKED_OUT", "Siz bu savolda bloklandingiz")
                    continue
                if room.game_state != "active":
                    await send_error(ws, "GAME_NOT_ACTIVE", "O'yin faol emas")
                    continue

                # Lock buzzer
                room.buzzer_open = False
                room.buzzer_winner = username

                # Cancel question timer, start 10s answer timer
                if room.question_timer_task:
                    room.question_timer_task.cancel()
                    room.question_timer_task = None

                await manager.broadcast(room, {
                    "type": "buzzer_locked",
                    "data": {
                        "winner": username,
                        "time_remaining": room.timer_seconds_remaining
                    }
                })
                room.answer_timer_task = asyncio.create_task(
                    manager.start_answer_timer(room, 10)
                )

            # ---- COMMON ----
            elif mtype == "chat":
                text = str(mdata.get("text", ""))[:300]
                if text.strip():
                    await manager.broadcast(room, {
                        "type": "chat",
                        "data": {"username": username, "text": text, "role": role}
                    })

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        await send_error(ws, "TIMEOUT", "Ulanish vaqti tugadi")
    except Exception as e:
        print(f"WS error: {e}")
    finally:
        if room:
            if role == "host" and room.host_ws == ws:
                room.host_ws = None
                await manager.broadcast(room, {"type": "host_left", "data": {}})
            elif role == "player" and username in room.players:
                pdata = room.players[username]
                room.disconnected_players[username] = {
                    "score": pdata["score"],
                    "correct": pdata.get("correct", 0),
                    "wrong": pdata.get("wrong", 0),
                    "expires_at": time.time() + 60
                }
                del room.players[username]
                await manager.broadcast(room, {
                    "type": "player_left",
                    "data": {"username": username, "reason": "disconnected"}
                })
            elif role == "spectator":
                if ws in room.spectators:
                    room.spectators.remove(ws)
        try:
            await ws.close()
        except Exception:
            pass


PRESETS_FILE = BASE_DIR / "presets.json"

def load_presets():
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Migratsiya: Eski list formatini yangi dict formatiga o'tkazish
                migrated = False
                for host in list(data.keys()):
                    if isinstance(data[host], list):
                        data[host] = {
                            "password": "123", # Eski xisoblar uchun vaqtinchalik parol
                            "packs": [{"name": "Eski Savollar", "categories": data[host]}]
                        }
                        migrated = True
                if migrated:
                    save_presets(data)
                return data
        except Exception as e:
            print(f"Preset yuklashda xato: {e}")
            return {}
    return {}

def save_presets(presets):
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False)

from fastapi import Request

@app.post("/api/save-presets")
async def save_host_presets(request: Request):
    data = await request.json()
    host = data.get("host")
    password = data.get("password")
    pack_name = data.get("pack_name", "Standart")
    categories = data.get("categories", [])
    
    if not host or not password:
        raise HTTPException(400, "Host nomi va paroli majburiy")

    presets = load_presets()
    if host in presets:
        if presets[host]["password"] != password:
            raise HTTPException(403, "Noto'g'ri parol. Bu host nomi band.")
    else:
        presets[host] = {"password": password, "packs": []}

    # Update or add pack
    packs = presets[host]["packs"]
    existing = next((p for p in packs if p["name"] == pack_name), None)
    if existing:
        existing["categories"] = categories
    else:
        packs.append({"name": pack_name, "categories": categories})

    save_presets(presets)
    return {"status": "ok", "message": f"'{pack_name}' to'plami saqlandi"}

@app.post("/api/list-packs")
async def list_host_packs(request: Request):
    data = await request.json()
    host = data.get("host")
    password = data.get("password")
    
    if not host: return {"packs": []}
    
    presets = load_presets()
    if host not in presets:
        return {"packs": []}
    
    if presets[host]["password"] != password:
        raise HTTPException(403, "Noto'g'ri parol")
        
    return {"packs": [p["name"] for p in presets[host]["packs"]]}

@app.post("/api/load-presets")
async def load_host_presets(request: Request):
    data = await request.json()
    host = data.get("host")
    password = data.get("password")
    pack_name = data.get("pack_name")
    
    if not host or not password:
        raise HTTPException(400, "Host va parol kerak")
        
    presets = load_presets()
    if host not in presets:
        raise HTTPException(404, "Host topilmadi")
        
    if presets[host]["password"] != password:
        raise HTTPException(403, "Noto'g'ri parol")
        
    packs = presets[host]["packs"]
    if not packs:
        return {"categories": []}
        
    if not pack_name:
        # Load the latest or first pack if name not specified
        return {"categories": packs[-1]["categories"], "pack_name": packs[-1]["name"]}
        
    target = next((p for p in packs if p["name"] == pack_name), None)
    if not target:
        raise HTTPException(404, "To'plam topilmadi")
        
    return {"categories": target["categories"], "pack_name": target["name"]}


@app.on_event("startup")
async def startup():
    asyncio.create_task(periodic_cleanup())


async def periodic_cleanup():
    while True:
        await asyncio.sleep(300)
        await manager.cleanup_empty_rooms()
