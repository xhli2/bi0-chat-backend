# Session Recall

1. When the user refers to earlier turns, call `session_lookup` first.
2. Use `summarize_chunk` to compress long excerpts before quoting them back.
3. Distinguish session facts from new inference; cite message roles when helpful.
