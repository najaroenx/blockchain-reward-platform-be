import argparse
import json


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(description="Worker runtime and configuration check")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without sending transactions"
    )
    return parser, parser.parse_args()


def print_configuration(chain_id: int) -> None:
    print(
        json.dumps(
            {
                "role": "worker",
                "configuration_valid": True,
                "chain_id": chain_id,
                "transaction_submission_enabled": False,
            }
        )
    )
