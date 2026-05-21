/**
 * Strip OpenClaw runtime tool/subagent activity footers from user-facing text.
 *
 * During block streaming the harness appends compact tool summaries (see
 * `formatToolAggregate` / `formatToolSummary` in OpenClaw's channel-streaming
 * module). Shapes include:
 *   - `🛠️ sellerclaw agent-orders list failed` (exec/bash compact command)
 *   - `🧾 Session History: session agent:marketing:subagent:…, limit 20`
 *   - `🤖 Subagents` (label-only header)
 *
 * SellerClaw's inbound plugin must not forward these lines to the backend.
 */

/** Emoji + title pairs from OpenClaw `TOOL_DISPLAY_CONFIG` (2026.5.20-beta.1). */
const RUNTIME_TOOL_DISPLAY_ENTRIES: ReadonlyArray<readonly [string, string]> = [
  ["🛠️", "Bash"],
  ["🛠️", "Exec"],
  ["🧰", "Process"],
  ["🧰", "Tool Call"],
  ["📖", "Read"],
  ["✍️", "Write"],
  ["📝", "Edit"],
  ["📎", "Attach"],
  ["🌐", "Browser"],
  ["🖼️", "Canvas"],
  ["🖼️", "Image"],
  ["📱", "Nodes"],
  ["⏰", "Cron"],
  ["🗺️", "Update Plan"],
  ["🔌", "Gateway"],
  ["📊", "Session Status"],
  ["🗂️", "Sessions"],
  ["📨", "Session Send"],
  ["🧾", "Session History"],
  ["🧑‍🔧", "Sub-agent"],
  ["🤖", "Subagents"],
  ["🧭", "Agents"],
  ["🧠", "Memory Search"],
  ["📓", "Memory Get"],
  ["🔎", "Web Search"],
  ["📄", "Web Fetch"],
  ["🧮", "Code Execution"],
  ["✉️", "Message"],
  ["🩹", "Apply Patch"],
  ["🎨", "Image Generation"],
  ["🎵", "Music Generation"],
  ["🎬", "Video Generation"],
  ["📑", "PDF"],
  ["⏸️", "Yield"],
  ["🔊", "TTS"],
];

const RUNTIME_TOOL_EMOJIS = [...new Set(RUNTIME_TOOL_DISPLAY_ENTRIES.map(([emoji]) => emoji))];

/** Ported from OpenClaw `extensions/discord/src/monitor/reply-safety.ts`. */
const INTERNAL_TRACE_LINE_RE =
  /^(?:>\s*)?(?:📊|🛠️|📖|📝|🔍|🔎|⚙️)\s*(?:Session Status|Exec|Read|Edit|Write|Patch|Search|Open|Click|Find|Screenshot|Update Plan|Tool Call|Tool Result|Function Call|Shell|Command)\s*:/i;

const INTERNAL_COMPACT_COMMAND_TRACE_LINE_RE =
  /^(?:>\s*)?🛠️\s*(?:(?:(?:elevated|pty)\b\s*(?:·|,)\s*)+)?(?:`{1,2}\s*\S|(?:run|check|fetch|pull|push|view|show|list|switch|create|merge|rebase|stage|restore|reset|stash|search|find|print|copy|move|remove|install|start|cd|git|pnpm|npm|yarn|bun|node|python|python3|bash|sh|sellerclaw)\b)/i;

const INTERNAL_CHANNEL_LINE_RE =
  /^(?:>\s*)?(?:analysis|commentary|tool[-_ ]?call|tool[-_ ]?result|function[-_ ]?call|thinking|reasoning)\s*[:=]/i;

const EXEC_COMPACT_LINE_RE = /^🛠️\s+(?!Exec\b|Bash\b)/u;

const SESSION_KEY_ACTIVITY_RE =
  /^[^\n]*\bsession\s+agent:[^\n]+(?:,\s*limit\s+\d+)?\s*$/i;

function normalizeActivityLine(rawLine: string): string {
  return rawLine.replace(/^\s*>\s*/, "").trim();
}

function matchesToolLabelLine(line: string): boolean {
  for (const [emoji, label] of RUNTIME_TOOL_DISPLAY_ENTRIES) {
    if (line === `${emoji} ${label}`) return true;
    if (line.startsWith(`${emoji} ${label}:`)) return true;
  }
  return false;
}

function matchesEmojiPrefixedActivityLine(line: string): boolean {
  const emoji = RUNTIME_TOOL_EMOJIS.find((candidate) => line.startsWith(`${candidate} `));
  if (!emoji) return false;
  if (matchesToolLabelLine(line)) return true;
  if (emoji === "🛠️" && EXEC_COMPACT_LINE_RE.test(line)) return true;
  return false;
}

/**
 * True when a single line is runtime tool/subagent activity rather than user prose.
 * Exported for unit tests.
 */
export function isRuntimeToolActivityLine(rawLine: string): boolean {
  const line = normalizeActivityLine(rawLine);
  if (!line) return false;
  if (INTERNAL_TRACE_LINE_RE.test(line)) return true;
  if (INTERNAL_COMPACT_COMMAND_TRACE_LINE_RE.test(line)) return true;
  if (INTERNAL_CHANNEL_LINE_RE.test(line)) return true;
  if (matchesToolLabelLine(line)) return true;
  if (matchesEmojiPrefixedActivityLine(line)) return true;
  if (SESSION_KEY_ACTIVITY_RE.test(line)) return true;
  return false;
}

/**
 * Remove runtime tool/subagent activity lines from a deliver payload text chunk.
 * Exported for unit tests.
 */
export function stripRuntimeToolActivityLines(text: string): string {
  if (!text) return text;
  const lines = text.split("\n");
  const kept = lines.filter((line) => !isRuntimeToolActivityLine(line));
  return kept.join("\n");
}
