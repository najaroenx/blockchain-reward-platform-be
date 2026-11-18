# Operation status

`GET /v1/operations/{operationId}` returns the current status, public transaction
steps, and recorded status events in ascending sequence order. Both operation
routes require a verified access token. The JWT subject must match the operation's
stored actor. A missing operation and one owned by another actor both return 404.

Use `eventSeq` to tell whether the status changed. An operation can move from
`CONFIRMED` to `ORPHANED` and later return to `SUBMITTED`. Send the last `ETag`
as `If-None-Match` to receive 304 when the sequence is unchanged. Use
`Retry-After` to space requests. `FINALIZED` and `CHAIN_REVERTED` are terminal.
Steps become public at submission. The response omits signed transaction bytes,
request payloads, and redeem codes.

`GET /v1/operations` lists the caller's operations, newest first. Filter by
`status` or `kind`. The default `limit` is 20 and the maximum is 100. Pass
`nextCursor` as `cursor` with the same filters to read the next page.

## History coverage

New operations begin at sequence 1 with `fromStatus: null`. The database
requires each status change to have a matching event at commit. It also checks
the predecessor of every event and prevents edits to recorded events or terminal
operations. Migration `0006_operation_history` starts each existing operation
with one snapshot of its stored status. Earlier transitions that lack records
cannot be recovered from the old schema.

Redeem records its admission, submission, and correlated receipt outcome.
`ORPHANED` history can be read and tested locally. Live reorg detection and
finality depend on the deployment and indexer policy. Outbound webhooks are
tracked in [#230](https://github.com/najaroenx/blockchain-reward-platform/issues/230)
and [#238](https://github.com/najaroenx/blockchain-reward-platform/issues/238).

## Verify locally

The Postman suite uses a temporary database and an operation with a recorded
`CONFIRMED` to `ORPHANED` transition.

```bash
.venv/bin/python scripts/verify_operations_api.py --newman newman
```

[OpenAPI specification](operations.openapi.json) · [Postman collection](../../postman/operations.postman_collection.json)
