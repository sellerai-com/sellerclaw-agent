from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sellerclaw_agent.bundle.protocols import AssembledAgentLike


@dataclass(frozen=True)
class ModelDefaults:
    """Resolved ``agents.defaults`` model blocks for the OpenClaw config.

    Built by the caller from the manifest's ``llm`` block so the generator stays
    agnostic of how refs are derived. ``image_generation_model`` /
    ``video_generation_model`` / ``pdf_model`` are ``None`` when the manifest omits
    the corresponding ``llm.*_model`` block (the key is then dropped from the config).
    ``compaction_model`` / ``memory_flush_model`` are always set (the caller falls
    back to ``text_model.primary`` when the manifest omits them).
    """

    model: dict[str, object]
    compaction_model: str
    memory_flush_model: str
    image_generation_model: dict[str, object] | None = None
    video_generation_model: dict[str, object] | None = None
    pdf_model: dict[str, object] | None = field(default=None)


# OpenClaw gateway logging in generated config (not user/manifest input). Secret redaction
# is not configurable: since OpenClaw 2026.8 it is always on and `logging.redactSensitive`
# is rejected as an unknown key.
OPENCLAW_BUNDLE_LOG_LEVEL = "warn"
OPENCLAW_BUNDLE_CONSOLE_STYLE = "pretty"

# Per-file truncation limit for workspace bootstrap files (AGENTS.md, SOUL.md, …). The
# supervisor's assembled AGENTS.md (full roster + owner rules appended by the cloud) is the
# largest: ~34.7k before the 2026-07 template slim-down, back to ~40.8k by 2026-09 as rules
# were added. Raised to 50k then — the previous 40k had run out again, and a rule worth writing
# should not cost deleting one that already earned its place. The cloud's
# test_assembled_bootstrap_files_fit_openclaw_limit guards the template side against this value.
OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS = 50000

# Bundled OpenClaw skills allowed in every agent workspace. All other bundled skills are
# dropped at load time; workspace/manifest skills and extra-dir skills are unaffected.
# Keep the list minimal: SellerClaw business flows live in manifest skills, not OpenClaw
# stock skills (meme-maker, taskflow, dev debuggers, etc.).
OPENCLAW_BUNDLE_ALLOWED_SKILLS: tuple[str, ...] = ("healthcheck",)

# Local sellerclaw-agent HTTP port inside the runtime container; plugins call back to it
# via loopback for media upload proxying. Kept as a module constant so bundle tests can
# assert the emitted config.
OPENCLAW_LOCAL_AGENT_BASE_URL = "http://127.0.0.1:8001"

# Keyless web search: plugin id and OpenClaw web-search provider id.
SELLERCLAW_WEB_SEARCH_PLUGIN_ID = "sellerclaw-web-search"
# Both of our own plugins ship as BUNDLED plugins: the image copies them into
# ``/app/dist/extensions/`` (OpenClaw's own bundled-plugin root), so they are discovered with
# ``origin: "bundled"`` and must NOT appear in ``plugins.load.paths`` — a path-loaded copy of the
# same id makes OpenClaw warn about a duplicate plugin id and override one with the other.
#
# Why bundled and not a load path: these are parts of the product, not optional extensions,
# and OpenClaw gates several plugin APIs on ``origin === "bundled"``. Most importantly
# ``api.session.workflow.scheduleSessionTurn`` (schedule a turn in a chat session) is a SILENT
# no-op for a non-bundled plugin — it returns undefined with no error and no log line. Same gate
# applies to the host-owned agent event streams (lifecycle/tool/assistant/error/item/plan),
# trusted tool policies without a ``contracts`` declaration, reserved command ownership,
# ``system.notify`` and native Control UI route placement.
OPENCLAW_BUNDLED_DIR_SELLERCLAW_UI = "/app/dist/extensions/sellerclaw-ui"
OPENCLAW_BUNDLED_DIR_SELLERCLAW_WEB_SEARCH = "/app/dist/extensions/sellerclaw-web-search"
# Long-term memory: the official mem0 plugin in platform mode, pointed at the cloud's
# Mem0-compatible adapter (`{agent_api_base}/mem0`). Bundled into the image at this path.
MEM0_PLUGIN_ID = "openclaw-mem0"
OPENCLAW_PLUGIN_PATH_MEM0 = "/opt/openclaw-plugins/openclaw-mem0"

