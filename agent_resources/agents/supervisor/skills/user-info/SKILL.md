---

## name: user-info
description: "Profile and agent settings for the current sellerclaw token — get-agent-me, get-agent-settings; optional list-agent-integrations."

## What this covers

**Who** the token represents and **how** SellerClaw has configured the agent for that user.

## Commands

**Profile** — identity behind the current token:

```bash
sellerclaw agent-context get-agent-me
```

**Agent config** — what the backend exposes for this user’s agent (masked sensitive fields):

```bash
sellerclaw agent-context get-agent-settings
```

**Connected integration groups** (stores, ads, etc.) — overview of groups and connections; **not** the profile and **not** sales-channel records (use `**sales-channels`** for channel UUIDs):

```bash
sellerclaw agent-context list-agent-integrations
```

Parse machine output from `**data**` in the JSON on stdout. Auth: `**agents.md**`.

## Flow

1. Need **who / language** → `**get-agent-me`**.
2. Need **agent setup** → `**get-agent-settings`**.
3. Need **what modules exist / connection counts** → `**list-agent-integrations`**.
