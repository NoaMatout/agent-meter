"""Reading the usage counters, whatever the provider calls them.

OpenAI-compatible APIs, DeepSeek and Anthropic all return a `usage` object, but
they name different fields and they do not count the same thing. This module
maps all three onto one model.

Two traps are handled here, both met in production:

1. The `usage` object contains nested objects. A naive regular expression stops
   at the first closing brace and returns nothing for calls that were billed.
   So we match braces properly.
2. When streaming, Anthropic spreads usage across several events: input tokens
   arrive first, output tokens last. Taking the last object seen loses the
   input. So we merge them.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

_USAGE_START = re.compile(r'"usage"\s*:\s*\{')


@dataclass
class Usage:
    """What one call consumed, in tokens.

    input_total  every input token, cached ones included.
    cache_read   served from cache, billed at the reduced rate.
    cache_write  written to cache. Zero for most providers.
    input_fresh  input_total minus cache_read, billed at the full rate.
    output       tokens produced.
    """

    input_total: int = 0
    cache_read: int = 0
    cache_write: int = 0
    input_fresh: int = 0
    output: int = 0

    def is_empty(self) -> bool:
        return not (self.input_total or self.output or self.cache_read or self.cache_write)

    def dict(self) -> dict:
        return asdict(self)


def _usage_objects(text: str) -> list[dict]:
    """Return every `usage` object in the text, matching braces."""
    found = []
    for m in _USAGE_START.finditer(text):
        start = m.end() - 1
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        found.append(json.loads(text[start:i + 1]))
                    except ValueError:
                        pass
                    break
    return found


def _count(value) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def normalise(raw: dict) -> Usage:
    """Map one provider's usage object onto the shared model."""
    details = raw.get("prompt_tokens_details") or {}

    cache_read = _count(
        raw.get("prompt_cache_hit_tokens")
        or details.get("cached_tokens")
        or raw.get("cache_read_input_tokens")
    )
    cache_write = _count(raw.get("cache_creation_input_tokens"))

    if "prompt_tokens" in raw:
        # OpenAI and compatibles: prompt_tokens already includes cached tokens.
        input_total = _count(raw.get("prompt_tokens"))
    elif "input_tokens" in raw:
        # Anthropic: input_tokens excludes cached tokens, so they must be added.
        input_total = _count(raw.get("input_tokens")) + cache_read + cache_write
    else:
        input_total = 0

    output = _count(raw.get("completion_tokens") or raw.get("output_tokens"))

    miss = raw.get("prompt_cache_miss_tokens")
    input_fresh = _count(miss) if miss is not None else max(0, input_total - cache_read)

    return Usage(input_total, cache_read, cache_write, input_fresh, output)


def merge(parts: list[Usage]) -> Usage | None:
    """Merge the usage reported across one call.

    While streaming, a provider may announce input at the start and output at
    the end. We keep the maximum field by field: a partial event must never
    erase a value already known.
    """
    parts = [p for p in parts if not p.is_empty()]
    if not parts:
        return None
    total = Usage()
    for p in parts:
        total.input_total = max(total.input_total, p.input_total)
        total.cache_read = max(total.cache_read, p.cache_read)
        total.cache_write = max(total.cache_write, p.cache_write)
        total.input_fresh = max(total.input_fresh, p.input_fresh)
        total.output = max(total.output, p.output)
    return total


def extract(body: bytes) -> Usage | None:
    """Pull the usage out of a response, streamed or not."""
    if not body:
        return None
    return merge([normalise(o) for o in _usage_objects(body.decode("utf-8", "ignore"))])
