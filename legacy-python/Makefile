PYTHON ?= python3.12

.PHONY: install env migrate api check worker-check indexer-check contracts-check

install:
	$(PYTHON) scripts/bootstrap.py

env:
	.venv/bin/reward-env-init

migrate:
	.venv/bin/alembic upgrade head

api:
	.venv/bin/reward-api

check:
	.venv/bin/python scripts/verify.py

worker-check:
	.venv/bin/reward-worker --check

indexer-check:
	.venv/bin/reward-indexer --check

contracts-check:
	.venv/bin/python scripts/sync_contract_reference.py --check
