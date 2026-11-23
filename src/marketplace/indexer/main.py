import argparse
import json

from marketplace.settings import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexer configuration check")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without indexing a chain"
    )
    args = parser.parse_args()
    try:
        config = load_settings("indexer")
    except ConfigurationError as error:
        parser.exit(2, f"{error}\n")
    if not args.check:
        parser.exit(
            2, "Indexer execution is not enabled: deployment and reorg policy are undecided.\n"
        )
    print(
        json.dumps(
            {
                "role": "indexer",
                "configuration_valid": True,
                "chain_id": config.chain_id,
                "finality_depth": config.finality_depth,
                "indexing_enabled": False,
            }
        )
    )
