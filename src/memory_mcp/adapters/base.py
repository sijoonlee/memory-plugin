from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from memory_mcp.core.events import EventCreate
from memory_mcp.core.projects import resolve_project


def fallback_session_id(source: str, project: str) -> str:
    """Build a stable session id when an adapter payload exposes none.

    The fallback stays scoped to the source adapter and project so unrelated
    sessions are not merged together during sessionization.
    """

    name = Path(project).name or "root"
    return f"{source}:{name}"


class BaseAdapter:
    """Normalize an agent/runtime lifecycle payload into an ``EventCreate``.

    Subclasses only override the ``extract_*`` hooks that know how to read a
    specific agent's payload shape. Identifier fallbacks live here so every
    adapter resolves project/session ids the same way.
    """

    source: str = "generic"

    def extract_project(self, payload: dict[str, Any]) -> str | None:
        return None

    def extract_session_id(self, payload: dict[str, Any]) -> str | None:
        return None

    def extract_run_id(self, payload: dict[str, Any]) -> str | None:
        return None

    def normalize(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        project: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> EventCreate:
        # Normalize to the repo root so the same project keys consistently,
        # whatever subfolder/cwd the event was captured from.
        resolved_project = resolve_project(
            project or self.extract_project(payload) or os.getcwd()
        )
        resolved_session = (
            session_id
            or self.extract_session_id(payload)
            or fallback_session_id(self.source, resolved_project)
        )
        resolved_run = run_id or self.extract_run_id(payload)
        return EventCreate(
            event_type=event_type,
            source=self.source,
            project=resolved_project,
            session_id=resolved_session,
            run_id=resolved_run,
            payload=payload,
        )


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string value among ``keys`` in ``payload``."""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
