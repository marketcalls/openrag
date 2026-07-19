from uuid import UUID

import pytest

from openrag.cli import build_parser


def test_authority_provision_cli_accepts_explicit_generation_and_dimension() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "authority",
            "provision",
            "--generation",
            "260f51ce-8c05-4d87-9579-96da4f27497e",
            "--dense-dimension",
            "768",
        ]
    )

    assert args.command == "authority"
    assert args.authority_command == "provision"
    assert args.generation == UUID("260f51ce-8c05-4d87-9579-96da4f27497e")
    assert args.dense_dimension == 768


@pytest.mark.parametrize("dimension", ["0", "-1"])
def test_authority_provision_cli_rejects_nonpositive_dimension(dimension: str) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "authority",
                "provision",
                "--generation",
                "260f51ce-8c05-4d87-9579-96da4f27497e",
                "--dense-dimension",
                dimension,
            ]
        )
