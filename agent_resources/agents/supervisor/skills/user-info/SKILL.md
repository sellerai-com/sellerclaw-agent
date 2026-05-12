---
name: user-info
description: "Look up the owner's profile, agent configuration, and connected integration groups (suppliers, ad accounts, etc.)."
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

Parse machine output from `data` in the JSON on stdout.

## Flow

1. Need **who / language** → `get-me`.
2. Need **agent setup** → `get-settings`.
3. Need **what modules exist / connection counts** → `list-integrations`.
