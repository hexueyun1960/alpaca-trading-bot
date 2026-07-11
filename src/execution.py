from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from src.config import Settings


ExecutionComponent = Literal["rest", "stream"]


class FileLeaderLock:
    def __init__(self, path: str):
        self.path = Path(path)
        self.handle = None
        self.acquired_at: datetime | None = None
        self._windows_lock_dir = self.path.with_name(self.path.name + ".lockdir")

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            try:
                self._windows_lock_dir.mkdir()
            except FileExistsError:
                return False
            self.acquired_at = datetime.now(timezone.utc)
            self.path.write_text(
                f"pid={os.getpid()} acquired_at={self.acquired_at.isoformat()}\n",
                encoding="utf-8",
            )
            return True

        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False

        self.acquired_at = datetime.now(timezone.utc)
        try:
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(f"pid={os.getpid()} acquired_at={self.acquired_at.isoformat()}\n")
            self.handle.flush()
        except OSError:
            self.handle.close()
            self.handle = None
            self.acquired_at = None
            return False
        return True

    def release(self) -> None:
        if os.name == "nt":
            try:
                self._windows_lock_dir.rmdir()
            except OSError:
                pass
            self.handle = None
            return
        if self.handle is None:
            return
        try:
            try:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> FileLeaderLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


@dataclass
class ExecutionContext:
    settings: Settings
    component: ExecutionComponent
    leader_lock: FileLeaderLock | None = None
    leader_lock_acquired: bool = False
    leader_lock_acquired_at: datetime | None = None
    leader_lock_lost_at: datetime | None = None

    @property
    def requested_mode(self) -> str:
        return self.settings.execution_mode

    @property
    def effective_mode(self) -> str:
        if self.settings.execution_mode == "shadow":
            return "shadow"
        if self.settings.require_leader_lock and not self.leader_lock_acquired:
            return "shadow"
        if self.settings.execution_mode != self.component:
            return "shadow"
        return self.settings.execution_mode

    @property
    def is_execution_leader(self) -> bool:
        return not self.settings.require_leader_lock or self.leader_lock_acquired

    @property
    def can_submit_orders(self) -> bool:
        return (
            self.settings.can_submit_orders
            and self.effective_mode == self.component
            and self.is_execution_leader
        )

    @property
    def can_open_new_positions(self) -> bool:
        return self.can_submit_orders

    @property
    def allow_risk_exits(self) -> bool:
        return self.settings.can_submit_orders and self.is_execution_leader

    def mark_lost(self) -> None:
        self.leader_lock_acquired = False
        self.leader_lock_lost_at = datetime.now(timezone.utc)

    def payload(self) -> dict:
        return {
            "instance_id": self.settings.instance_id,
            "component": self.component,
            "execution_mode": self.settings.execution_mode,
            "effective_mode": self.effective_mode,
            "leader_lock_required": self.settings.require_leader_lock,
            "leader_lock_path": self.settings.leader_lock_path,
            "leader_lock_status": "acquired" if self.leader_lock_acquired else "not_acquired",
            "leader_lock_acquired_at": self.leader_lock_acquired_at.isoformat()
            if self.leader_lock_acquired_at
            else None,
            "leader_lock_lost_at": self.leader_lock_lost_at.isoformat()
            if self.leader_lock_lost_at
            else None,
        }

    def release(self) -> None:
        if self.leader_lock:
            self.leader_lock.release()
        self.mark_lost()


def acquire_execution_context(settings: Settings, component: ExecutionComponent) -> ExecutionContext:
    context = ExecutionContext(settings=settings, component=component)
    if settings.execution_mode == "shadow":
        context.leader_lock_acquired = False
        return context
    if not settings.require_leader_lock:
        context.leader_lock_acquired = True
        context.leader_lock_acquired_at = datetime.now(timezone.utc)
        return context

    lock = FileLeaderLock(settings.leader_lock_path)
    acquired = lock.acquire()
    context.leader_lock = lock if acquired else None
    context.leader_lock_acquired = acquired
    context.leader_lock_acquired_at = lock.acquired_at if acquired else None
    return context
