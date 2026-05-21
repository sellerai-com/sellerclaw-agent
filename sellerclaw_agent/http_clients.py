"""httpx client factories that never use an ambient proxy.

All of the agent's own HTTP traffic — control-plane polling, cloud auth,
state restore, media upload, and forwarding to the local OpenClaw gateway on
``127.0.0.1`` — must go out directly, ignoring any ``all_proxy`` /
``http(s)_proxy`` set in the environment. The only thing that should route
through the user's proxy is the OpenClaw agent's *browser*, which is
configured separately.

We disable httpx's ``trust_env`` so it never reads those proxy variables. This
also avoids ``trust_env`` crashing at client construction on schemes httpx
can't parse (e.g. a bare ``socks://`` instead of ``socks5://``).

Use these factories instead of constructing ``httpx.Client`` /
``httpx.AsyncClient`` directly anywhere in the agent.
"""

from __future__ import annotations

from typing import Any

import httpx


def sync_client(**kwargs: Any) -> httpx.Client:
    """``httpx.Client`` with ambient proxy / env config disabled."""
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)


def async_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` with ambient proxy / env config disabled."""
    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)
