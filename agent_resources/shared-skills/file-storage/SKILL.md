---
name: file-storage
description: "Deliver agent-produced artifacts (screenshots, reports, exports, in-memory blobs) to the user as an HTTPS `download_url`. Two paths: pass a local container path to the `message` tool's `imagePath`/`mediaUrl` for inline auto-upload (default for `browser` screenshots and anything already on disk), or run `sellerclaw agent-files upload|from-url` to mint a URL you need outside the current `message.send` — handing to another agent, embedding in markdown, persisting. A bare local path is never visible to the user."
---

# File Storage Skill

## Goal

Any artifact you produce reaches the user only as an HTTPS `download_url`. A bare local path on the container (e.g. `/home/node/.openclaw/media/...`) is invisible to the user.

## Path 1 — auto-upload via the `message` tool (preferred for files on disk)

The `message` tool uploads a local path through the internal proxy and forwards the resulting HTTPS URL in one call:

```
message.send(
  text="Here is the page.",
  imagePath="/home/node/.openclaw/media/browser/<uuid>.jpg"
)
```

Absolute `/...` or `file://...` paths are also accepted via `imageUrl`, `mediaUrl`, or `localImagePath`. Use this whenever the file already exists on disk — no token, no `exec`, no extra step.

## Path 2 — mint a `download_url` via `sellerclaw agent-files`

Use when you need the URL for something other than the current `message.send`: passing to another agent, embedding in markdown, persisting, or starting from a remote URL. All commands return a `CreateFileResponse` JSON with `download_url`.

Local file (binary or text) — multipart upload:

```bash
sellerclaw agent-files upload /tmp/report.csv
sellerclaw agent-files upload /tmp/raw.bin --filename inventory.json
```

Remote URL — server-side fetch into storage:

```bash
sellerclaw agent-files from-url --url https://example.com/sheet.csv
sellerclaw agent-files from-url --url https://... --filename custom.csv
```

List existing user files:

```bash
sellerclaw agent-files list --limit 20
```

For in-memory content, write it to a local path first, then `agent-files upload`. Re-check any subcommand with `sellerclaw agent-files <cmd> --help`.

Response shape (all three create commands):

```json
{"file_id":"...","filename":"report.csv","content_type":"text/csv","size_bytes":123,"download_url":"https://.../files/<id>/report.csv","expires_at":"..."}
```

## Delivering the URL to the user

Inline image preview (`.png` / `.jpg` / `.jpeg` / `.webp` / `.gif`):

```
message.send(text="...", mediaUrls=["<download_url>"])
```

Markdown link for any file type:

```
Report is ready: [weekly-sales.csv](<download_url>)
```

Local paths in `mediaUrls` are stripped by the runtime — only HTTPS URLs reach the user. Never claim "file sent" without actually passing `imagePath` (Path 1) or an HTTPS `download_url` (Path 2) in the same reply.

## Constraints

- Extensions: `.txt`, `.csv`, `.md`, `.json`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`
- TTL: 7 days (link 404s after that)
- Max size: 10 MB
