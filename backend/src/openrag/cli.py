"""Bounded operational commands for OpenRAG deployment workflows."""

import argparse
import asyncio
import json
import sys
from uuid import UUID

from qdrant_client import AsyncQdrantClient

from openrag.core.config import get_settings
from openrag.modules.documents.authority_storage import (
    AuthorityCollectionSpec,
    AuthorityStorageMismatch,
    provision_authority_storage,
)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the closed command tree without performing external I/O."""

    settings = get_settings()
    parser = argparse.ArgumentParser(prog="openrag")
    commands = parser.add_subparsers(dest="command", required=True)
    authority = commands.add_parser("authority")
    authority_commands = authority.add_subparsers(
        dest="authority_command",
        required=True,
    )
    provision = authority_commands.add_parser("provision")
    provision.add_argument(
        "--generation",
        type=UUID,
        default=settings.authority_generation_id,
    )
    provision.add_argument(
        "--dense-dimension",
        type=_positive_int,
        default=settings.embedding_dim,
    )
    return parser


async def _provision(generation: UUID, dense_dimension: int) -> dict[str, object]:
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant_url)
    try:
        status = await provision_authority_storage(
            AuthorityCollectionSpec(
                generation_id=generation,
                dense_dimension=dense_dimension,
            ),
            client=client,
        )
        return {
            "status": "ready",
            "physical_collection": status.physical_collection,
            "generation_id": str(generation),
        }
    finally:
        await client.close()


def run(argv: list[str] | None = None) -> int:
    """Execute one command and return a process-safe exit code."""

    args = build_parser().parse_args(argv)
    if args.command != "authority" or args.authority_command != "provision":
        raise RuntimeError("unsupported command")
    try:
        result = asyncio.run(_provision(args.generation, args.dense_dimension))
    except AuthorityStorageMismatch:
        print('{"status":"failed","error_code":"AUTHORITY_SCHEMA_MISMATCH"}', file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - operational boundary emits only a safe code
        print('{"status":"failed","error_code":"AUTHORITY_STORAGE_UNAVAILABLE"}', file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
