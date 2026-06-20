from memory_mcp.core.content import build_content_for_embedding


def test_build_content_for_embedding_includes_main_fields() -> None:
    content = build_content_for_embedding(
        when_useful="When working on generated SDKs.",
        details="The agent edited generated code directly. Change the source schema and regenerate.",
        tags=["sdk", "openapi"],
    )

    assert "When useful: When working on generated SDKs." in content
    assert (
        "Details: The agent edited generated code directly. "
        "Change the source schema and regenerate." in content
    )
    assert "Tags: sdk, openapi" in content
