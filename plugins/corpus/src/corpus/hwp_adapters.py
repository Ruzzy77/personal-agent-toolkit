"""Packaged binary HWP extraction adapter."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from .adapters import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractionEnvelope,
)
from .errors import ExtractionError

_ADAPTER_SOURCE = Path(__file__).with_name("hwp5_adapter_main.py")
_WRAPPER_SOURCE = Path(__file__)


class HWP5SpecPartialAdapter:
    """Extract exact HWP5 section/PARA_TEXT observations without semantic guesses."""

    def __init__(self) -> None:
        try:
            source_hash = hashlib.sha256(
                _ADAPTER_SOURCE.read_bytes() + b"\0" + _WRAPPER_SOURCE.read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise ExtractionError("packaged HWP adapter source is unavailable") from exc
        self.config = {
            "max_sections": 512,
            "max_records_per_section": 500_000,
            "max_inflated_section_bytes": 128 * 1024 * 1024,
            "max_total_inflated_bytes": 512 * 1024 * 1024,
            "max_total_records": 2_000_000,
            "specification": "hancom-hwp5-revision-1.3",
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="work-corpus.hwp5.spec-partial",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=self.config,
            capabilities=AdapterCapabilities(
                format_ids=("hwp",),
                structural_unit_types=("section_paragraph",),
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_geometry=False,
                supports_confidence=False,
                supports_ocr=False,
                may_emit_partial=True,
            ),
        )
        self.budgets = AdapterBudgets(
            timeout_seconds=60,
            max_input_bytes=512 * 1024 * 1024,
            max_stdout_bytes=64 * 1024 * 1024,
            max_units=200_000,
            max_total_content_chars=50_000_000,
        )

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        adapter = ExternalJSONLAdapter(
            self.descriptor,
            (sys.executable, str(_ADAPTER_SOURCE)),
            self.budgets,
            config=self.config,
        )
        return adapter.extract(path, format_id=format_id)
