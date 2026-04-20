from __future__ import annotations

import pytest
from sellerclaw_agent.cloud import chat_listener as chat_listener_module

pytestmark = pytest.mark.unit


def test_inbound_body_strips_session_key() -> None:
    body = chat_listener_module._inbound_body_from_sse(
        {
            "chat_id": "c1",
            "agent_id": "supervisor",
            "user_id": "u1",
            "text": "hi",
            "message_id": "m1",
            "session_key": "agent:supervisor:sellerclaw-ui:direct:c1",
            "raw_content": [{"type": "text", "text": "hi"}],
        },
    )
    assert body == {
        "chat_id": "c1",
        "agent_id": "supervisor",
        "user_id": "u1",
        "text": "hi",
        "message_id": "m1",
        "raw_content": [{"type": "text", "text": "hi"}],
    }
    assert "session_key" not in body
