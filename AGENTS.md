# Issue validation before implementation

- Before implementing any issue, read the issue and compare its requirements with the actual smart contract implementation, interfaces/ABI, and relevant tests. Reference packs and issue examples may be outdated.
- Verify function inputs and outputs, caller/signing identity, wallet authorization, state transitions, emitted events, and whether settlement is atomic within one transaction or requires separate transactions.
- Report discrepancies with source file references before defining the backend scope or presenting request/response examples as the intended API. Distinguish issue examples from a verified API design.
- Keep contract-owned business logic in the contract. Define backend authentication, wallet-to-user authorization, transaction submission, idempotency, error mapping, and indexing around the verified contract behavior.
- If the intended contract version or deployment cannot be verified, state that explicitly and resolve it before implementing dependent behavior.

## Contract source to check

- Local candidate: `../blockchain-coupon-marketplace-loyalty`; remote: `corp-ais/blockchain-coupon-marketplace-loyalty`. Confirm the relevant commit and intended deployment for each task.
- Coupon entry point: `contracts/Coupon.sol`; implementation: `contracts/abstracts/BaseCoupon.sol`; interface: `contracts/interfaces/ICoupon.sol`.
- For redemption, also inspect the marketplace callback and vault settlement. Do not assume the issue's separate burn/release workflow matches the implementation.

## Feature-based development

Use Feature-Based Clean Architecture. Keep feature code in `src/marketplace/<feature_name>/` and give each new feature the four layers below. Use the [README development guidelines](README.md#development-guidelines) for the folder template and examples.

- `domain/` owns entities and backend-owned rules. Keep it in pure Python. Validate invariants and state transitions inside the entity. Keep database access, network calls, and framework imports in the outer layers.
- `application/` owns orchestration in `use_cases/` and ports in `interfaces/`. Declare ports with `abc.ABC` and `@abstractmethod`. Accept ports through constructors and ask the Domain to evaluate business rules. Use Domain or Application types in port signatures.
- `infrastructure/` owns ORM models, queries, and external adapters. Repositories subclass their ports explicitly. Apply the same rule to other adapters that implement ports. Map ORM rows to Domain entities or Application DTOs at the boundary.
- `presentation/` owns HTTP and CLI input, request and response validation, and output mapping. Pass validated input to the injected Use Case. Wire concrete adapters at the API, Worker, or Indexer entry point.

Keep imports inward. Presentation depends on Application. Application depends on Domain and its own ports. Infrastructure implements Application ports and can use Domain types. Keep shared infrastructure independent of feature packages.

Keep atomic writes inside the repository transaction. For Worker handlers, use the supplied connection so the handler's database writes and outbox acknowledgement commit or roll back together.

Use a Fake Repository that implements the same port to unit test orchestration. Run `make check` before handing off changes. Use PostgreSQL integration tests for changes that affect row locking, concurrent claims, or transaction boundaries.

## Project knowledge graph

- Use the `graphify` skill and query `graphify-out/graph.json` first for questions about project structure, imports, and relationships. Verify relevant source files before editing or asserting runtime behavior.
- Open `graphify-out/graph.html` for the interactive map and `graphify-out/GRAPH_REPORT.md` for the summary. Check `graphify-out/BUILD_NOTES.md` for coverage limitations.
- Run `graphify update .` after code changes. For documentation changes, use the skill's semantic update workflow so the graph includes the updated meaning.
- Treat `docs/legacy/` as historical reference and coupon drafts as scaffolding. Graph relationships do not establish a verified deployment, authentication, or transaction execution.
