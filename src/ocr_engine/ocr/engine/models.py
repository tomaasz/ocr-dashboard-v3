from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OcrStage(str, Enum):
    """
    Etapy OCR zgodnie z kanonem: Two-Step OCR.
    Stage3 (post-processing) istnieje, ale nie nadpisuje raw OCR.
    """

    STAGE1_RAW_AND_CLASSIFY = "stage1_raw_and_classify"
    STAGE2_STRUCTURED_EXTRACTION = "stage2_structured_extraction"
    STAGE3_POSTPROCESS = "stage3_postprocess"


@dataclass(frozen=True)
class EngineCaps:
    """
    Możliwości silnika – przyda się UI/pipeline do walidacji i testów.
    """

    supports_stage1: bool = True
    supports_stage2: bool = True
    supports_stage3: bool = False
    supports_chat_rotation: bool = True
    supports_upload_watchdog: bool = True


@dataclass(frozen=True)
class EngineConfig:
    """
    Konfiguracja techniczna silnika.
    UWAGA: brak sekretów. Sekrety/credentiale mają żyć poza UI.
    """

    # ścieżka robocza na artefakty runtime (np. screenshots, html dumpy)
    runtime_dir: Path | None = None

    # limity stabilności/rotacji sesji (to są defaulty, logika po stronie impl.)
    rotate_chat_every_n_docs: int = 8
    rotate_on_timeout: bool = True

    # timeouts (sekundy) – wartości docelowo będą wczytywane z configu runtime
    upload_timeout_s: int = 30
    dom_signal_timeout_s: int = 30


@dataclass
class OcrError(Exception):
    """
    Jawny błąd silnika OCR. W pipeline powinien zostać złapany i zapisany do DB/logów,
    a w job dir powinien powstać artefakt diagnostyczny.
    """

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class OcrResult:
    """
    Minimalny rezultat z jednego entry (e1/e2/...) albo z całej strony.
    Na razie trzymamy to bardzo neutralnie.
    """

    entry_id: str  # "e1", "e2", ...
    stage: OcrStage
    parse_ok: bool  # zgodnie z kanonem: fallback gdy JSON się sypie -> False
    raw_response_text: str  # zawsze przechowujemy tekst (nawet przy parse_ok=True)
    data: dict[str, Any] = field(default_factory=dict)  # structured output (jeśli parse_ok=True)
    artifacts: dict[str, str] = field(default_factory=dict)  # map: logical_name -> relative path

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "stage": self.stage.value,
            "parse_ok": self.parse_ok,
            "raw_response_text": self.raw_response_text,
            "data": self.data,
            "artifacts": self.artifacts,
        }
