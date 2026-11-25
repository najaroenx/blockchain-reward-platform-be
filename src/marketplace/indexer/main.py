from marketplace.indexer.application.interfaces.deployments import DeploymentUnavailable
from marketplace.indexer.application.use_cases.check_deployment import CheckDeployment
from marketplace.indexer.domain.exceptions import DeploymentChainMismatch
from marketplace.indexer.infrastructure.deployment_file import FileDeploymentReader
from marketplace.indexer.presentation.cli import parse_args, print_configuration
from marketplace.settings import ConfigurationError, load_settings


def main() -> None:
    parser, args = parse_args()
    try:
        config = load_settings("indexer")
    except ConfigurationError as error:
        parser.exit(2, f"{error}\n")
    if not args.check:
        parser.exit(
            2, "Indexer execution is not enabled: deployment and reorg policy are undecided.\n"
        )
    block = None
    if config.deployment_file is not None:
        check = CheckDeployment(FileDeploymentReader(config.deployment_file))
        try:
            block = check.start_block(config.chain_id)
        except DeploymentUnavailable:
            parser.exit(2, "DEPLOYMENT_FILE must contain a ready deployment record\n")
        except DeploymentChainMismatch:
            parser.exit(2, "DEPLOYMENT_FILE chainId must match CHAIN_ID\n")
    print_configuration(config.chain_id, config.finality_depth, block)
