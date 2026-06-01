from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _load_tiktoken():
    try:
        import tiktoken  # type: ignore

        return tiktoken
    except Exception:
        return None


def estimate_tokens_for_model(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    tiktoken = _load_tiktoken()
    if tiktoken is None:
        return max(1, len(text) // 4)
    try:
        if model:
            encoding = tiktoken.encoding_for_model(model)
        else:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return max(1, len(text) // 4)
