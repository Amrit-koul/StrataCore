from dataclasses import dataclass, field
from typing import Any


@dataclass
class CoreState:
    """Process-local session state (single-user demo server)."""

    active_csv_table: str | None = None
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    metrics: dict[str, Any] = field(
        default_factory=lambda: {
            "total": 0,
            "success": 0,
            "llm_used": 0,
            "self_corrected": 0,
            "latencies": [],
        }
    )
