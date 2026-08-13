"""The universal response envelope.

Every command computes exactly one domain payload and wraps it here. Both the
human renderer and the JSON serializer read from this single object, which is
what keeps the two modes in semantic parity: there is no second code path that
could drift.

Envelope shape (stable, versioned by :data:`piia.version.ENVELOPE_VERSION`)::

    {
      "$schema": "...", "version": "1.0.0", "command": "analyze",
      "status": "success" | "error" | "partial",
      "timestamp": "2026-08-13T17:28:31Z",
      "execution": {"duration_ms": 142, "request_id": "req_...", "tool_version": "0.1.0"},
      "data": {...} | null,
      "error": {...} | null,
      "warnings": [...],
      "pagination": {...} | null
    }
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from piia.errors import PiiaError
from piia.version import ENVELOPE_VERSION, __version__

SCHEMA_BASE_URL = "https://raw.githubusercontent.com/PIIA-CLI/PIIA-CLI/main/schemas/v1"
ENVELOPE_SCHEMA_URL = f"{SCHEMA_BASE_URL}/cli-envelope.json"

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_PARTIAL = "partial"


def utc_now_iso() -> str:
    """Return an RFC 3339 UTC timestamp with second precision.

    ``PIIA_FROZEN_TIME`` pins the value, which makes golden-file tests and
    byte-reproducible document runs possible.
    """
    frozen = os.environ.get("PIIA_FROZEN_TIME")
    if frozen:
        return frozen
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_request_id() -> str:
    """Return a correlation id, overridable via ``PIIA_REQUEST_ID``."""
    pinned = os.environ.get("PIIA_REQUEST_ID")
    if pinned:
        return pinned
    return f"req_{uuid.uuid4().hex[:16]}"


@dataclass
class Warning_:
    """A non-fatal degradation the caller should know about."""

    code: str
    message: str
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "remediation": self.remediation}


@dataclass
class Envelope:
    """A single command result, renderable in either mode."""

    command: str
    data: dict[str, Any] | None = None
    status: str = STATUS_SUCCESS
    error: dict[str, Any] | None = None
    warnings: list[Warning_] = field(default_factory=list)
    pagination: dict[str, Any] | None = None
    request_id: str = field(default_factory=new_request_id)
    timestamp: str = field(default_factory=utc_now_iso)
    duration_ms: int = 0
    exit_code: int = 0

    # -- construction ------------------------------------------------------
    @classmethod
    def start(cls, command: str) -> Envelope:
        env = cls(command=command)
        env._started = time.perf_counter()
        return env

    _started: float = field(default_factory=time.perf_counter, repr=False)

    def stop(self) -> Envelope:
        self.duration_ms = int((time.perf_counter() - self._started) * 1000)
        return self

    def succeed(self, data: dict[str, Any]) -> Envelope:
        self.data = data
        self.status = STATUS_PARTIAL if self.warnings else STATUS_SUCCESS
        self.exit_code = 0
        return self.stop()

    def fail(self, exc: PiiaError) -> Envelope:
        self.data = None
        self.error = exc.to_dict()
        self.status = STATUS_ERROR
        self.exit_code = exc.exit_code
        return self.stop()

    def warn(self, code: str, message: str, remediation: str | None = None) -> Envelope:
        self.warnings.append(Warning_(code=code, message=message, remediation=remediation))
        return self

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "$schema": ENVELOPE_SCHEMA_URL,
            "version": ENVELOPE_VERSION,
            "command": self.command,
            "status": self.status,
            "timestamp": self.timestamp,
            "execution": {
                "duration_ms": self.duration_ms,
                "request_id": self.request_id,
                "tool_version": __version__,
            },
            "data": self.data,
            "error": self.error,
            "warnings": [w.to_dict() for w in self.warnings],
            "pagination": self.pagination,
        }
