import argparse
import json


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(description="Indexer configuration check")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without indexing a chain"
    )
    return parser, parser.parse_args()


def print_configuration(chain_id: int, finality_depth: int, block: int | None) -> None:
    result: dict[str, object] = {
        "role": "indexer",
        "configuration_valid": True,
        "chain_id": chain_id,
        "finality_depth": finality_depth,
        "indexing_enabled": False,
    }
    if block is not None:
        result["deployment_start_block"] = block
    print(json.dumps(result))
