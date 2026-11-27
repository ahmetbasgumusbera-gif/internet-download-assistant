"""
Eklentiden gelen indirme istekleri için "bilet" kaydı (Qt yok, iş parçacığı güvenli).

Eklenti bir indirmeyi uygulamaya verince bilet alır ve durumunu sorgular. Uygulamadaki indirme sunucu engeli (401/403/429)
yüzünden başarısız olursa eklenti aynı adresi tarayıcıya indirtir ve uygulamaya "devraldım" der.
"""
from __future__ import annotations

import re
import threading
import time
import uuid

from core.models import TaskStatus

TTL = 6 * 3600
_HELPFUL = re.compile(r"HTTP (401|403|405|406|429|5\d\d)", re.IGNORECASE)

_STATE_BY_STATUS = {
    TaskStatus.COMPLETED: "done",
    TaskStatus.ERROR: "failed",
    TaskStatus.CANCELLED: "cancelled",
    TaskStatus.PAUSED: "paused",
}


class Handoff:
    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}

    def new_ticket(self) -> str:
        ticket = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            for key in [k for k, v in self._items.items() if now - v["at"] > TTL]:
                del self._items[key]
            self._items[ticket] = {"state": "pending", "task": "", "error": "", "at": now}
        return ticket

    def attach(self, ticket: str, task_id: str) -> None:
        with self._lock:
            item = self._items.get(ticket)
            if item is not None:
                item["task"] = task_id
                item["state"] = "running"

    def finish(self, ticket: str, state: str, error: str = "") -> None:
        with self._lock:
            item = self._items.get(ticket)
            if item is not None:
                item["state"], item["error"] = state, error

    def on_task(self, task) -> None:
        state = _STATE_BY_STATUS.get(task.status, "running")
        with self._lock:
            for item in self._items.values():
                if item["task"] == task.id:
                    item["state"] = state
                    item["error"] = task.error if state == "failed" else ""

    def on_removed(self, task_id: str) -> None:
        with self._lock:
            for item in self._items.values():
                if item["task"] == task_id:
                    item["state"] = "gone"

    def task_of(self, ticket: str) -> str:
        with self._lock:
            item = self._items.get(ticket)
            return item["task"] if item else ""

    def state(self, ticket: str) -> dict:
        with self._lock:
            item = self._items.get(ticket)
            if item is None:
                return {"state": "unknown", "error": "", "browserHelpful": False}
            helpful = item["state"] == "failed" and bool(_HELPFUL.search(item["error"] or ""))
            return {"state": item["state"], "error": item["error"], "browserHelpful": helpful}


HANDOFF = Handoff()
