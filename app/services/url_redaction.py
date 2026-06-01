from __future__ import annotations

from urllib.parse import urlparse


def redact_provider_base_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    host = parsed.hostname or ""
    if not host:
        return None
    port_part = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port_part}"
