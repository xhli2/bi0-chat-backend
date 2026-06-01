# Web Search (HTTP Fetch)

1. Confirm the user supplied a full `http://` or `https://` URL.
2. Call `http_search_wrapper` with that URL (host must be on the tenant allowlist).
3. Summarize `body_preview` in plain language; include `status_code` and `fetched_url`.
4. If the host is denied, explain the policy and ask for an allowed source or alternate link.
