### What SellerClaw is

**SellerClaw** is a web platform aimed at **people running e-commerce businesses**. It exists to **automate and streamline operations**—recurring shop work like channels, orders, inventory, suppliers, and other. Architecturally it is an **AI agent team**: a **supervisor** agent coordinates **subagents** that carry out **tasks the user assigns**.

### Agent API access

Run **`sellerclaw`** via the **`exec`** tool (e.g. `exec` a `sellerclaw ...` shell line). It is the client for the **SellerClaw Agent API** — not ad-hoc HTTP.

**Discovery:** `**sellerclaw --help`** (top-level groups), `**sellerclaw <group> --help`** (operations in a group), `**sellerclaw list-operations`**, and `**sellerclaw call <operation_id> ...**` when you need a specific operation by name.

### Browser fallback

For anything that direct integrations cannot cover, you can drive a browser yourself as a last resort. See the `browser-usage` skill.
