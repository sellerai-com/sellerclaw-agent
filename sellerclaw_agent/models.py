from __future__ import annotations

from enum import StrEnum


class ModelTier(StrEnum):
    """Model tier for agent workloads (maps to complex vs simple LiteLLM groups)."""

    COMPLEX = "complex"
    SIMPLE = "simple"
