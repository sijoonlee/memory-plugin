from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from memory_mcp.core.events import MemoryCandidateCreate
from memory_mcp.review.service import (
    CandidateFilters,
    CandidateReviewService,
    CandidateUpdate,
)


def create_app(root: Path | str = Path(".memory-mcp")) -> Starlette:
    service = CandidateReviewService.from_root(root)
    static_dir = files("memory_mcp.review").joinpath("static")

    async def index(request: Request) -> Response:
        html = static_dir.joinpath("index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True})

    async def list_candidates(request: Request) -> Response:
        filters = CandidateFilters(
            status=_optional_query(request, "status", default="pending_review"),
            project=_optional_query(request, "project"),
            category=_optional_query(request, "category"),
            min_confidence=_optional_float_query(request, "min_confidence"),
            created_from=_optional_query(request, "created_from"),
            created_to=_optional_query(request, "created_to"),
        )
        candidates = service.list_candidates(filters=filters)
        return _json(
            {
                "candidates": [
                    candidate.model_dump(mode="json") for candidate in candidates
                ]
            }
        )

    async def list_memories(request: Request) -> Response:
        memories = service.list_active_memories()
        return _json(
            {"memories": [memory.model_dump(mode="json") for memory in memories]}
        )

    async def get_memory(request: Request) -> Response:
        memory_id = request.path_params["memory_id"]
        memory = service.get_memory_detail(memory_id)
        return _json({"memory": memory.model_dump(mode="json")})

    async def get_candidate(request: Request) -> Response:
        candidate_id = request.path_params["candidate_id"]
        include_segment_events = (
            request.query_params.get("include_segment_events", "false").lower()
            == "true"
        )
        detail = service.get_candidate_detail(
            candidate_id,
            include_segment_events=include_segment_events,
        )
        return _json(detail.model_dump(mode="json"))

    async def update_candidate(request: Request) -> Response:
        candidate_id = request.path_params["candidate_id"]
        update = CandidateUpdate.model_validate(await _json_body(request))
        candidate = service.update_candidate(candidate_id, update)
        return _json({"candidate": candidate.model_dump(mode="json")})

    async def approve_candidate(request: Request) -> Response:
        candidate_id = request.path_params["candidate_id"]
        body = await _json_body(request)
        update = CandidateUpdate.model_validate(body.get("update", {}))
        candidate, memory = service.approve_candidate(candidate_id, update=update)
        return _json(
            {
                "candidate": candidate.model_dump(mode="json"),
                "memory": memory.model_dump(mode="json"),
            }
        )

    async def reject_candidate(request: Request) -> Response:
        candidate_id = request.path_params["candidate_id"]
        body = await _json_body(request)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return _json({"error": "reason is required"}, status_code=400)
        candidate = service.reject_candidate(candidate_id, reason=reason)
        return _json({"candidate": candidate.model_dump(mode="json")})

    async def merge_candidates(request: Request) -> Response:
        body = await _json_body(request)
        source_ids = body.get("source_ids") or []
        merged = MemoryCandidateCreate.model_validate(body.get("merged", {}))
        candidate = service.merge_candidates(source_ids, merged)
        return _json({"candidate": candidate.model_dump(mode="json")})

    async def archive_candidate(request: Request) -> Response:
        candidate_id = request.path_params["candidate_id"]
        candidate = service.archive_candidate(candidate_id)
        return _json({"candidate": candidate.model_dump(mode="json")})

    async def retry_segment(request: Request) -> Response:
        segment_id = request.path_params["segment_id"]
        segment = service.retry_segment(segment_id)
        return _json({"segment": segment.model_dump(mode="json")})

    app = Starlette(
        debug=False,
        routes=[
            Route("/", index, methods=["GET"]),
            Route("/api/health", health, methods=["GET"]),
            Route("/api/candidates", _handle_errors(list_candidates), methods=["GET"]),
            Route(
                "/api/candidates/merge",
                _handle_errors(merge_candidates),
                methods=["POST"],
            ),
            Route("/api/memories", _handle_errors(list_memories), methods=["GET"]),
            Route(
                "/api/memories/{memory_id}",
                _handle_errors(get_memory),
                methods=["GET"],
            ),
            Route(
                "/api/candidates/{candidate_id}",
                _handle_errors(get_candidate),
                methods=["GET"],
            ),
            Route(
                "/api/candidates/{candidate_id}",
                _handle_errors(update_candidate),
                methods=["PATCH"],
            ),
            Route(
                "/api/candidates/{candidate_id}/approve",
                _handle_errors(approve_candidate),
                methods=["POST"],
            ),
            Route(
                "/api/candidates/{candidate_id}/reject",
                _handle_errors(reject_candidate),
                methods=["POST"],
            ),
            Route(
                "/api/candidates/{candidate_id}/archive",
                _handle_errors(archive_candidate),
                methods=["POST"],
            ),
            Route(
                "/api/segments/{segment_id}/retry",
                _handle_errors(retry_segment),
                methods=["POST"],
            ),
            Mount(
                "/static",
                StaticFiles(directory=str(static_dir)),
                name="static",
            ),
        ],
    )
    return app


def _handle_errors(handler: Any) -> Any:
    async def wrapped(request: Request) -> Response:
        try:
            return await handler(request)
        except ValidationError as exc:
            return _json(
                {"error": "validation failed", "details": exc.errors()},
                status_code=422,
            )
        except ValueError as exc:
            return _json({"error": str(exc)}, status_code=400)

    return wrapped


async def _json_body(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body:
        return {}
    return json.loads(body)


def _json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


def _optional_query(
    request: Request,
    name: str,
    *,
    default: str | None = None,
) -> str | None:
    value = request.query_params.get(name, default)
    if value is None or value == "":
        return None
    return value


def _optional_float_query(request: Request, name: str) -> float | None:
    value = _optional_query(request, name)
    if value is None:
        return None
    return float(value)