# Bundled OpenClaw plugin that backs the PDF tool's fallback (extract + page-render)
# pipeline. Without it the PDF tool fails with `PDF extraction disabled or unavailable:
# enable the document-extract plugin to process application/pdf files`. Bundled with
# the OpenClaw runtime, so no load path needed — just allow + enable.
OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID = "document-extract"

# The bundled telegram/whatsapp channel plugins are deliberately NOT in ``plugins.allow``. Every
# boot warns that a configured channel's plugin is "omitted from plugins.allow" and auto-enables it
# for that run, which reads like an oversight — it is not. Allow-listing whatsapp promotes its
# channel to one the gateway's readiness probe waits for, and an unpaired WhatsApp (no QR session
# yet, which is the normal state until the owner scans one) then reports "stopped" forever: /ready
# stayed 503 with failing=["whatsapp"], the health monitor restarted the channel in a loop, and the
# cloud kept treating a perfectly healthy agent as still starting — chat included. The warning is
# the cheaper of the two.


def _build_telegram_groups(*, group_ids: list[str]) -> dict[str, dict[str, bool]]:
    result: dict[str, dict[str, bool]] = {}
    for gid in group_ids:
        normalized = gid.strip()
        if normalized:
            result[normalized] = {"requireMention": True}
    return result


def _build_control_ui_config(
    *,
    allowed_origins: tuple[str, ...],
) -> dict[str, object]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in allowed_origins:
        normalized = item.strip().rstrip("/")
        if not normalized or normalized in seen:
            continue
        unique.append(normalized)
        seen.add(normalized)
    return {
        "allowedOrigins": unique,
        "dangerouslyAllowHostHeaderOriginFallback": False,
    }


def _merge_openclaw_channels(
    *,
    telegram_channel: dict[str, object] | None,
    whatsapp_channel: dict[str, object] | None,
    sellerclaw_ui: dict[str, object],
) -> dict[str, object]:
    channels: dict[str, object] = {"sellerclaw-ui": sellerclaw_ui}
    if telegram_channel is not None:
        channels["telegram"] = telegram_channel
    if whatsapp_channel is not None:
        channels["whatsapp"] = whatsapp_channel
    return channels


