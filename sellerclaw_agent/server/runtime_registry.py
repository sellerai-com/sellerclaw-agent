from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID

_registry: EdgeRuntimeRegistry | None = None


def install_runtime_registry(registry: EdgeRuntimeRegistry | None) -> None:
    global _registry  # noqa: PLW0603
    _registry = registry


def get_runtime_registry() -> EdgeRuntimeRegistry | None:
    return _registry


@dataclass
class TaskRuntimeState:
    alive: bool = True
    last_success_at: datetime | None = None
    consecutive_errors: int = 0
    last_error: str | None = None
    restart_count: int = 0
    current_command_id: str | None = None
    sse_connected: bool = False


@dataclass
class EdgeRuntimeRegistry:
    """Thread-safe snapshot of edge background task health (ping, executor, SSE)."""

    _lock: Lock = field(default_factory=Lock, repr=False)
    _tasks: dict[str, TaskRuntimeState] = field(
        default_factory=lambda: {
            "ping_loop": TaskRuntimeState(),
            "command_executor": TaskRuntimeState(),
            "chat_sse": TaskRuntimeState(),
        },
    )
    _last_dispatched_command_id: UUID | None = field(default=None, repr=False)

    def mark_task_alive(self, name: str, *, alive: bool) -> None:
        with self._lock:
            self._tasks[name].alive = alive

    def mark_ping_success(self) -> None:
        with self._lock:
            s = self._tasks["ping_loop"]
            s.last_success_at = datetime.now(tz=UTC)
            s.consecutive_errors = 0
            s.last_error = None
            s.alive = True

    def mark_ping_error(self, message: str) -> None:
        with self._lock:
            s = self._tasks["ping_loop"]
            s.consecutive_errors += 1
            s.last_error = message[:500]
            s.alive = True

    def mark_executor_command(self, *, command_id: UUID | None) -> None:
        with self._lock:
            self._tasks["command_executor"].current_command_id = (
                str(command_id) if command_id is not None else None
            )

    def mark_sse_connected(self, connected: bool) -> None:
        with self._lock:
            self._tasks["chat_sse"].sse_connected = connected

    def get_last_dispatched_command_id(self) -> UUID | None:
        with self._lock:
            return self._last_dispatched_command_id

    def set_last_dispatched_command_id(self, command_id: UUID | None) -> None:
        with self._lock:
            self._last_dispatched_command_id = command_id

    def increment_restart(self, name: str) -> None:
        with self._lock:
            self._tasks[name].restart_count += 1

    def snapshot_tasks(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            out: dict[str, dict[str, Any]] = {}
            for key, st in self._tasks.items():
                out[key] = {
                    "alive": st.alive,
                    "last_success_at": st.last_success_at.isoformat() if st.last_success_at else None,
                    "consecutive_errors": st.consecutive_errors,
                    "last_error": st.last_error,
                    "restart_count": st.restart_count,
                    "current_command_id": st.current_command_id,
                    "connected": st.sse_connected if key == "chat_sse" else None,
                }
            return out
