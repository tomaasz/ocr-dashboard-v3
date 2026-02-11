from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import EngineCaps, EngineConfig, OcrResult, OcrStage


@runtime_checkable
class OcrEngine(Protocol):
    """
    Minimalny kontrakt silnika OCR.

    Założenia:
    - Silnik operuje na katalogu job: jobs/<job_id>/ (czyta job.json)
    - Silnik NIE modyfikuje ui.* w job.json (to kanon)
    - Silnik może zapisywać swoje wyniki/artefakty w job dir (np. ocr/)
    - DB/queue to osobna warstwa (engine nie jest "kolejką")
    """

    @property
    def name(self) -> str: ...

    @property
    def caps(self) -> EngineCaps: ...

    def configure(self, config: EngineConfig) -> None:
        """
        Ustawienia runtime. Bez sekretów.
        """
        ...

    def load_job(self, job_dir: Path) -> dict:
        """
        Wczytuje i waliduje minimalnie job.json (tylko sanity-check).
        Nie interpretuje semantycznie pól UI – to robi UI.
        """
        ...

    def ensure_job_layout(self, job_dir: Path) -> None:
        """
        Zapewnia minimalne katalogi na wyniki (np. jobs/<id>/ocr/).
        Nie usuwa niczego.
        """
        ...

    def iter_entry_ids(self, job: dict) -> Iterable[str]:
        """
        Deterministyczna kolejność entry_id: e1, e2, ... zgodnie z kolejnością ui.rects.
        """
        ...

    def run_entry(
        self,
        job_dir: Path,
        entry_id: str,
        stage: OcrStage = OcrStage.STAGE1_RAW_AND_CLASSIFY,
    ) -> OcrResult:
        """
        Uruchamia OCR dla pojedynczego entry (crop/rect).
        """
        ...

    def run_job(
        self,
        job_dir: Path,
        stages: list[OcrStage] | None = None,
    ) -> list[OcrResult]:
        """
        Uruchamia OCR dla wszystkich entry w job.json.
        Zwraca listę wyników (po jednym na entry i per stage).
        """
        ...


class BaseOcrEngine(ABC):
    """
    Wygodna klasa bazowa (opcjonalna) – ułatwia testy i spójność.
    """

    def __init__(self) -> None:
        self._config = EngineConfig()

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    def caps(self) -> EngineCaps:
        return EngineCaps()

    def configure(self, config: EngineConfig) -> None:
        self._config = config

    def load_job(self, job_dir: Path) -> dict:
        job_path = job_dir / "job.json"
        if not job_path.exists():
            raise FileNotFoundError(f"Missing job.json: {job_path}")

        data = json.loads(job_path.read_text(encoding="utf-8"))

        ui = data.get("ui")
        if not isinstance(ui, dict):
            raise ValueError("job.json: missing or invalid 'ui' object")

        rects = ui.get("rects")
        if not isinstance(rects, list):
            raise ValueError("job.json: missing or invalid 'ui.rects' (expected list)")

        image_size = ui.get("image_size")
        if not isinstance(image_size, dict):
            raise ValueError(
                "job.json: missing or invalid 'ui.image_size' (expected object with w/h)"
            )

        img_w = image_size.get("w")
        img_h = image_size.get("h")
        if not isinstance(img_w, int) or not isinstance(img_h, int) or img_w <= 0 or img_h <= 0:
            raise ValueError("job.json: invalid 'ui.image_size' (expected positive ints w/h)")

        # validate rects (technicznie: typy, zakresy, granice obrazu)
        for idx, r in enumerate(rects):
            if not isinstance(r, dict):
                raise ValueError(f"job.json: ui.rects[{idx}] must be an object")

            x = r.get("x")
            y = r.get("y")
            w = r.get("w")
            h = r.get("h")

            if not all(isinstance(v, int) for v in (x, y, w, h)):
                raise ValueError(f"job.json: ui.rects[{idx}] must have int x,y,w,h")

            if x < 0 or y < 0 or w <= 0 or h <= 0:
                raise ValueError(
                    f"job.json: ui.rects[{idx}] has invalid geometry (x,y>=0 and w,h>0)"
                )

            if x + w > img_w or y + h > img_h:
                raise ValueError(
                    f"job.json: ui.rects[{idx}] out of bounds for image_size w={img_w} h={img_h}"
                )

        return data

    def ensure_job_layout(self, job_dir: Path) -> None:
        (job_dir / "ocr").mkdir(parents=True, exist_ok=True)
        (job_dir / "ocr" / "artifacts").mkdir(parents=True, exist_ok=True)

        if self._config.runtime_dir:
            self._config.runtime_dir.mkdir(parents=True, exist_ok=True)

    def iter_entry_ids(self, job: dict):
        rects = job["ui"]["rects"]
        for i in range(len(rects)):
            yield f"e{i + 1}"

    def run_job(self, job_dir: Path, stages: list[OcrStage] | None = None) -> list[OcrResult]:
        job = self.load_job(job_dir)
        self.ensure_job_layout(job_dir)

        stages = stages or [OcrStage.STAGE1_RAW_AND_CLASSIFY, OcrStage.STAGE2_STRUCTURED_EXTRACTION]

        out: list[OcrResult] = []
        for entry_id in self.iter_entry_ids(job):
            for st in stages:
                out.append(self.run_entry(job_dir=job_dir, entry_id=entry_id, stage=st))
        return out
