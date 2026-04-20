from __future__ import annotations

from typing import Protocol


class AssembledAgentLike(Protocol):
    """Structural type for assembled agent configs (monolith or sellerclaw_agent dataclass)."""

    @property
    def agent_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def model_tier(self) -> object: ...

    @property
    def is_entry_point(self) -> bool: ...

    @property
    def subagent_ids(self) -> list[str]: ...

    @property
    def tools_allow(self) -> list[str]: ...

    @property
    def tools_deny(self) -> list[str]: ...

    @property
    def agents_md(self) -> str: ...

    @property
    def memory_md(self) -> str: ...

    @property
    def soul_md(self) -> str | None: ...

    @property
    def user_md(self) -> str | None: ...

    @property
    def skills(self) -> dict[str, str]: ...
