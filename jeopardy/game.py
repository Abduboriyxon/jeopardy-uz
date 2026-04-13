import asyncio
import json
import random
import string
import time
from typing import Optional
from fastapi import WebSocket


def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))


class GameRoom:
    def __init__(self, room_code: str):
        self.room_code = room_code
        self.host_ws: Optional[WebSocket] = None
        self.host_username: Optional[str] = None
        self.players: dict = {}
        self.spectators: list = []
        self.categories: list = []
        self.used_questions: set = set()
        self.game_state: str = "waiting"
        self.current_question: Optional[dict] = None
        self.buzzer_open: bool = False
        self.buzzer_winner: Optional[str] = None
        self.locked_out_players: set = set()
        self.answer_timer_task: Optional[asyncio.Task] = None
        self.question_timer_task: Optional[asyncio.Task] = None
        self.timer_seconds_remaining: int = 0
        self.answer_timer_remaining: int = 0
        self.created_at: float = time.time()
        self.started_at: Optional[float] = None
        self.ended_at: Optional[float] = None
        self.disconnected_players: dict = {}

    def get_board_state(self):
        board = {}
        for cat in self.categories:
            board[cat["name"]] = []
            for q in cat["questions"]:
                board[cat["name"]].append({
                    "points": q["points"],
                    "status": "used" if (cat["name"], q["points"]) in self.used_questions else "available",
                    "has_media": bool(q.get("media"))
                })
        return board

    def get_full_state(self):
        return {
            "room_code": self.room_code,
            "game_state": self.game_state,
            "players": [
                {
                    "username": u,
                    "score": d["score"],
                    "correct": d.get("correct", 0),
                    "wrong": d.get("wrong", 0),
                    "is_connected": d.get("is_connected", True)
                }
                for u, d in self.players.items()
            ],
            "board": self.get_board_state(),
            "categories": [c["name"] for c in self.categories],
            "current_question": self.current_question,
            "buzzer_open": self.buzzer_open,
            "buzzer_winner": self.buzzer_winner,
            "timer_seconds_remaining": self.timer_seconds_remaining,
            "answer_timer_remaining": self.answer_timer_remaining,
            "used_questions": [[k[0], k[1]] for k in self.used_questions],
        }

    def find_question(self, category: str, points: int):
        for cat in self.categories:
            if cat["name"] == category:
                for q in cat["questions"]:
                    if q["points"] == points:
                        return q
        return None

    def all_questions_used(self):
        total = sum(len(c["questions"]) for c in self.categories)
        return len(self.used_questions) >= total


class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, GameRoom] = {}

    def create_room(self) -> str:
        for _ in range(20):
            code = generate_room_code()
            if code not in self.rooms:
                self.rooms[code] = GameRoom(code)
                return code
        raise Exception("Could not generate unique room code")

    def get_room(self, room_code: str) -> Optional[GameRoom]:
        return self.rooms.get(room_code.upper())

    async def broadcast(self, room: GameRoom, message: dict):
        msg_str = json.dumps({**message, "timestamp": time.time()})
        if room.host_ws:
            try:
                await room.host_ws.send_text(msg_str)
            except Exception:
                pass
        for uname, pdata in list(room.players.items()):
            if pdata.get("ws") and pdata.get("is_connected"):
                try:
                    await pdata["ws"].send_text(msg_str)
                except Exception:
                    pass
        for ws in list(room.spectators):
            try:
                await ws.send_text(msg_str)
            except Exception:
                pass

    async def send_to_ws(self, ws: WebSocket, message: dict):
        try:
            await ws.send_text(json.dumps({**message, "timestamp": time.time()}))
        except Exception:
            pass

    async def send_to_host(self, room: GameRoom, message: dict):
        if room.host_ws:
            await self.send_to_ws(room.host_ws, message)

    async def send_to_player(self, room: GameRoom, username: str, message: dict):
        pdata = room.players.get(username)
        if pdata and pdata.get("ws") and pdata.get("is_connected"):
            await self.send_to_ws(pdata["ws"], message)

    async def start_question_timer(self, room: GameRoom, seconds: int):
        room.timer_seconds_remaining = seconds
        try:
            for remaining in range(seconds, -1, -1):
                room.timer_seconds_remaining = remaining
                await self.broadcast(room, {
                    "type": "timer_tick",
                    "data": {"seconds_remaining": remaining, "phase": "question"}
                })
                if remaining == 0:
                    await self._handle_question_expired(room)
                    return
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def start_answer_timer(self, room: GameRoom, seconds: int = 10):
        room.answer_timer_remaining = seconds
        try:
            for remaining in range(seconds, -1, -1):
                room.answer_timer_remaining = remaining
                await self.broadcast(room, {
                    "type": "timer_tick",
                    "data": {"seconds_remaining": remaining, "phase": "answer"}
                })
                if remaining == 0:
                    await self._handle_answer_timeout(room)
                    return
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _handle_question_expired(self, room: GameRoom):
        if not room.current_question:
            return
        cat = room.current_question["category"]
        pts = room.current_question["points"]
        answer = room.current_question.get("answer", "")
        room.used_questions.add((cat, pts))
        room.current_question = None
        room.buzzer_open = False
        room.buzzer_winner = None
        room.locked_out_players = set()
        await self.broadcast(room, {
            "type": "question_closed",
            "data": {"category": cat, "points": pts, "answer": answer, "status": "expired"}
        })
        if room.all_questions_used():
            await self._end_game(room)

    async def _handle_answer_timeout(self, room: GameRoom):
        if not room.current_question or not room.buzzer_winner:
            return
        winner = room.buzzer_winner
        pts = room.current_question["points"]
        if winner in room.players:
            room.players[winner]["score"] -= pts
            room.players[winner]["wrong"] = room.players[winner].get("wrong", 0) + 1
            await self.broadcast(room, {
                "type": "score_update",
                "data": {"username": winner, "score": room.players[winner]["score"], "delta": -pts}
            })
        room.locked_out_players.add(winner)
        room.buzzer_winner = None
        active_players = [u for u in room.players if u not in room.locked_out_players]
        if not active_players:
            await self._handle_question_expired(room)
        else:
            room.buzzer_open = True
            # Resume question timer
            if room.question_timer_task:
                room.question_timer_task.cancel()
            room.question_timer_task = asyncio.create_task(
                self.start_question_timer(room, room.timer_seconds_remaining)
            )
            await self.broadcast(room, {
                "type": "buzzer_reopened",
                "data": {
                    "timed_out_player": winner,
                    "locked_out": list(room.locked_out_players),
                    "delta": -pts
                }
            })

    async def _end_game(self, room: GameRoom):
        room.game_state = "ended"
        room.ended_at = time.time()
        scores = sorted(
            [{"username": u, "score": d["score"], "correct": d.get("correct", 0), "wrong": d.get("wrong", 0)}
             for u, d in room.players.items()],
            key=lambda x: x["score"], reverse=True
        )
        winner = scores[0]["username"] if scores else None
        await self.broadcast(room, {
            "type": "game_ended",
            "data": {"final_scores": scores, "winner": winner}
        })

    async def cleanup_empty_rooms(self):
        now = time.time()
        to_delete = []
        for code, room in self.rooms.items():
            if room.game_state == "ended" and (now - (room.ended_at or now)) > 3600:
                to_delete.append(code)
            elif not room.host_ws and not room.players and (now - room.created_at) > 300:
                to_delete.append(code)
        for code in to_delete:
            del self.rooms[code]


manager = ConnectionManager()
