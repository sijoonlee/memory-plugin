from __future__ import annotations


def build_content_for_embedding(
    *,
    when_useful: str,
    details: str,
    tags: list[str],
) -> str:
    tag_text = ", ".join(tags) if tags else "none"
    return "\n".join(
        [
            f"When useful: {when_useful}",
            f"Details: {details}",
            f"Tags: {tag_text}",
        ]
    )
