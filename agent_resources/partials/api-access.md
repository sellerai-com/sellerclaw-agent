## API access

The SellerClaw system provides a REST API for performing various tasks. The API runs as
a separate service. Every request must include an authorization header with a permanent token.

- **Base URL**: `{{api_base_url}}`
- **Auth header**: `Authorization: Bearer $AGENT_API_KEY` (include in every request)
- **Tool**: use `exec curl` for all HTTP requests
