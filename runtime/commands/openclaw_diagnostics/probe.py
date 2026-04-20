from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Final, cast

TAG = "[openclaw_start]"

URL_READY: Final[str] = "http://127.0.0.1:7789/ready"
URL_READYZ: Final[str] = "http://127.0.0.1:7789/readyz"


def probe_endpoint_result(url: str, *, timeout: float = 2.0) -> tuple[str, str]:
    """Return (status_code, body) where status_code is HTTP code as str, or ``ERR``."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            chunk = resp.read()
            if isinstance(chunk, str):
                ok_body: str = chunk.strip()
            else:
                ok_body = chunk.decode("utf-8", "replace").strip()
            return str(resp.status), ok_body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if isinstance(raw, str):
            err_text = raw.strip()
        elif isinstance(raw, (bytes, bytearray)):
            err_text = raw.decode("utf-8", "replace").strip()
        else:
            err_text = str(raw).strip()
        return str(exc.code), cast(str, err_text)
    except Exception as exc:
        return "ERR", str(exc)


def format_probe_line(status: str, body: str) -> str:
    return f"{status}\t{body}"


def probe_readiness() -> None:
    status, body = probe_endpoint_result(URL_READY)
    print(format_probe_line(status, body))


def probe_readyz() -> None:
    status, body = probe_endpoint_result(URL_READYZ)
    print(format_probe_line(status, body))


def is_ready_payload(body: str) -> bool:
    try:
        payload = json.loads(body)
    except Exception:
        return False
    return payload.get("ready") is True


def monitor_readiness(openclaw_pid: int, *, attempts: int, interval_seconds: int) -> None:
    """Long-running readiness loop; mirrors openclaw_start monitor_gateway_readiness."""
    sleep_seconds = max(1, int(interval_seconds))
    attempt = 1
    while attempt <= attempts:
        try:
            os.kill(openclaw_pid, 0)
        except OSError:
            return

        status_code, body = probe_endpoint_result(URL_READY)
        body_display = body if body else "<empty>"
        print(
            f"{TAG} Gateway readiness attempt {attempt}/{attempts}: "
            f"status={status_code} body={body_display}"
        )

        if status_code == "200" and is_ready_payload(body):
            print(
                f"{TAG} Gateway readiness reached ready=true on attempt {attempt}/{attempts}"
            )
            return

        attempt += 1
        time.sleep(sleep_seconds)

    try:
        os.kill(openclaw_pid, 0)
    except OSError:
        return

    readyz_status, readyz_body = probe_endpoint_result(URL_READYZ)
    readyz_display = readyz_body if readyz_body else "<empty>"
    print(
        f"{TAG} Gateway readiness still not ready after {attempts} attempts; "
        f"readyz status={readyz_status} body={readyz_display}"
    )
