from __future__ import annotations

from memory_mcp.core.models import MEMORY_TYPES, MemoryRecord
from memory_mcp.core.store import LocalMemoryStore

# Layer-1 "memory on demand" catalog (M20): a compact ``when_useful -> id`` list
# the host injects so the agent knows what memories exist and can pull full
# detail with ``memory_get(id)``. It is a read-time view over ``list_memories``
# (no storage, recomputed each time the M21 hook asks) — see plan-2.md.

CATALOG_DEFAULT_LIMIT = 5
# Soft ceiling on catalog size. The budget is expressed in words because we have
# no tokenizer here; ~1500 words ≈ 2000 tokens at the usual ~0.75 words/token.
CATALOG_DEFAULT_MAX_WORDS = 1500

# Display order of the type groups; also the only types shown (untyped memories
# are excluded — untyped is no longer a valid created state, see M19).
_TYPE_ORDER: tuple[str, ...] = MEMORY_TYPES

# Keep each catalog line to a single, scannable cue.
_MAX_CUE_CHARS = 200


def select_catalog_memories(
    store: LocalMemoryStore,
    *,
    project: str | None = None,
    limit: int = CATALOG_DEFAULT_LIMIT,
    max_words: int = CATALOG_DEFAULT_MAX_WORDS,
) -> list[MemoryRecord]:
    """Pick the memories that belong in the catalog, highest score first.

    Scoping is inclusive (mirrors ``search_memories``): when ``project`` is given,
    the repo's own memories plus global (project-less) ones are eligible; other
    repos are excluded. ``project=None`` considers every active memory. Untyped
    memories are dropped. The result is bounded by both ``limit`` and a running
    ``max_words`` budget — whichever binds first.
    """

    eligible = [
        memory
        for memory in store.list_memories(status="active")
        if memory.memory_type in _TYPE_ORDER
        and (project is None or memory.project in (project, None))
    ]
    eligible.sort(key=lambda memory: memory.score, reverse=True)

    selected: list[MemoryRecord] = []
    words = 0
    for memory in eligible:
        if len(selected) >= limit:
            break
        line_words = _count_words(_catalog_line(memory))
        if selected and words + line_words > max_words:
            break
        selected.append(memory)
        words += line_words
    return selected


def render_catalog(
    store: LocalMemoryStore,
    *,
    project: str | None = None,
    limit: int = CATALOG_DEFAULT_LIMIT,
    max_words: int = CATALOG_DEFAULT_MAX_WORDS,
) -> str:
    """Render the catalog block, grouped by ``memory_type``.

    Returns an empty string when nothing qualifies, so a hook can emit it
    verbatim and inject nothing into an empty store's session.
    """

    memories = select_catalog_memories(
        store, project=project, limit=limit, max_words=max_words
    )
    if not memories:
        return ""

    open_tag = (
        f'<memory-catalog project="{project}">' if project else "<memory-catalog>"
    )
    lines = [open_tag]
    for memory_type in _TYPE_ORDER:
        group = [memory for memory in memories if memory.memory_type == memory_type]
        if not group:
            continue
        lines.append(f"[{memory_type}]")
        lines.extend(_catalog_line(memory) for memory in group)
    lines.append("</memory-catalog>")
    lines.append(
        "Call memory_get(<id>) to read the full memory when a line looks relevant."
    )
    return "\n".join(lines)


def _catalog_line(memory: MemoryRecord) -> str:
    return f"- {memory.id}  {_cue(memory.when_useful)}"


def _cue(when_useful: str) -> str:
    """First line of ``when_useful``, trimmed to a single scannable cue."""

    first_line = when_useful.strip().splitlines()[0].strip() if when_useful.strip() else ""
    if len(first_line) > _MAX_CUE_CHARS:
        return first_line[: _MAX_CUE_CHARS - 1].rstrip() + "…"
    return first_line


def _count_words(text: str) -> int:
    return len(text.split())
