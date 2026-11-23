# Contract validation before the Python implementation

Candidate repository: `corp-ais/blockchain-coupon-marketplace-loyalty`.
Source revision inspected: `1dc42d555046830c7320e46052696f62ab380dad`, pushed to
`feature/version2.0` during this session. The Python reference pack pins that exact commit.

**Deployment status: unverified.** No approved on-chain addresses, proxy implementation identity,
or deployment block were supplied. This inspection establishes source behavior only. Consequently
the Python app exposes no transaction submission endpoints or live chain indexer.

## Verified calls and identities

| Contract call | Inputs and output | Identity and authorization |
| --- | --- | --- |
| `MarketplaceUpgradeable.listCoupon` | `typeId, amount, pricePerUnit, paymentToken`; returns `listingId` in a call, emits `CouponListed` in a transaction | Seller is `msg.sender`, must be whitelisted, own the coupons, and approve the marketplace for ERC-1155 transfers. |
| `MarketplaceUpgradeable.buyCoupon` | `listingId, amount`; no Solidity return value, emits `CouponSold` | Buyer is `msg.sender`, must be whitelisted and different from the seller. THB allowance is granted to Vault; other ERC-20 allowance is granted to Marketplace. |
| `Coupon.redeem` | `typeId, amount`; returns `string[] redeemCodes`, emits Coupon's `CouponRedeemed` | Burns from `msg.sender`. Requires sufficient balance and an active coupon type. |
| `Coupon.redeemFrom` | `from, typeId, amount`; returns redeem codes | Caller must be the owner, `from`, or an ERC-1155 operator approved by `from`. Settlement uses `from`, not the caller. |
| `MarketplaceUpgradeable.refundExpiredCoupon` | `typeId, maxEscrows`; returns `remaining`, emits `ExpiredEscrowReleased` | Owner only, after expiry. Pays remaining escrow to sellers in batches. It does not refund the buyer. |
| `MarketplaceUpgradeable.updatePrice` | `listingId, newPricePerUnit`; emits `PriceUpdated` | Active listing's seller only. Existing purchases keep their escrow price. |

Source locations at the pinned revision:

- Entry point: `contracts/MarketplaceUpgradeable.sol:32`, `:56`, `:74`, `:83`, `:121`.
- Listing/purchase checks: `contracts/abstracts/BaseMarketplaceUpgradeable.sol:356`, `:376`.
- Payment branches: `contracts/abstracts/BaseMarketplaceUpgradeable.sol:495` and `:515`.
- Coupon entry point: `contracts/Coupon.sol`; implementations in `contracts/abstracts/BaseCoupon.sol:121`
  and `:160`; ABI declarations/events in `contracts/interfaces/ICoupon.sol`.
- Vault funding and releases: `contracts/Vault.sol:72`, `:103`, `:123`.
- Marketplace signatures/events: `contracts/interfaces/IMarketplace.sol`.
- Deployment script candidate: `scripts/deployFull.js:59` deploys a UUPS proxy. This is not evidence
  that a particular deployment exists or has been upgraded.

## Discrepancies with old assumptions

1. Redemption burns coupons, consumes codes, calls Marketplace, and releases applicable Vault
   escrow within one transaction. A revert unwinds the whole transaction. There is no backend
   burn-then-release workflow to implement. If Coupon's marketplace callback is not configured,
   the callback is skipped; deployment wiring must be checked before enabling writes.
2. Only the configured THB address creates a new Vault escrow. Other ERC-20 payments go directly
   to the seller. The current resale backing logic carries the original escrow through the resale;
   `T04 CASE 1C` verifies the THB-to-points-to-redemption flow for both implementations.
3. The name `refundExpiredCoupon` remains, but its current parameters and owner-only payout
   semantics differ from the earlier buyer-refund API. Never expose an expired buyer-refund route
   by inferring behavior from the function name.
4. Listing prices can change via `updatePrice`. The legacy SQL trigger
   `f_listing_price_immutable` blocks changes once a listing is active, conflicting with source.
5. Solidity `buyCoupon` returns nothing. A submitted transaction yields a transaction hash;
   business results must be obtained from the mined receipt and events, not an invented return DTO.
   Similarly a transaction does not deliver `redeem`'s Solidity return value to an ordinary RPC
   transaction caller; the Coupon event contains the codes.
6. Coupon and Marketplace both emit an event named `CouponRedeemed`, with different signatures.
   An indexer must distinguish emitter address and ABI topic, not event name alone. The declared
   `CouponRefunded` event is not the current expiry payout event.
7. The interface's `paymentToken` comment mentions address zero for ETH. Actual listing validation
   requires a contract address and payment uses ERC-20, so native ETH is not supported here.
8. `totalSupply` is cumulative issued supply and is not reduced by redemption; `totalRedeemed`
   increases. Do not map those counters to an old off-chain supply formula without reconciling it.

The old Prisma reference is archived, not executed as the new database schema. Its source-defined
limits, fees, per-code reservations, ownership rules, and operations must be reviewed per feature.
This migration does not silently adopt all fourteen proposed features as implemented requirements.

## Repeatable reference checks

```bash
.venv/bin/python scripts/sync_contract_reference.py --check
```

This reads committed ABIs using `git show` at the pinned revision; local edits in the contract repo
cannot alter the result. The manifest includes SHA-256 hashes. Backend tests verify the bundled
checksums and key function shapes. These checks do not attest any live deployment.
