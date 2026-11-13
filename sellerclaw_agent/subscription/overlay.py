"""Which models the OpenClaw config runs when the owner's own subscription powers the agent.

The cloud sends a lineup per provider (``llm.subscription``); the machine knows which provider the
owner connected. The overlay joins the two into OpenClaw model refs:

- **Claude** — ``anthropic/<model>`` executed by the Claude Code CLI (``agentRuntime.id:
  "claude-cli"``) on its native sign-in. Our LiteLLM passthrough group for Anthropic (it reads
  PDFs) is renamed so the PDF model never collides with the subscription's refs.
- **ChatGPT** — ``openai/<model>`` on OpenClaw's own loop (``agentRuntime.id: "openclaw"``) with
  the machine's ChatGPT sign-in.

Every role takes its first candidate and keeps the rest as fallbacks — all on the same
subscription. Nothing falls back to our paid models: when the plan's limit is used up the agent
waits (owner's decision, 2026-11-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotations only: ``sellerclaw_agent.bundle`` imports this module while it loads.
    from sellerclaw_agent.bundle.manifest import SubscriptionLineup, SubscriptionRole
    from sellerclaw_agent.subscription.state import SubscriptionChoice


@dataclass(frozen=True)
class _ProviderRoute:
    model_provider: str
    runtime_id: str
    plugin_id: str


_ROUTES: dict[str, _ProviderRoute] = {
    "claude": _ProviderRoute(model_provider="anthropic", runtime_id="claude-cli", plugin_id="anthropic"),
    "chatgpt": _ProviderRoute(model_provider="openai", runtime_id="openclaw", plugin_id="openai"),
}

#: The LiteLLM group the cloud names ``anthropic`` (native PDF reading on credits) is renamed to this
#: while Claude runs on the owner's plan, so ``anthropic/*`` refs mean the subscription only.
CLAUDE_MODE_PASSTHROUGH_GROUP = "sellerclaw-anthropic"


@dataclass(frozen=True)
class RoleModels:
    """One role's OpenClaw model refs on the subscription."""

    primary: str
    fallbacks: tuple[str, ...] = ()
    thinking: str | None = None

    def model_block(self) -> str | dict[str, object]:
        """``agents.*.model``: a plain ref, or ``{primary, fallbacks}`` when there are candidates left."""
        if not self.fallbacks:
            return self.primary
        return {"primary": self.primary, "fallbacks": list(self.fallbacks)}

    @property
    def refs(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True)
class SubscriptionOverlay:
    """The OpenClaw-side shape of "this agent runs on the owner's own plan"."""

    provider: str
    runtime_id: str
    plugin_id: str
    supervisor: RoleModels
    specialist: RoleModels
    utility: RoleModels

    @property
    def model_refs(self) -> tuple[str, ...]:
        """Every ref the overlay uses, once each — each gets the subscription runtime pinned."""
        return tuple(dict.fromkeys((*self.supervisor.refs, *self.specialist.refs, *self.utility.refs)))

    def runtime_pins(self) -> dict[str, dict[str, object]]:
        """``agents.defaults.models``: the execution runtime pinned per model, never provider-wide."""
        return {ref: {"agentRuntime": {"id": self.runtime_id}} for ref in self.model_refs}

    @property
    def renames_anthropic_passthrough(self) -> bool:
        return self.provider == "claude"


def _role(route: _ProviderRoute, role: SubscriptionRole) -> RoleModels:
    refs = [f"{route.model_provider}/{model}" for model in role.models]
    return RoleModels(primary=refs[0], fallbacks=tuple(refs[1:]), thinking=role.thinking)


def resolve_overlay(
    lineups: dict[str, SubscriptionLineup],
    choice: SubscriptionChoice | None,
) -> SubscriptionOverlay | None:
    """The overlay for the connected subscription, or ``None`` to run on credits.

    ``None`` both when nothing is connected and when the cloud sent no lineup for the connected
    provider — the latter is how the cloud switches the feature off for an agent.
    """
    if choice is None:
        return None
    lineup = lineups.get(choice.provider)
    route = _ROUTES.get(choice.provider)
    if lineup is None or route is None:
        return None
    return SubscriptionOverlay(
        provider=choice.provider,
        runtime_id=route.runtime_id,
        plugin_id=route.plugin_id,
        supervisor=_role(route, lineup.supervisor),
        specialist=_role(route, lineup.specialist),
        utility=_role(route, lineup.utility),
    )
