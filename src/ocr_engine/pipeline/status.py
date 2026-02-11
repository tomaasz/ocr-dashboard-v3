from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class LastError:
    type: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "message": self.message}


def write_status(
    job_dir: Path,
    *,
    technical_state: str,
    engine_name: str,
    results_count: int | None = None,
    last_error: LastError | None = None,
) -> Path:
    """
    Zapisuje status techniczny do jobs/<id>/ocr/status.json (placeholder pod przyszły DB).
    """
    ocr_dir = job_dir / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "technical_state": technical_state,
        "updated_at": _utc_now_iso(),
        "engine": {"name": engine_name},
    }

    if results_count is not None:
        payload["results_count"] = results_count

    if last_error is not None:
        payload["last_error"] = last_error.to_dict()

    path = ocr_dir / "status.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
