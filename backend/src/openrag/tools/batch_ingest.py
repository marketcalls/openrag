"""Stream a bounded multi-file batch through the normal document API."""

import argparse
import asyncio
import json
import mimetypes
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

import httpx

_SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md"})
_MAX_FILES = 10_000
_MAX_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class BatchItemResult:
    filename: str
    size_bytes: int
    status: str
    document_id: str | None
    error_code: str | None
    elapsed_ms: int


def discover_files(inputs: Sequence[Path]) -> tuple[Path, ...]:
    """Resolve regular supported files deterministically without following links."""

    found: dict[Path, None] = {}
    for raw in inputs:
        path = raw.expanduser().resolve(strict=True)
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for candidate in candidates:
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or candidate.suffix.lower() not in _SUPPORTED_SUFFIXES
            ):
                continue
            found[candidate.resolve(strict=True)] = None
            if len(found) > _MAX_FILES:
                raise ValueError("batch_file_limit_exceeded")
    if not found:
        raise ValueError("batch_has_no_supported_files")
    return tuple(sorted(found, key=lambda value: str(value).casefold()))


def validate_batch(files: Sequence[Path], *, max_total_bytes: int) -> int:
    if max_total_bytes < 100 * 1024 * 1024:
        raise ValueError("batch_capacity_below_100_mb")
    total = sum(path.stat().st_size for path in files)
    if total > max_total_bytes:
        raise ValueError("batch_total_size_exceeded")
    return total


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("batch_base_url_invalid")
    return value.rstrip("/")


async def _upload_one(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    workspace_id: UUID,
    token: str,
    path: Path,
    retries: int,
) -> BatchItemResult:
    started = perf_counter()
    response: httpx.Response | None = None
    for attempt in range(retries + 1):
        try:
            with path.open("rb") as source:
                response = await client.post(
                    f"{base_url}/api/v1/workspaces/{workspace_id}/documents",
                    headers={"Authorization": f"Bearer {token}"},
                    files={
                        "file": (
                            path.name,
                            source,
                            mimetypes.guess_type(path.name)[0]
                            or "application/octet-stream",
                        )
                    },
                )
        except httpx.HTTPError:
            response = None
        retryable = response is None or response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt == retries:
            break
        await asyncio.sleep(min(2**attempt, 4))

    elapsed_ms = round((perf_counter() - started) * 1000)
    if response is None:
        return BatchItemResult(
            path.name,
            path.stat().st_size,
            "failed",
            None,
            "network_error",
            elapsed_ms,
        )
    if response.status_code not in {200, 201}:
        return BatchItemResult(
            path.name,
            path.stat().st_size,
            "failed",
            None,
            f"http_{response.status_code}",
            elapsed_ms,
        )
    try:
        document_id = str(UUID(str(response.json()["id"])))
    except (KeyError, TypeError, ValueError):
        return BatchItemResult(
            path.name,
            path.stat().st_size,
            "failed",
            None,
            "invalid_response",
            elapsed_ms,
        )
    return BatchItemResult(
        path.name,
        path.stat().st_size,
        "queued",
        document_id,
        None,
        elapsed_ms,
    )


async def ingest_batch(
    files: Sequence[Path],
    *,
    base_url: str,
    workspace_id: UUID,
    token: str,
    max_total_bytes: int = 500 * 1024 * 1024,
    concurrency: int = 4,
    retries: int = 2,
    client: httpx.AsyncClient | None = None,
) -> tuple[BatchItemResult, ...]:
    """Upload a batch concurrently; parsing/indexing remains durable and async."""

    if not token or len(token) > 8_192:
        raise ValueError("batch_token_invalid")
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        raise ValueError("batch_concurrency_invalid")
    if not 0 <= retries <= 5:
        raise ValueError("batch_retries_invalid")
    normalized_url = _validate_base_url(base_url)
    validate_batch(files, max_total_bytes=max_total_bytes)
    semaphore = asyncio.Semaphore(concurrency)
    owned_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    async def upload(path: Path) -> BatchItemResult:
        async with semaphore:
            return await _upload_one(
                active_client,
                base_url=normalized_url,
                workspace_id=workspace_id,
                token=token,
                path=path,
                retries=retries,
            )

    try:
        return tuple(await asyncio.gather(*(upload(path) for path in files)))
    finally:
        if owned_client:
            await active_client.aclose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openrag-batch-ingest")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workspace-id", required=True, type=UUID)
    parser.add_argument("--token-env", default="OPENRAG_BATCH_TOKEN")
    parser.add_argument("--max-total-mb", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("inputs", nargs="+", type=Path)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get(args.token_env, "")
    try:
        files = discover_files(args.inputs)
        results = asyncio.run(
            ingest_batch(
                files,
                base_url=args.base_url,
                workspace_id=args.workspace_id,
                token=token,
                max_total_bytes=args.max_total_mb * 1024 * 1024,
                concurrency=args.concurrency,
                retries=args.retries,
            )
        )
    except (OSError, ValueError):
        print('{"status":"failed","error_code":"BATCH_VALIDATION_FAILED"}', file=sys.stderr)
        return 2
    payload = {
        "status": "completed",
        "queued": sum(item.status == "queued" for item in results),
        "failed": sum(item.status == "failed" for item in results),
        "items": [asdict(item) for item in results],
    }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 1 if payload["failed"] else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
