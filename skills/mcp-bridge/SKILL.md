# MCP Bridge

1. Prefer MCP tools when the user asks for plugin/external-tool behavior.
2. Call `mcp_proxy_call` with the configured server name and tool name.
3. Return a short summary of the MCP response; redact secrets if present.
