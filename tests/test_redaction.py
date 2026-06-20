from __future__ import annotations

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.redaction import REDACTED, redact_payload, redact_text
from memory_mcp.core.store import LocalMemoryStore

from conftest import FakeEmbedder

PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Q\n"
    "uKUpRKfFLfRYC9AIKjbJTWit+CqvjSFmbw==\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_redact_text_handles_api_keys_and_bearer_tokens() -> None:
    redacted = redact_text(
        "use sk-ABCDEF0123456789ghij with Authorization: Bearer abc.def.ghi"
    )
    assert "sk-ABCDEF0123456789ghij" not in redacted
    assert "abc.def.ghi" not in redacted
    assert redacted.count(REDACTED) == 2
    assert "Bearer" in redacted


def test_redact_text_strips_private_key_blocks() -> None:
    redacted = redact_text(f"here is the key:\n{PRIVATE_KEY}\nkeep this line")
    assert "PRIVATE KEY" not in redacted
    assert "MIIBOgIBAAJBAKj" not in redacted
    assert "keep this line" in redacted


def test_redact_text_redacts_inline_password_fields() -> None:
    redacted = redact_text("connect with password=hunter2 and token: secrettoken")
    assert "hunter2" not in redacted
    assert "secrettoken" not in redacted
    assert "password=" in redacted


def test_redact_payload_redacts_sensitive_keys_and_nested_values() -> None:
    payload = {
        "prompt": "deploy with sk-ABCDEF0123456789ghij now",
        "api_key": "AKIAIOSFODNN7EXAMPLE",
        "nested": {
            "password": "hunter2",
            "notes": ["safe text", "Bearer abc.def.ghi"],
        },
    }
    redacted = redact_payload(payload)

    assert "sk-ABCDEF0123456789ghij" not in redacted["prompt"]
    assert redacted["api_key"] == REDACTED
    assert redacted["nested"]["password"] == REDACTED
    assert redacted["nested"]["notes"][0] == "safe text"
    assert "abc.def.ghi" not in redacted["nested"]["notes"][1]


def test_append_event_redacts_payload(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    record = store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            payload={
                "prompt": "token=supersecretvalue",
                "authorization": "Bearer abc.def.ghi",
            },
        )
    )

    assert "supersecretvalue" not in record.payload["prompt"]
    assert record.payload["authorization"] == REDACTED

    reloaded = store.list_events()
    assert "supersecretvalue" not in reloaded[0].payload["prompt"]


def test_create_memory_redacts_fields(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    record = store.create_memory(
        MemoryCreate(
            when_useful="When deploying.",
            details=f"leaked sk-ABCDEF0123456789ghij in logs; rotate this:\n{PRIVATE_KEY}",
            tags=["password=hunter2"],
        )
    )

    assert "sk-ABCDEF0123456789ghij" not in record.details
    assert "PRIVATE KEY" not in record.details
    assert "hunter2" not in record.tags[0]

    loaded = store.get_memory(record.id)
    assert loaded is not None
    assert "sk-ABCDEF0123456789ghij" not in loaded.details
    assert "sk-ABCDEF0123456789ghij" not in loaded.content_for_embedding
