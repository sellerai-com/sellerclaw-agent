---
name: file-storage
description: Upload text and binary files to SellerClaw File Storage API and share download links.
---

# File Storage Skill

## Goal
Upload content as files and provide download URLs to the owner or other agents.

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

## Constraints

- Allowed extensions: `.txt`, `.csv`, `.md`, `.json`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`
- Text upload: UTF-8 only via `POST /files/`
- Binary upload: `multipart/form-data` via `POST /files/upload`
- TTL: 168 hours / 7 days (after that the link returns 404)
- Max size: 10 MB