def generate_openclaw_config(
    *,
    assembled_agents: Sequence[AssembledAgentLike],
    gateway_token: str,
    hooks_token: str,
    agent_api_key: str,
    user_id: UUID,
    sellerclaw_api_url: str,
    sellerclaw_agent_api_base_url: str | None = None,
    providers: dict[str, object],
    openclaw_version: str | None = None,
    telegram_enabled: bool,
    telegram_bot_token: str,
    telegram_allowed_user_ids: tuple[str, ...],
    telegram_allowed_group_ids: tuple[str, ...],
    whatsapp_enabled: bool = False,
    whatsapp_allowed_numbers: tuple[str, ...] = (),
    allowed_origins: tuple[str, ...] = (),
    browser_enabled: bool = True,
    web_search_enabled: bool = False,
    web_search_auth_token: str = "",
    primary_channel: str = "sellerclaw-ui",
    model_defaults: ModelDefaults,
    thinking_default: str = "adaptive",
    reasoning_default: str = "off",
    heartbeat_every: str = "0m",
    cron_enabled: bool = True,
    web_fetch_enabled: bool = True,
    memory_enabled: bool = False,
) -> str:
    """Build OpenClaw JSON config from assembled agents and flat parameters.

    ``providers`` is the fully built ``models.providers`` mapping (manifest-driven,
    assembled by the bundle builder). ``model_defaults`` carries the manifest-derived
    ``agents.defaults`` model blocks. ``thinking_default`` / ``reasoning_default`` are
    likewise resolved from the manifest by the caller and emitted under
    ``agents.defaults`` (``thinkingDefault`` / ``reasoningDefault``). The agent heartbeat
    cadence is cloud-owned (``heartbeat_every``, from the manifest); ``"0m"`` disables the poll.
    The block is always emitted because OpenClaw's built-in default is an *enabled* 30m/1h poll,
    so omitting it would leave that running; the model is pinned to the cheap ``simple`` tier.
    """
    agent_ids = [agent.agent_id for agent in assembled_agents]
    entry_point = next(agent.agent_id for agent in assembled_agents if agent.is_entry_point)

    telegram_token = (telegram_bot_token or "").strip()
    telegram_on = telegram_enabled and bool(telegram_token)
    telegram_allow_from = [f"tg:{uid.strip()}" for uid in telegram_allowed_user_ids if uid.strip()]
    telegram_groups = _build_telegram_groups(
        group_ids=[gid.strip() for gid in telegram_allowed_group_ids if str(gid).strip()]
    )
    has_telegram_allowlist = bool(telegram_allow_from) or bool(telegram_groups)
    telegram_policy = "allowlist" if has_telegram_allowlist else "open"
    telegram_channel: dict[str, object] | None = None
    telegram_bindings: list[dict[str, object]] = []
    # The telegram channel block exists whenever a bot token is configured; its
    # ``enabled`` flag mirrors the manifest. The entry-point binding is added only
    # when telegram is actually on (enabled + token).
    if telegram_token:
        telegram_channel = {
            "enabled": telegram_enabled,
            "botToken": telegram_token,
            "dmPolicy": telegram_policy,
            "allowFrom": telegram_allow_from,
            "groupPolicy": telegram_policy,
            "groups": telegram_groups,
        }
    if telegram_on:
        telegram_bindings = [
            {
                "agentId": entry_point,
                "match": {"channel": "telegram"},
            },
        ]

    # WhatsApp (personal account, Baileys). DM-only: groupPolicy is hard-disabled, so the
    # agent never reads or replies in WhatsApp groups. No credential is emitted — the session
    # is paired (QR) and persisted in OpenClaw's default whatsapp authDir on the agent. Phone
    # numbers are normalized to digits (OpenClaw matches allowFrom on digits-only E.164).
    whatsapp_allow_from = [
        digits for uid in whatsapp_allowed_numbers if (digits := re.sub(r"\D", "", str(uid)))
    ]
    whatsapp_channel: dict[str, object] | None = None
    whatsapp_bindings: list[dict[str, object]] = []
    if whatsapp_enabled:
        whatsapp_channel = {
            "enabled": True,
            "dmPolicy": "allowlist" if whatsapp_allow_from else "open",
            "allowFrom": whatsapp_allow_from,
            "groupPolicy": "disabled",
        }
        whatsapp_bindings = [
            {
                "agentId": entry_point,
                "match": {"channel": "whatsapp"},
            },
        ]

    # Derived agent API base URL (SELLERCLAW_AGENT_API_BASE_URL). Defaults to the
    # bare SELLERCLAW_API_URL when the caller doesn't supply a derived value, which
    # keeps older call sites (and tests) working without an explicit path segment.
    effective_agent_api_base_url = (
        sellerclaw_agent_api_base_url
        if sellerclaw_agent_api_base_url is not None
        else sellerclaw_api_url
    )
    effective_agent_api_base_url = (effective_agent_api_base_url or "").strip().rstrip("/")

    if web_search_enabled:
        if not (web_search_auth_token or "").strip():
            raise ValueError(
                "Web search auth token is required when web search is enabled "
                "(agent bearer from agent_token.json or AGENT_API_KEY)."
            )
        if not effective_agent_api_base_url:
            raise ValueError(
                "SellerClaw API base URL is required when web search is enabled (SELLERCLAW_API_URL)."
            )

    # Both of our own plugins are bundled into the image (see OPENCLAW_BUNDLED_DIR_* above), so
    # they are discovered without a load path. Only path-loaded plugins go in this list, which
    # today means the vendored third-party mem0 below.
    plugin_load_paths: list[str] = []
    # mem0 MUST stay in plugins.load.paths. It is referenced by plugins.slots.memory, and
    # OpenClaw resolves slot/allow/entries against load.paths + the installed registry: drop
    # the load path and config validation hard-fails with "plugins.slots.memory: plugin not
    # found: openclaw-mem0", so the gateway refuses to boot. (That validation also runs before
    # every CLI command, so a runtime `openclaw plugins install` cannot bootstrap itself out of
    # the invalid state — it is blocked by the very config it would repair.)
    #
    # Being on a load path satisfies validation but does NOT make the plugin "installed" in
    # OpenClaw's plugin registry, which is what the startup doctor checks — hence the doctor
    # re-downloads @mem0/openclaw-mem0 from npm on every cold start of a volumeless machine.
    # Fixing that means ALSO getting it into the registry, without removing this load path.
    if memory_enabled:
        plugin_load_paths.append(OPENCLAW_PLUGIN_PATH_MEM0)

    # Long-term memory: mem0 plugin in PLATFORM mode pointed at the cloud Mem0-compatible adapter.
    # The agent presents its own agent API key (the cloud resolves it to the user and bills
    # extraction/embeddings to that user); no DB credentials ever live on the box.
    memory_plugin_entry: dict[str, object] | None = None
    if memory_enabled:
        if not effective_agent_api_base_url:
            raise ValueError("sellerclaw_agent_api_base_url is required when memory_enabled is set")
        if not (agent_api_key or "").strip():
            raise ValueError("agent_api_key is required when memory_enabled is set")
        memory_plugin_entry = {
            "enabled": True,
            # The plugin is loaded from a path (non-bundled), so OpenClaw blocks its conversation-
            # access hooks unless we opt in explicitly. Without this the ``agent_end`` hook — which
            # auto-captures the conversation into long-term memory — is silently skipped, so nothing
            # is ever remembered.
            "hooks": {"allowConversationAccess": True},
            "config": {
                "mode": "platform",
                "apiKey": agent_api_key.strip(),
                "baseUrl": f"{effective_agent_api_base_url}/mem0",
                "userId": str(user_id),
                "skills": {
                    "triage": {"enabled": True},
                    "recall": {"enabled": True},
                    # Dream = the plugin's periodic in-conversation memory consolidation. We disable
                    # it: it only runs behind a coarse gate (minHours=24 / minSessions=5), adds
                    # latency/cost to user turns, and still depends on the agent calling write tools.
                    # Capture is handled deterministically cloud-side instead (chat-archival job)
                    # plus the inline memory_add instruction in the supervisor's AGENTS.md.
                    "dream": {"enabled": False},
                },
            },
        }

    web_search_plugin_entry: dict[str, object] | None = None
    web_search_plugin_id: str | None = None
    if web_search_enabled:
        web_search_plugin_id = SELLERCLAW_WEB_SEARCH_PLUGIN_ID
        web_search_plugin_entry = {
            "enabled": True,
            "config": {
                "webSearch": {
                    "baseUrl": effective_agent_api_base_url,
                    "authToken": (web_search_auth_token or "").strip(),
                }
            },
        }

    if web_search_enabled:
        web_search_tools: dict[str, object] = {
            "enabled": True,
            "provider": SELLERCLAW_WEB_SEARCH_PLUGIN_ID,
        }
    else:
        web_search_tools = {"enabled": False}

    # Keyed by agent id: OpenClaw 2026.8 replaced the `agents.list` array with the
    # `agents.entries` mapping, where the key IS the id (no `id` field inside the entry).
    agents_entries: dict[str, dict[str, object]] = {}
    for agent in assembled_agents:
        payload: dict[str, object] = {
            "name": agent.name,
            "workspace": f"/home/node/.openclaw/workspace-{agent.agent_id}",
            "model": agent.model_ref,
            "tools": {
                "allow": list(agent.tools_allow),
                # Deny OpenClaw builtin media tools: media generation goes through
                # sellerclaw-cli (cloud media endpoints), because the builtin tools' async
                # completion delivery is lost on the request-scoped sellerclaw-ui channel.
                "deny": list(
                    dict.fromkeys(
                        [*agent.tools_deny, "image_generate", "video_generate", "music_generate"]
                    )
                ),
            },
        }
        if agent.is_entry_point:
            # No `default: true` marker — it is retired in 2026.8. The entry point is now
            # designated per surface by the `bindings` below (one per enabled channel).
            # Only the entry point orchestrates subagents. Block `sessions_spawn` without
            # an `agentId`: otherwise OpenClaw spawns a clone of the requester (a workspace
            # without platform skills, rediscovering CLI commands via --help until timeout).
            payload["subagents"] = {
                "allowAgents": list(agent.subagent_ids),
                "requireAgentId": True,
            }
        # Per-agent thinking override comes from the manifest (always resolved there).
        agent_thinking = getattr(agent, "thinking_default", None)
        if isinstance(agent_thinking, str) and agent_thinking.strip():
            payload["thinkingDefault"] = agent_thinking.strip()
        agents_entries[agent.agent_id] = payload

    sellerclaw_ui_plugin_config: dict[str, object] = {
        "apiBaseUrl": sellerclaw_api_url.strip().rstrip("/"),
        "userId": str(user_id),
        "agentApiKey": (agent_api_key or "").strip(),
        "internalWebhookSecret": hooks_token,
        "primaryChannel": primary_channel,
        "localAgentBaseUrl": OPENCLAW_LOCAL_AGENT_BASE_URL,
    }

    # OpenClaw treats `meta` as the marker that the file came from a trusted writer: if
    # `lastKnownGood` carried a `meta` object and our new file doesn't, OpenClaw flags
    # `missing-meta-vs-last-good` and silently restores the previous backup, dropping every
    # fresh value (including the rotated agentApiKey). The guard only checks that `meta` is
    # an object, so an empty one is enough when the runtime version is unknown.
    #
    # `lastTouchedAt` moved into OpenClaw's own SQLite state in 2026.8 and is now rejected
    # here; `lastTouchedVersion` is the remaining stamp. It also drives OpenClaw's downgrade
    # guard (an older binary refuses to mutate a config written by a newer one), so it must
    # name the runtime that actually ships in this image, never a value we invent.
    meta_block: dict[str, object] = {}
    runtime_version = (openclaw_version or "").strip()
    if runtime_version:
        meta_block["lastTouchedVersion"] = runtime_version

    agents_defaults: dict[str, object] = {
        "skipBootstrap": True,
        # Per-turn cap: max seconds for a single agent turn (one model iteration) before it is
        # aborted. Applies to every agent, including each individual turn of a spawned subagent.
        # It does NOT bound a subagent's total delegated run — that is `subagents.runTimeoutSeconds`
        # below. See https://docs.openclaw.ai/gateway/config-agents (`agents.defaults.timeoutSeconds`).
        #
        # An aborted turn is not a graceful stop: OpenClaw delivers a bare "LLM request timed
        # out." as the run's final text, so whatever the turn had already accomplished is
        # reported to the owner as a failure. At 600s that fired three times in one staging chat
        # (b76fd17a, 2026-08-19) on a supervisor doing ordinary work — the reasoning-heavy model
        # spent ~285s thinking before its first tool call, and each turn was cut mid-flight:
        # once just before the build was delegated (the job only started because the owner asked
        # "что случилось?"), once 40s after 626 listings had been shelved and verified, so the
        # finished result was announced as an error. 1200s doubles the headroom while staying
        # under both the subagent total-run cap (3600s) and the gateway proxy's read timeout
        # (1800s, runtime/nginx/openclaw-proxy.conf), so neither of those binds first.
        "timeoutSeconds": 1200,
        # Total-run cap for a spawned subagent session: max seconds a delegated subagent may run
        # across all its turns before OpenClaw aborts the run. This is the default when the entry
        # point calls `sessions_spawn` without an explicit `runTimeoutSeconds`. Without this key
        # OpenClaw applies no total-run limit (falls back to 0 = unbounded), so a stuck delegated
        # task could run indefinitely; we cap it at one hour. See
        # https://docs.openclaw.ai/tools/subagents and
        # https://docs.openclaw.ai/gateway/config-agents (`agents.defaults.subagents.runTimeoutSeconds`).
        #
        # `announceTimeoutMs` is how long OpenClaw waits for the supervisor's completion run (the
        # "A background task completed…" wake) to produce its final reply before falling back to
        # steering the same completion event into the still-running session as a duplicate. The
        # 120s default fired mid-run on a perfectly healthy announce (the run took ~128s), the
        # duplicate drew a NO_REPLY, and — because only a run's final text is delivered — the
        # already-written owner report was silently discarded (staging chat 4ed0228a, 2026-07-27).
        # There is no total-run bound to align with (`timeoutSeconds` above caps one *turn*), so no
        # value removes the race outright; this one is chosen to sit 30s above the single-turn cap:
        # a run that stalls inside one turn is aborted by that cap first — the fallback then
        # proceeds off the error — while a run still making progress across turns is left alone
        # instead of being interrupted. Upstream has no dedup before the steer fallback
        # (openclaw#41235 fixed a different sub-case), so the window is our only lever. It tracks
        # `timeoutSeconds` by construction: raising the turn cap without raising this would put
        # the announce window *below* it and reopen the dropped-report race.
        "subagents": {"runTimeoutSeconds": 3600, "announceTimeoutMs": 1_230_000},
        "bootstrapMaxChars": OPENCLAW_BUNDLE_BOOTSTRAP_MAX_CHARS,
        "model": model_defaults.model,
        "thinkingDefault": thinking_default,
        "reasoningDefault": reasoning_default,
        "blockStreamingDefault": "on",
        # How soon a reply starts appearing in the owner's chat.
        #
        # ``blockStreamingBreak: text_end`` pushes whatever the agent has written the moment it
        # finishes a text block — i.e. right before it reaches for a tool — instead of holding
        # everything until the turn ends. It is also the upstream default; pinning it here keeps
        # a future default change from silently making the chat go quiet again.
        #
        # ``minChars`` then decides how a *long* stretch of writing is cut up. At 800 the first
        # visible piece only appeared once the agent had written a whole page, so a run that
        # spent six minutes importing a product and opening tasks showed nothing at all until it
        # was over — its running commentary added up to 671 characters, under the threshold, and
        # landed in one lump at the end. 200 is about a short paragraph: enough that a cut still
        # falls on a sentence or line break, small enough that the owner sees the agent talking
        # while it works.
        "blockStreamingBreak": "text_end",
        "blockStreamingChunk": {
            "minChars": 200,
            "maxChars": 3000,
            "breakPreference": "newline",
        },
        "compaction": {
            # No `reserveTokensFloor`: OpenClaw 2026.8 computes the compaction reserve itself
            # and caps it against the active model's context window, so the old fixed 20000
            # floor is gone from the schema (it starved small-context models).
            "model": model_defaults.compaction_model,
            "memoryFlush": {
                "enabled": True,
                "softThresholdTokens": 10000,
                "model": model_defaults.memory_flush_model,
            },
        },
    }
    # image/video model blocks are intentionally NOT emitted: media generation goes through
    # sellerclaw-cli (cloud media endpoints) and the builtin image_generate/video_generate tools
    # (which read these blocks) are denied. The LiteLLM {user}image / {user}video groups stay
    # registered cloud-side and are used by the media endpoints under the user's virtual key.
    if model_defaults.pdf_model is not None:
        agents_defaults["pdfModel"] = model_defaults.pdf_model

    # Agent heartbeat. Cadence is cloud-owned (``heartbeat_every`` from the manifest); the cloud
    # also owns HEARTBEAT.md, so the on/off policy lives there. ``"0m"`` disables the periodic
    # poll — emitting the block is required because OpenClaw's built-in default is an enabled
    # 30m/1h poll, so omitting it would leave that running. Scheduled/proactive work runs via the
    # separate ``cron`` system, so this never affects scheduled tasks.
    #
    # ``model`` is a belt-and-suspenders cost guard owned here: whatever cadence the cloud sets,
    # an enabled heartbeat must use the cheap ``simple`` tier, never the primary ``complex`` model
    # with high thinking. We reuse the simple-tier group already resolved for memory-flush.
    agents_defaults["heartbeat"] = {"every": heartbeat_every, "model": model_defaults.memory_flush_model}

    # A multi-agent fleet has no default agent in 2026.8: every ambient surface (channels,
    # heartbeat, cron, bare CLI) must resolve an explicit owner or fail closed. OpenClaw
    # stamps this marker itself when it migrates such a config; a sole agent stays its own
    # implicit owner and the docs say to omit the marker there.
    agents_block: dict[str, object] = {
        "defaults": agents_defaults,
        "entries": agents_entries,
    }
    if len(agents_entries) > 1:
        agents_block["ownership"] = "explicit"
        # Companion to the marker above. Durable session rows that predate it (the retired
        # `agent:main:*` keys) are migrated only when their replacement owner is unambiguous,
        # which a fleet never is on its own: without this, OpenClaw preserves those rows but
        # leaves them unowned, stranding the chat history they hold. Naming the entry point
        # keeps the pre-2026.8 outcome, where it was the config's default agent.
        agents_defaults["sessionStore"] = {"agentId": entry_point}
        # Same problem for work the runtime itself schedules. Cron jobs created without an
        # explicit `--agent` (the memory plugin's nightly "dreaming" reconciliation is one)
        # have no owner to fall back to in a fleet, so they refuse to run: "Agent-less cron
        # job has no resolvable owner ... or set agents.defaults.systemAgent.agentId".
        agents_defaults["systemAgent"] = {"agentId": entry_point}

    config_payload = {
        "meta": meta_block,
        "logging": {
            "level": OPENCLAW_BUNDLE_LOG_LEVEL,
            "consoleLevel": OPENCLAW_BUNDLE_LOG_LEVEL,
            "consoleStyle": OPENCLAW_BUNDLE_CONSOLE_STYLE,
        },
        "gateway": {
            "mode": "local",
            "auth": {"mode": "token", "token": gateway_token},
            # ONLY the local nginx hop (runtime/nginx/openclaw-proxy.conf), and nothing
            # wider. OpenClaw resolves the client address by walking X-Forwarded-For from
            # the right and discarding every entry that is itself a trusted proxy: with
            # the docker bridge range in this list, a caller from the host (172.x) was
            # discarded as a proxy, left no client to attribute, and every
            # gateway-authenticated request through nginx came back 403
            # proxy_attribution_required.
            "trustedProxies": ["127.0.0.1", "::1"],
            "controlUi": _build_control_ui_config(
                allowed_origins=allowed_origins,
            ),
            "http": {
                "endpoints": {
                    "responses": {"enabled": True},
                },
            },
        },
        "hooks": {
            "enabled": True,
            "token": hooks_token,
            "path": "/hooks",
            "defaultSessionKey": "hook:dev",
            "allowRequestSessionKey": True,
            "allowedSessionKeyPrefixes": ["hook:", "agent:"],
            "allowedAgentIds": agent_ids,
        },
        "models": {"providers": providers},
        "agents": agents_block,
        "messages": {
            "visibleReplies": "automatic",
            "queue": {"mode": "steer"},
            # Never surface OpenClaw's synthesized "⚠️ <Tool> failed: …" notices in the chat.
            # A failed tool call is normal mid-turn noise — the agent sees the error in its
            # context and usually recovers on the next call — but the notice reads to the user
            # like a product bug even when the turn ended successfully. It is also a payload
            # the agent never wrote: it arrives on the final road while the agent's own reply
            # was already streamed as preview blocks, so the cloud treats it as the reply's
            # final wording and drops the streamed text (see ``append_text_part`` cloud-side).
            # The engine only emits it when it cannot tell the assistant already acknowledged
            # the failure, and that check is an English-only regex — so a Russian-speaking
            # agent triggers it on essentially every failed command.
            "suppressToolErrors": True,
        },
        "bindings": [
            *telegram_bindings,
            *whatsapp_bindings,
            {
                "agentId": entry_point,
                "match": {"channel": "sellerclaw-ui"},
            },
        ],
        # No `session.agentToAgent`: cross-agent messaging is configured solely under
        # `tools.agentToAgent` since 2026.8, and the old `maxPingPongTurns` cap is now a
        # built-in limit with no config surface.
        "session": {
            "dmScope": "per-channel-peer",
            "reset": {"mode": "idle"},
        },
        "channels": _merge_openclaw_channels(
            telegram_channel=telegram_channel,
            whatsapp_channel=whatsapp_channel,
            sellerclaw_ui=sellerclaw_ui_plugin_config,
        ),
        "plugins": {
            "enabled": True,
            **({"slots": {"memory": MEM0_PLUGIN_ID}} if memory_enabled else {}),
            "allow": [
                "sellerclaw-ui",
                "browser",
                OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID,
                *(
                    [web_search_plugin_id]
                    if web_search_enabled and web_search_plugin_id is not None
                    else []
                ),
                *([MEM0_PLUGIN_ID] if memory_enabled else []),
            ],
            "load": {"paths": plugin_load_paths},
            "entries": {
                "sellerclaw-ui": {
                    "enabled": True,
                    # Path-loaded (non-bundled) plugin: OpenClaw blocks its conversation-access
                    # hooks unless we opt in. Without this the completion-delivery guard's answer
                    # capture (``llm_output`` + ``agent_end``) and the reasoning relay are silently
                    # dropped at load — the owner then gets the subagent's raw internal envelope
                    # instead of the supervisor's answer, and no "Thinking…" panel at all.
                    "hooks": {"allowConversationAccess": True},
                    "config": sellerclaw_ui_plugin_config,
                },
                OPENCLAW_DOCUMENT_EXTRACT_PLUGIN_ID: {"enabled": True},
                **(
                    {web_search_plugin_id: web_search_plugin_entry}
                    if web_search_enabled
                    and web_search_plugin_id is not None
                    and web_search_plugin_entry is not None
                    else {}
                ),
                **({MEM0_PLUGIN_ID: memory_plugin_entry} if memory_enabled and memory_plugin_entry is not None else {}),
            },
        },
        "browser": {
            "enabled": browser_enabled,
            "defaultProfile": "openclaw",
            "headless": False,
            "noSandbox": True,
            "executablePath": "/usr/local/bin/openclaw_chrome",
            # Disable in-page JS execution (browser ``evaluate``). Agents drive pages via
            # snapshot/click/type; running arbitrary JS against untrusted pages is an
            # injection/abuse surface we don't need.
            "evaluateEnabled": False,
            # Capture page snapshots with the compact "efficient" preset (~8k vs ~40-80k
            # chars) to cut token cost on large pages.
            "snapshotDefaults": {"mode": "efficient"},
            "profiles": {},
        },
        "attachments": {
            # Auto-clean persisted media (inbound uploads, browser captures, outbound
            # files) after 7 days so the local media tree doesn't grow unbounded. The block
            # was the root `media` object before OpenClaw 2026.8 renamed it.
            "ttlHours": 168,
        },
        "cron": {
            "enabled": cron_enabled,
            # Redirect cron failure notifications to the cloud error sink instead of
            # announcing "Cron job … failed" as a chat message to the seller. The
            # runtime POSTs the failure payload to ``failureAlert.to`` with
            # ``Authorization: Bearer <webhookToken>``; the cloud authenticates that
            # token via ``get_agent_user_id`` (same agent API key the channel uses).
            "webhookToken": (agent_api_key or "").strip(),
            # OpenClaw 2026.8 folded the retired ``failureDestination`` block into
            # ``failureAlert``, which also owns the alert threshold. Its defaults would
            # change behaviour silently — alerts are off unless ``enabled``, and would then
            # skip the first failure (``after: 2``) and mute repeats for an hour
            # (``cooldownMs``). This sink must see every failed run, so all three are pinned.
            "failureAlert": {
                "enabled": True,
                "after": 1,
                "cooldownMs": 0,
                "mode": "webhook",
                "to": f"{sellerclaw_api_url.strip().rstrip('/')}/internal/openclaw/errors",
            },
        },
        "tools": {
            "web": {
                "fetch": {"enabled": web_fetch_enabled},
                "search": web_search_tools,
            },
            # `full` is the 2026.8 spelling of the retired `security: "full"` + `ask: "off"`
            # pair: run host commands without approval prompts.
            #
            # ``backgroundMs`` is how long a command is held in the foreground before it is
            # detached into a background session the agent then has to poll with the `process`
            # tool. OpenClaw's default is 10s, which is under the normal runtime of the commands
            # this agent actually runs: a CJ catalog lookup, `catalog source-from-supplier` or a
            # storefront publish all sit in the 10-30s band. At 10s each of those turned one turn
            # into three (detach, poll, poll) and pushed agents into guessing `sleep 90` instead.
            # 60s covers them in a single turn; the ceiling OpenClaw accepts is 120s.
            "exec": {"mode": "full", "backgroundMs": 60000},
            "sessions": {"visibility": "all"},
            "agentToAgent": {"enabled": True},
        },
        "skills": {
            "allowBundled": list(OPENCLAW_BUNDLE_ALLOWED_SKILLS),
            # OpenClaw defaults workshop.autonomous.mode to "auto": a daily
            # collection-review session plus post-run self-learning. We pin "off"
            # because per-agent tools.allow does not include skill_workshop, so
            # auto review fails closed and spams gateway health; we also do not
            # want unattended skill rewrites. Manual /learn and Workshop UI still
            # work. "propose" would still spawn background capture runs.
            "workshop": {"autonomous": {"mode": "off"}},
        },
    }
    return json.dumps(config_payload, indent=2)
