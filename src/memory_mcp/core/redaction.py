"""Secret redaction for inbound text and JSON-like payloads.

Redaction runs at two chokepoints:

- ``EventStore.append_event`` redacts event payloads before they reach
  ``events.sqlite``, which covers everything the pipeline later derives.
- ``LocalMemoryStore.create_memory`` redacts memory fields before embedding or
  storage, which covers the direct ``memory_create`` path and approved
  candidates.

The goal is best-effort scrubbing of common credential shapes, not a complete
secret scanner. Two complementary mechanisms are used:

- key-based: dict values under a sensitive-looking key are dropped wholesale.
- pattern-based: known secret shapes are matched inside any string.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Dict keys whose value should be redacted regardless of its shape.
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|authorization|auth[_-]?token|private[_-]?key|"
    r"refresh[_-]?token|session[_-]?token|credential)",
    re.IGNORECASE,
)

# PEM private key blocks, e.g. -----BEGIN RSA PRIVATE KEY----- ... -----END ...
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Authorization bearer tokens; keep the scheme, drop the credential.
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]+", re.IGNORECASE)

# Standalone token shapes that are unambiguous on their own.
_TOKEN_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # OpenAI-style keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack tokens
)

# Inline "key = value" / "key: value" secrets in free text. Keeps the key and
# separator so the redaction is legible, drops the value.
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|auth[_-]?token)\s*[:=]\s*)[\"']?([^\s,;\"'}]+)"
)


def redact_text(text: str) -> str:
    """Scrub known secret shapes from a single string."""

    if not text:
        return text
    redacted = _PRIVATE_KEY_RE.sub(REDACTED, text)
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
    for pattern in _TOKEN_RES:
        redacted = pattern.sub(REDACTED, redacted)
    redacted = _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-like value (str / dict / list / scalar)."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact an event payload object."""

    return redact_value(payload)


def _is_sensitive_key(key: Any) -> bool:
    return isinstance(key, str) and _SENSITIVE_KEY_RE.search(key) is not None
