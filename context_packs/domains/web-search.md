# Web Fetch Policy

- Only fetch URLs explicitly provided by the user or cited in the task.
- Respect tenant HTTP host allowlists; never probe internal or metadata endpoints.
- Summarize fetched content; do not dump raw HTML unless asked.
