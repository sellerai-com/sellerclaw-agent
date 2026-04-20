"""Unit tests for edge chat SSE message_id deduplication."""

from __future__ import annotations

import pytest
from sellerclaw_agent.cloud import chat_listener as chat_listener_module

pytestmark = pytest.mark.unit


def test_message_id_dedup_after_record_only() -> None:
    dedup = chat_listener_module._MessageIdDedup(max_size=100)
    assert dedup.already_forwarded("m1") is False
    dedup.record_forwarded("m1")
    assert dedup.already_forwarded("m1") is True


def test_message_id_dedup_empty_and_whitespace_never_stored() -> None:
    dedup = chat_listener_module._MessageIdDedup(max_size=2)
    assert dedup.already_forwarded("") is False
    dedup.record_forwarded("")
    assert dedup.already_forwarded("") is False
    assert dedup.already_forwarded("  ") is False
    dedup.record_forwarded("  ")
    assert dedup.already_forwarded("  ") is False
    assert dedup.already_forwarded("a") is False
    dedup.record_forwarded("a")
    assert dedup.already_forwarded("a") is True


def test_message_id_dedup_lru_eviction_oldest() -> None:
    dedup = chat_listener_module._MessageIdDedup(max_size=3)
    dedup.record_forwarded("a")
    dedup.record_forwarded("b")
    dedup.record_forwarded("c")
    dedup.record_forwarded("d")
    assert dedup.already_forwarded("a") is False
    dedup.record_forwarded("a")
    assert dedup.already_forwarded("a") is True
