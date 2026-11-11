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
