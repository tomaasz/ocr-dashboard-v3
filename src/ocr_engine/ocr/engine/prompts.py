"""
Prompt management module for OCR engine.

Handles loading, templating, and rendering of prompts for Gemini OCR.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PromptMeta:
    """Metadata for prompt rendering."""

    file_name: str = ""
    source_path: str = ""


class PromptManager:
    """Manages OCR prompts: loading from JSON and rendering with metadata."""

    def __init__(self, prompts_file: Path):
        self.prompts_file = prompts_file
        self._cache: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """Load prompts from JSON file (cached)."""
        if self._cache is None:
            with open(self.prompts_file, encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def get_template(self, prompt_id: str) -> list[str]:
        """Get prompt template by ID."""
        data = self.load()
        for p in data.get("prompts", []):
            if p.get("id") == prompt_id:
                return p.get("template", [])
        # Fallback to first prompt
        return data["prompts"][0]["template"]

    def get_default_id(self) -> str:
        """Get default prompt ID."""
        data = self.load()
        return data.get("default_prompt_id", "generic_json")

    def render(self, prompt_id: str, meta: PromptMeta) -> str:
        """Render prompt with metadata substitution."""
        template = self.get_template(prompt_id)
        text = "\n".join(template)
        return text.replace("__FILE_NAME__", meta.file_name).replace(
            "__SOURCE_PATH__", meta.source_path
        )

    def setup_and_render(self, prompt_id: str | None, file_name: str, source_path: str) -> str:
        """Setup metadata and render prompt."""
        pid = prompt_id if prompt_id else self.get_default_id()
        meta = PromptMeta(file_name=file_name, source_path=source_path)
        return self.render(pid, meta)
