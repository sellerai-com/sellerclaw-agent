---
name: file-storage
description: Upload text and binary files to SellerClaw File Storage API and share download links with the user or other agents.
---

# File Storage Skill

## Goal
Upload content as files and provide download URLs to the user (owner) or other agents.

**Rule of thumb:** whenever you produce an artifact (screenshot, report, export, chart,
rendered document) that you want the user to actually *see* or *download* — you MUST
upload it first and share the `download_url`. A local path on disk (e.g. the `mediaUrl`
returned by the `browser` tool) is **never** visible to the user on its own.

## Text Upload

```bash
curl -s -X POST "{{api_base_url}}/files/" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"filename": "report.csv", "content": "col1,col2\\nval1,val2\\n"}'
```

Response: `{"file_id":"...","filename":"report.csv","content_type":"text/csv","size_bytes":123,"download_url":"{{api_base_url}}/files/{id}/report.csv","expires_at":"..."}`.

## Binary Upload (images)

```bash
curl -s -X POST "{{api_base_url}}/files/upload" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -F "file=@/path/to/image.png"
```

Response: same payload shape as text upload (including `content_type` and `size_bytes`).

## Delivering the file to the user

After upload, the response contains `download_url` — a public HTTPS link valid for 7 days.
Use it in one of two ways:

### 1. Attach as image (preview renders inline in chat)

For images (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`). The `message` tool accepts a
media URL via any of these param names: `mediaUrls`, `imageUrls`, `imageUrl`, `mediaUrl`.
Pass the `download_url` you just received:

```
message.send(
  text="Here is the screenshot you asked for.",
  mediaUrls=["{download_url from response above}"]
)
```

Only HTTPS URLs are forwarded — local file paths are stripped by the runtime and will
not reach the user.

### 2. Inline markdown link (for any file type)

Works for all extensions (CSV, JSON, MD, images, …). Put the link in the message body:

```
Report is ready: [weekly-sales.csv]({download_url})
```

## Delivering a browser screenshot — end-to-end

The `browser` tool's `screenshot` / `snapshot` action returns
`result.media.mediaUrl` = a **local path inside the container** (e.g.
`/home/node/.openclaw/media/browser/<uuid>.jpg`). That path is NOT reachable by the user.

Canonical flow:

1. Call `browser` to take the screenshot → copy `result.media.mediaUrl` (local path).
2. Upload it:
   ```bash
   curl -s -X POST "{{api_base_url}}/files/upload" \
     -H "Authorization: Bearer $AGENT_API_KEY" \
     -F "file=@${mediaUrl}"
   ```
   → response contains `download_url`.
3. Send to user via `message.send(text=..., mediaUrls=[download_url])`.

**Never claim "screenshot sent" / "file attached" without having actually called step 3
with an HTTPS `download_url` in `mediaUrls` (or an explicit markdown link in the text).**
A plain text "done" without the URL means the user sees nothing.

## Constraints

- Allowed extensions: `.txt`, `.csv`, `.md`, `.json`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`
- Text upload: UTF-8 only via `POST /files/`
- Binary upload: `multipart/form-data` via `POST /files/upload`
- TTL: 168 hours / 7 days (after that the link returns 404)
- Max size: 10 MB
