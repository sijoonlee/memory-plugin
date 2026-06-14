from __future__ import annotations


def build_content_for_embedding(
    *,
    what_happened: str,
    when_useful: str,
    helpful_explanation: str,
    tags: list[str],
) -> str:
    tag_text = ", ".join(tags) if tags else "none"
    return "\n".join(
        [
            f"What happened: {what_happened}",
            f"When useful: {when_useful}",
            f"Helpful explanation: {helpful_explanation}",
            f"Tags: {tag_text}",
        ]
    )
