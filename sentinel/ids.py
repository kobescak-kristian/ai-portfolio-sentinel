"""Clock / id-factory / sleeper seams.

Production implementations use real time, real randomness and real
sleeps. Tests inject frozen/seeded/no-op doubles so the rest of the
control plane never calls a wall-clock or randomness primitive
directly — every timestamp and identifier in a test run is
reproducible.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdFactory(Protocol):
    def new_run_id(self) -> str: ...

    def new_task_id(self) -> str: ...


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real UTC wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class RandomIdFactory:
    """Real, unique run/task identifiers."""

    def new_run_id(self) -> str:
        return f"r-{uuid.uuid4().hex}"

    def new_task_id(self) -> str:
        return f"t-{uuid.uuid4().hex}"


class SystemSleeper:
    """Real sleeping — used only by production retry backoff."""

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


@dataclass
class FrozenClock:
    """Deterministic clock for tests: returns a scripted sequence of
    timestamps, repeating the last one once the sequence is exhausted
    so a test doesn't need to size the script exactly."""

    ticks: list[datetime]
    _index: int = field(default=0, init=False)

    def now(self) -> datetime:
        if self._index < len(self.ticks):
            value = self.ticks[self._index]
            self._index += 1
        else:
            value = self.ticks[-1]
        return value


@dataclass
class SeededIdFactory:
    """Deterministic run/task ids for tests: a fixed run id plus a
    counter-based task id sequence, so assertions can name exact ids."""

    run_ids: list[str] = field(default_factory=lambda: ["run-001"])
    task_prefix: str = "task"
    _run_index: int = field(default=0, init=False)
    _task_counter: int = field(default=0, init=False)

    def new_run_id(self) -> str:
        if self._run_index < len(self.run_ids):
            value = self.run_ids[self._run_index]
            self._run_index += 1
        else:
            value = f"{self.run_ids[-1]}-{self._run_index}"
            self._run_index += 1
        return value

    def new_task_id(self) -> str:
        self._task_counter += 1
        return f"{self.task_prefix}-{self._task_counter:04d}"


class NoOpSleeper:
    """Instant "sleep" for tests — retry/backoff logic runs at full speed."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
