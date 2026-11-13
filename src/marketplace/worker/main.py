import argparse
import json

from marketplace.settings import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker configuration check")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without sending transactions"
    )
    args = parser.parse_args()
    try:
        config = load_settings("worker")
    except ConfigurationError as error:
        parser.exit(2, f"{error}\n")
    if not args.check:
        parser.exit(
            2, "Worker execution is not enabled: deployment, signing, and queue are undecided.\n"
        )
    print(
        json.dumps(
            {
                "role": "worker",
                "configuration_valid": True,
                "chain_id": config.chain_id,
                "transaction_submission_enabled": False,
            }
        )
    )
