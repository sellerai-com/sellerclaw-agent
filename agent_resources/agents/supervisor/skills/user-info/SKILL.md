---
name: user-info
description: "Look up who the owner is, their preferences, and which external accounts are connected. Use when the user says \"who am I\", \"what's my workspace\", \"show my settings\", \"which integrations are connected\", \"do I have a Shopify / supplier / ads account linked\", or whenever another task needs the owner's profile or connection state."
---

## What this covers

**Who** the token represents and **how** SellerClaw has configured the agent for that user.

## Commands

**Profile** — identity behind the current token:

```bash
sellerclaw agent-context get-me
```

**Agent config** — what the backend exposes for this user’s agent (masked sensitive fields):

```bash
sellerclaw agent-context get-settings
```

**Connected integration groups** (stores, ads, etc.) — overview of groups and connections; **not** the profile and **not** sales-channel records (use `sales-channels` for channel UUIDs):

```bash
sellerclaw agent-context list-integrations
```

Parse machine output from `data` in the JSON on stdout. Auth: `agents.md`.

## Flow

1. Need **who / language** → `get-me`.
2. Need **agent setup** → `get-settings`.
3. Need **what modules exist / connection counts** → `list-integrations`.
