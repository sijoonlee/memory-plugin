from memory_mcp.core.content import build_content_for_embedding


def test_build_content_for_embedding_includes_main_fields() -> None:
    content = build_content_for_embedding(
        what_happened="The agent edited generated code directly.",
        when_useful="When working on generated SDKs.",
        helpful_explanation="Change the source schema and regenerate.",
        tags=["sdk", "openapi"],
    )

    assert "What happened: The agent edited generated code directly." in content
    assert "When useful: When working on generated SDKs." in content
    assert "Helpful explanation: Change the source schema and regenerate." in content
    assert "Tags: sdk, openapi" in content
