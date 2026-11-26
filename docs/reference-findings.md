# Findings against the reference packs

`blockchain-reward-platform/reference/` is read-only evidence (its README: never edit it, open an
issue instead). This file records where the backend deliberately departs from a reference pack, the
evidence, and the issue text to file against the reference repository. Each entry names the
migration and test that carry the decision.

## Open: issues to file against the reference repository

### FEAT-04 · the pack models an epoch token; the delivered contract is NewPointToken v3

**Evidence.** `reference/feat-04/db/migration.sql` makes `point_type_chain.genesis_at`,
`epoch_seconds` and `validity_epochs` NOT NULL, and README §"ค่า epoch กับการหมดอายุ" derives every
expiry from them (an ERC-7818 style token). The delivered contract,
`reference/point-contract/point-smart-contract.html` ("Factory lifecycle API v3", checked
14 Oct 2026), has no epochs:

- `deployPointToken(name, symbol)` creates a Draft token with no decimals argument;
  `decimals()` is always 18. The pack lets the merchant choose 0-30.
- Expiry is one of four business models, fixed once per token by
  `configureBusiness(business, config)` and announced by `BusinessConfigured(uint8 business,
  bytes config)`:

  | id | model | config |
  | --- | --- | --- |
  | 1 | A · one program window | `(uint256 startDate, uint256 expireDate)` |
  | 2 | B · per-lot dates, FEFO | `(uint8 0)` |
  | 3 | C · per-lot dates, user selects | `(uint8 1)` |
  | 4 | D · duration from delivery | `(uint8 policy)` 0 FEFO_ONLY, 1 USER_SELECT |

- Lot dates arrive through `LotCreated(lotId, startDate, expireDate)`.

No event emits `genesis_at`, `epoch_seconds` or `validity_epochs`, so a projector could never write a
`point_type_chain` row under the pack's schema. This also answers RFD 10 Q2 (BE-0201): everything
the projector needs is in `TokenDeployed`, `BusinessConfigured` and `LotCreated`; no `eth_call`.

**What the backend does.** Migration `0006_feat04_point_type`:

- `point_type.decimals` must be 18 (`ck_point_type_decimals_fixed`, default 18).
- `point_type` stores the configuration the merchant requests (`business_model`,
  `program_start_at`, `program_expire_at`, `lot_policy`), checked per model by
  `ck_point_type_business_config`.
- `point_type_chain` drops the three epoch columns and stores the configuration read from
  `BusinessConfigured`, NULL until the token is configured, with its own block and tx
  (`ck_point_chain_configured`).
- `tests/sql/feat04_acceptance.sql`: T4 tests `decimals = 18` instead of 0-30, T11 tests the
  configuration instead of `epoch_seconds > 0`, T15 requires a configured contract for LIVE, and
  T15b (added) requires the chain configuration to equal the requested one.

The schema holds all four models, so it does not wait on the business choice in
`reference/point-contract/CHOOSE-BUSINESS.md` (BE-0214). Still open and out of this migration:
`point_balance_bucket` keeps the epoch model until BE-0512 moves balances to the contract's lots;
reference FEAT-05 and FEAT-06 share the epoch assumption.

**Issue text.**

> FEAT-04 schema assumes an epoch token, but NewPointToken v3 has no epochs.
> `point_type_chain.genesis_at / epoch_seconds / validity_epochs` are NOT NULL and no v3 event
> provides them; `deployPointToken(name, symbol)` takes no decimals (always 18). Expiry is one of
> business models A-D set by `configureBusiness` / `BusinessConfigured`. FEAT-05 and FEAT-06 inherit
> the epoch model. Backend: migration 0006 and docs/reference-findings.md.

### FEAT-04 · `f_point_chain_immutable` does not protect `validity_epochs`

**Evidence.** README §"ค่า epoch กับการหมดอายุ" says `trg_point_chain_immutable` stops the three
epoch values changing, because a change would move every computed expiry. The function in
`reference/feat-04/db/migration.sql` compares `contract_address`, `genesis_at` and `epoch_seconds`
only: `validity_epochs` can be rewritten.

**What the backend does.** The columns differ (see above), but the rule is kept and widened:
`trg_point_chain_immutable` in 0006 fixes every chain-sourced column once it is set, including the
configuration, which may go from NULL to a value once. T9b in `tests/sql/feat04_acceptance.sql`
fails against a trigger written like the reference one (checked by swapping it in).

**Issue text.**

> `f_point_chain_immutable` (FEAT-04) omits `validity_epochs`, the value that moves every expiry
> date, although the README says all three epoch values are fixed. Compare every chain-sourced
> column, e.g. with `IS DISTINCT FROM`.

### FEAT-05 · balances are epoch buckets; the contract has lots and inventory

**Evidence.** `reference/feat-05` keeps balances in `point_balance_bucket` (one row per holder per
epoch, `expires_at` from the epoch constants) and models issuing as the merchant reserving and
transferring from its own balance. In NewPointToken v3 the merchant does not transfer from a
balance: A mints (`mint`), B/C mint lots (`mintLot` with dates), D delivers from inventory
(`mintInventory`, then `sendToCustomer` with a duration). Only D has a merchant balance to
reserve against (inventory, which does not age). Customer balances are lots (`LotCreated`,
`LotMinted`, `LotTransferred`, `LotRedeemed`, `CustomerDelivered`), spent First Expiring First
Out with ties on the smaller lot id. At exactly `expireDate` a lot of A/B/C is still Active while
a D lot is Expired.

**What the backend does.** Migration `0008_feat05_lots_inventory` (the schema parts of BE-0512
and BE-0513): `point_lot`, `point_lot_balance`, `point_inventory` replace `point_balance_bucket`;
business A has one `lot_key = 'A'` row holding its program window. `f_available_points`,
`trg_point_reservation_balance` and `f_points_fefo` carry T2-T6 and T17 onto lots and
inventory; T2b (the `expireDate` edge) and T4b (D inventory) are added.
`expiry_notice` (FEAT-06) pointed at buckets and now points at a lot balance, keeping its
one-notice-per-threshold rule; FEAT-06's other rules wait for that pack.

**Issue text.**

> FEAT-05/06 keep balances as epoch buckets and model issuing as a transfer from the merchant's
> balance. NewPointToken v3 has lots (FEFO by expireDate then lotId; D expires at expireDate,
> A/B/C do not) and, for D, an inventory; A/B/C mint new points. Backend: migration 0008 and
> docs/reference-findings.md.

### FEAT-05 · `ledger_entry` merged two ledgers that exclude each other

**Evidence.** FEAT-05 `ledger_entry` is a point ledger (`point_type_id`, `holder_kind`,
`direction` DEBIT/CREDIT). FEAT-10 and FEAT-12 `ledger_entry` is a THB ledger (`account` NOT NULL
in a THB enum, `asset = 'THB'`, entries sum to zero). 0002 merged both into one table, so a
point row cannot satisfy `account` or `asset`.

**What the backend does.** Migration `0007_feat05_reservation_ledger` moves points to
`point_ledger_entry` and drops the FEAT-05 columns from `ledger_entry`. T16 becomes a deferred
constraint trigger (DEBIT = CREDIT per operation at COMMIT) instead of a query.

### All money packs · amounts are decimal strings with no rule

**Evidence.** 0002 ported `numeric(78,0)` to `String(78)` and dropped `amount > 0` / `>= 0` on
every money column so the schema runs on SQLite, which cannot hold these numbers (one point is
10^18 base units).

**What the backend does.** `f_is_uint256(text, positive)` (0007) validates digits, no leading
zero and the 2^256 - 1 bound on PostgreSQL. FEAT-05 tables use it now (T11 T12); each later
money pack applies it to its own columns (THB `ledger_entry` needs a signed variant). Money rules
are therefore proved on PostgreSQL, by `tests/sql/*_acceptance.sql`.

### FEAT-06 · smaller findings in the read-side pack

| Finding | What the backend does |
| --- | --- |
| T5 claims "available never goes negative" but its numbers give 50, so `greatest(..., 0)` was never exercised. Without it the merged schema returned -250 for an expired lot with an open reservation. | 0009 clamps at zero; `tests/sql/feat06_acceptance.sql` T5 asks both the reference case and one that would be -250. |
| The tests pin T0 to 2026-10-01, but the reservation trigger checks against the real clock, so the fixture breaks once real time passes the test lots' expiry. | T0 is the start of the day the test runs; the read functions take `as_of`, so results still repeat. |
| T18 forbids any column matching `available`; FEAT-12's `deposit_reversal.available_at_reversal` matches. It records the balance at the moment of a reversal (a fact), not a cached balance. | T18 exempts that one column and says why, as FEAT-02 T32 does for `tel_hash`. |
| T20 asserts the plan uses `ix_bucket_live`. With lots keyed by (point type, holder, lot), the primary key already serves the query; an extra index went unused. | No new index; T20 asserts an index scan on `point_lot_balance` and no sequential scan, whatever the index is called. |
| `f_expiry_forecast` groups by UTC date, as in the reference. A lot expiring at 03:00 Bangkok time is reported on the previous day. | Kept as the reference has it. **Open for product:** group by Asia/Bangkok date instead? A one-line change in 0009's function. |

### FEAT-07 · the pack's code model does not match the Coupon contract

**Evidence.** `src/marketplace/infrastructure/blockchain/abi/Coupon.json` (the delivered ABI):

- `createCouponType(string name, uint256 startDate, uint256 expireDate)` takes a start date; the
  pack's `coupon_type` has none.
- `mint(to, typeId, amount, string[] redeemCodes)` stores the strings it is given, and
  `getRedeemCode(typeId, index)` is a public view. Whatever is minted can be read by anyone.
- The token is ERC-1155: a holder has an *amount* of a type, not particular codes.
  `redeem(typeId, amount)` returns the next unused strings of the type's pool
  (`getNextRedeemCode`), whoever minted or holds them.

**What the backend does (0010).** `coupon_type.starts_at` (T5). The strings minted on chain are
each code's `code_ref` = `'0x' || hex(sha256(id))`, never the plaintext, and
`ck_code_ref_derived` (T6) holds every row to that, so the projector can map a `RedeemCodeUsed` /
`CouponRedeemed` string back to a row without knowing any code. The plaintext is shown to the
merchant once and kept nowhere (T10).

**Open for FEAT-08 / FEAT-10 / FEAT-11.** Because redeem takes codes from a per-type pool, the code
the chain reports as redeemed is not necessarily the one the holder presented. Those packs must
decide whether `coupon_code.holder_*` tracks particular codes or only counts per holder (the
chain's view). FEAT-11 also uses `coupon_type.contract_address` / `chain_type_id`, which duplicate
`coupon_type_chain` (0002 kept both); only the `coupon_type_chain` copy is protected by
`trg_coupon_chain_immutable`.

**Issue text.** "FEAT-07: `coupon_type` has no start date although `createCouponType` requires
one, and the pack does not say what goes into `mint`'s `redeemCodes`. The contract exposes them
through the public `getRedeemCode`, so they must be references (`code_ref`), not codes. Redeem
draws from a per-type pool, which breaks the per-code holder model FEAT-08/10/11 assume."

### FEAT-07 · smaller findings

| Finding | What the backend does |
| --- | --- |
| `f_issue_within_supply` recounts the type's live codes for every inserted row: issuing the maximum 1,000,000 codes reads about 5 x 10^11 index entries. | `coupon_type.issued_count`, kept by statement-level triggers on `coupon_code` (one UPDATE per type per statement), and the CHECK `ck_coupon_type_within_supply`. The row update also serialises concurrent issues of one type: checked with two sessions, the second waits and is refused. |
| The supply trigger runs on INSERT only: setting a VOIDED code back to ISSUED passes the supply. | The counter follows UPDATE and DELETE too (T8b). |
| "total_supply cannot change" is a comment, not a rule. | Lowering it under `issued_count` breaks the CHECK (T21d). Raising it is allowed: the contract has no cap, the database quota is the only one. |
| Only `chain_type_id` was immutable. | `trg_coupon_chain_immutable` covers every chain-sourced column (T13); `chain_type_id` must be a uint256 string (T13b). |
| T20 (LIVE has a chain row) was a report query. | `trg_coupon_live_has_chain`, checked at commit, so the projector may write either table first. |
| `face_value >= 0` was lost in 0002's string port. | `ck_coupon_type_face_value_uint256` (T19). |
| The name index is an expression index. | `name_normalized` column + CHECK, as `point_type` (0006). |
| `code_hash` is a plain sha256 of the code. Short codes can be recovered from a leaked table by trying every code. | Left as the pack has it (66-character hex either way). **Open for FEAT-08:** use an HMAC with a KMS-held key, as OTP codes do (FEAT-02). |

### FEAT-08 · the pack's listing flow is not the Marketplace contract

**Evidence.** `src/marketplace/infrastructure/blockchain/abi/Marketplace.json` and `Vault.json`:

- Listing is `listCoupon(uint256 typeId, uint256 amount, uint256 pricePerUnit, address
  paymentToken)`, after the Coupon token's ERC-1155 `setApprovalForAll`. The pack's three steps
  `approveCoupon → createListing → lockInVault` do not exist, and its README admits the ABI was
  "a requirement the contract must have".
- The Vault locks the buyer's funds (`lockFunds`, `releaseFunds`, `returnFunds`) when a coupon is
  bought; it never holds coupons.
- A listing is an *amount* of a type. `CouponListed`, `CouponSold` and `buyCoupon(listingId,
  amount)` name no codes. The contract also has `updatePrice`, which the pack says not to offer.

**What the backend does (0011).** `listing_item` stays as the backend's record of which of the
seller's codes are set aside, and must hold exactly `quantity` rows, the listed amount
(T3 T3b). `tests/sql/feat08_acceptance.sql` T11 uses the real step names.

**Open for FEAT-09 / FEAT-10.** After `listCoupon` the chain holder of the coupons is the
marketplace contract, so the projector must not rewrite `coupon_code.holder_*` from Transfer
events while the codes are listed. A partial purchase moves some items to the buyer while the
listing stays ACTIVE, so T24 ("items are held by the seller") only holds for codes not yet sold;
FEAT-10 must mark sold items. Whether the seller must be whitelisted (`addToWhitelist`) before
listing is not stated anywhere.

**Issue text.** "FEAT-08: the listing steps (approveCoupon / createListing / lockInVault) are not
in the delivered Marketplace ABI, which uses setApprovalForAll + listCoupon(typeId, amount, price,
paymentToken) and a Vault for buyer funds. The quantity trigger only rejects too many items (the
comment says 'must equal'), and a code can enter two open listings when no COUPON_UNIT
reservation is written."

### FEAT-08 · smaller findings

| Finding | What the backend does |
| --- | --- |
| `f_listing_quantity_match` rejects only `count > quantity`, though its comment says the two must be equal. | `trg_listing_item_max` refuses the extra item at once; `trg_listing_item_count` requires equality at commit (T3b). |
| Only the COUPON_UNIT reservation stopped one code from entering two listings; `listing_item` itself allowed it. | `trg_listing_item_valid` refuses a code already in a PENDING or ACTIVE listing (T9b) and locks the code row, so concurrent attempts queue. Checked with two sessions: the second is refused once the first commits. A FAILED listing frees its codes (T16b). |
| Items could be added to a listing after it went ACTIVE. | Refused unless the listing is PENDING (T2b). |
| `trg_listing_chain_immutable` covered `contract_address` and `chain_listing_id` only. | Every chain column, and the close (block, tx, reason) once written (T17); `chain_listing_id` must be a uint256 (T17b). |
| T23 (ACTIVE has a chain row) was a report query. | `trg_listing_active_has_chain`, checked at commit. |
| `unit_price > 0` was lost in 0002's string port. | `ck_listing_unit_price_uint256` (T8 T20). |

### FEAT-09 · delist is one transaction; the step gate is too wide

**Evidence.** `Marketplace.json`: `delistCoupon(uint256 listingId)` emits `CouponDelisted` and
returns the coupons to the seller in the same transaction. The pack's `cancelListing →
releaseFromVault` pair, and the ordering argument built on it, do not apply. The pack's
`f_step_order_gate` holds every operation's step n until step n-1 is FINALIZED; FEAT-07 issues up
to 1,000,000 codes in mint steps of 500, which do not depend on each other.

**What the backend does (0012).** The five rules of the pack, with two changes:
`trg_step_order_gate` applies to LISTING_CREATE / LISTING_DELETE / LISTING_BUY only (T19b proves a
mint batch is not held back), and `ck_operation_actor_scope` also accepts `system:` (FEAT-02 writes
`system:provision`). `trg_delist_admissible` leaves a malformed scope to the CHECK, so T8 is refused
by the rule it tests.

**Issue text.** "FEAT-09: the delist steps (cancelListing / releaseFromVault) are not in the
delivered Marketplace ABI; delistCoupon is one transaction. f_step_order_gate also serialises
COUPON_ISSUE mint batches, making large issues wait for finality between every batch."

### FEAT-09 · smaller findings and open questions

| Finding | What the backend does |
| --- | --- |
| `uq_listing_inflight` gains LISTING_BUY (FEAT-09 and FEAT-10 agree): a listing has one unfinished purchase at a time. | Followed. **Open for product:** a busy listing serves one buyer per finality window; others get 409 LISTING_BUSY and retry. The contract itself allows concurrent partial `buyCoupon`. |
| `f_listing_status_transition` did not cover INSERT. | A listing must start PENDING (T26b). |
| FEAT-08's acceptance T14, T15, T16 and T19 relied on behaviour 0012 now refuses (a second step before the first is final, releasing a listed code, delisting a PENDING listing). | Those tests were rewritten to hit the rule they name; `tests/sql/feat08_acceptance.sql` says so in its header. |
| Open in the pack: cancelling a PENDING listing, a reason for admin delists, unblocking MANUAL_REVIEW. | Unchanged: PENDING cannot be delisted, admin delists record who in `operation_event.metadata`, MANUAL_REVIEW keeps blocking. |

### FEAT-10 · the pack pays the seller at purchase; the chain locks the money in the Vault

**Evidence.** `Marketplace.json`, `Vault.json`: `buyCoupon(listingId, amount)` emits `CouponSold`
and the Vault's `FundsLocked(escrowId, seller, buyer, amount)`. The seller is paid by
`releaseFunds` / `releaseFundsPartial` when the buyer redeems (`onCouponRedeemed`, FEAT-11);
`refundExpiredCoupon` returns unredeemed money to the buyer (`returnFunds`). Neither ABI has a fee.
The pack's T15 credits MERCHANT_THB (net) and PLATFORM_THB (fee) at purchase and names the steps
`approveTHB → executePurchase`; the real ones are the THB token's `approve` and `buyCoupon`.

**What the backend does (0013).** A purchase moves the buyer's THB to a new ledger account,
`ESCROW:<purchase id>`, so no database balance holds money the chain has locked. fee_schedule and
the purchase's fee / net split stay as the pack has them, decided at purchase and applied at
release. Everything else follows the pack: ledger sums to zero per operation and balances equal
their ledger (both at commit), no negative balance but ISSUANCE, THB reservations within the
available balance under a per-account advisory lock, buy admission in the database.

**Open for product / FEAT-11.**
- The contract takes no fee. With `fee_bps > 0` there is no on-chain transfer to the platform;
  either the rate stays 0, the platform charges outside the chain, or the contract changes.
- Whole listings only (`uq_purchase_listing`, as in the pack), though `buyCoupon` takes an amount.
  Partial buys need per-purchase items and a remaining count instead of the unique index.
- `escrow` (from 0002) mirrors the Vault; FEAT-11 should tie `ESCROW:<purchase>` to it
  (`remaining_amount` = the account's balance).

**Issue text.** "FEAT-10: the delivered Marketplace locks the buyer's THB in the Vault at purchase
and pays the seller on redemption; the pack's ledger pays seller and platform immediately, so
database balances would show money that is still locked on chain. The contract has no fee. The
balance-equals-ledger check fires only when thb_balance changes, so a ledger row written without a
balance update commits."

### FEAT-10 · smaller findings

| Finding | What the backend does |
| --- | --- |
| `trg_balance_matches_ledger` fires on thb_balance only. | Also fires on ledger_entry (T14b). The test uses accounts with no queued balance event, so only the ledger-side trigger can catch it: dropping that trigger turns T14b red. |
| The ledger could be edited or deleted. | `trg_ledger_append_only` (T18b): corrections are reversing entries. |
| `purchase` is "copied from the listing" by the service only. | `trg_purchase_terms`: price, quantity, seller, its own LISTING_BUY operation and the rate in force at insert; terms never change afterwards (T20b T23b). |
| Listing price could change under a buyer (the contract has `updatePrice`). | `trg_listing_terms_fixed` (T24): delist and list again. |
| Amounts lost their numeric rules in 0002. | `f_is_int256` for signed ledger and balance amounts, `f_is_uint256` for purchase amounts (T18). |
| A THB reservation names its account only through a prefix convention. | `ck_*_account_id`: every account_id starts with its account (T12b); a reservation may spend CUSTOMER_THB or MERCHANT_THB only (T12). |
| FEAT-06's acceptance used a LISTING_BUY operation on a point type as a generic fixture. | Now POINT_TRANSFER; 0013 checks that a LISTING_BUY names a real listing. |
| The balance check sums an account's whole ledger at every commit. | `ix_ledger_account` keeps it an index scan; a busy account will want a checkpoint later. |

### FEAT-11 · redeem and release are one transaction; the Vault keeps an escrow per purchase

**Evidence.** The pack's README says its `burnCoupon(code_ref)` "is a requirement the contract must
have, not the real thing yet". The delivered Coupon has `redeem(typeId, amount)` / `redeemFrom`;
Marketplace has `onCouponRedeemed(redeemer, typeId, amount)` and `getHeldBacking(holder, typeId)`;
the Vault has `releaseFundsPartial(escrowId, couponsToRedeem)` and `escrows(escrowId)`. Redeeming
burns and pays the seller in one transaction, from the redeemer's escrows of that type in the
contract's own order. Each purchase is its own escrow (`FundsLocked(escrowId, …)`), so many escrows
of one coupon type are active at once, which the pack's `uq_escrow_active` (T9) forbids.

**What the backend does (0014).** An escrow row is one Vault escrow (`chain_escrow_id`) and one
purchase (`purchase_id`). It copies the purchase (total, coupon count, wallets, coupon type) and
never changes those; its remaining amount only falls and equals the ledger account
`ESCROW:<purchase>` at commit; it is closed exactly when nothing remains. "A redeemed coupon's
seller has been paid" (pack T11, a report query) is checked at commit, counted per buyer and coupon
type because the Vault does not release per code. Redeem admission is in the database: the actor
holds the code, the coupon type is in its validity window, the code is not for sale.

**Open (needs the contract or product).** `redeem` takes codes from the type's pool and releases
from the redeemer's escrows in the Vault's order, so the code and escrow the chain reports may not
be the row the customer presented. Either the contract redeems a named code (`code_ref`), or the
backend tracks coupons as counts per holder and gives up per-code ownership (FEAT-07 already flags
this). The pack's `splitEvenly` question (how a partial release divides the total) is also the
contract's to answer; the schema only bounds it (remaining falls, never below 0).

**Issue text.** "FEAT-11: burnCoupon(code_ref) + releaseFundsPartial does not exist; the delivered
Coupon.redeem → Marketplace.onCouponRedeemed → Vault release is one transaction. The Vault keeps
one escrow per purchase, so uq_escrow_active (one per coupon type) would block a second buyer.
T11 checks after the fact what the schema can enforce at commit."

### FEAT-11 · smaller findings

| Finding | What the backend does |
| --- | --- |
| T3 releases a reservation while its operation is still PENDING. | FEAT-05's release rule refuses that; the test ends the operation first. |
| Nothing tied the Vault mirror (`escrow`) to the money (FEAT-10's ESCROW account). | `trg_escrow_matches_ledger` at commit, from both tables; ESCROW money without an escrow row is refused. FEAT-10's T15 now writes the escrow row in the purchase's transaction. |
| A REDEEMED code could be set back to HELD, or an ISSUED code marked REDEEMED. | `trg_coupon_redeemed_final` (T14). |
| The redeem request carried the code id in the payload only. | `subject_id` is the code id; admission is checked from it (T13). |
| Deferred checks queued by the fixture were firing for other tests' rows. | The tests fire and consume them before T17, so each case is caught only by the trigger it names (verified by dropping each trigger in turn). |

### FEAT-12 · the mint rules trust the caller in two places

**Evidence.** reference/feat-12/db/migration.sql:
- `f_issuance_only_from_mint` checks only that the operation's kind is THB_MINT. Any THB_MINT
  operation can then write `ISSUANCE −x, CUSTOMER_THB +x` with no deposit behind it: F4 through
  the database instead of the admin key.
- `f_reversal_outcome_matches_balance` checks the outcome against `available_at_reversal`, a
  number the caller writes. Recording a false "available" picks the outcome.
- The pack's own fixture confirms dep_2 and dep_7 with the same webhook `wh_2`, and nothing
  requires the confirming webhook to be signed or to be a `charge.complete`.

The THB token's ABI (`mint(to, amount)`, `burn(from, amount)`) matches the pack; it also has
`forceTransfer(from, to, amount)`, an admin move of anyone's THB that the indexer must surface,
never apply silently.

**What the backend does (0015).** The pack's rules, plus:
- `trg_issuance_backed` (at commit): an ISSUANCE movement is either the operation's `thb_mint`
  (ISSUANCE −amount, the depositor's account +amount) or its BURNED reversal (ISSUANCE +amount,
  the depositor −amount) (T16b T16c).
- The reversal trigger computes the depositor's available THB itself and requires the recorded
  value to equal it (T22b).
- `uq_deposit_webhook`: a webhook confirms one deposit. The confirming webhook must be a signed
  `charge.complete` of the same provider (T7b T7c).
- MINTED needs the thb_mint row, REVERSED a BURNED reversal, and a deposit starts
  PENDING_PAYMENT (T14 T15b T23b).
- A thb_mint is recorded under its deposit's own THB_MINT operation (T10b). Afterwards only its
  chain columns change, and one Mint event backs one mint (`uq_thb_mint_chain_event`).

FEAT-10 and FEAT-11's acceptance tests now fund their buyers through a real deposit
(webhook → confirmed deposit → thb_mint), because a bare THB_MINT no longer commits.

**Open (unchanged from the pack).** PSP fees (mint before or after the fee); what happens after
MANUAL_REVIEW and who bears it; deposit limits (20 to 50,000 baht, not yet approved by
compliance, no daily cap); a `charge.complete` with a different amount leaves the deposit
PENDING_PAYMENT until it expires.

**Issue text.** "FEAT-12: trg_issuance_only_from_mint checks the operation kind only, so a THB_MINT
operation with no deposit can still create THB. available_at_reversal is trusted as supplied, so
the outcome can be chosen by writing a false balance. One webhook may confirm several deposits
(the fixture's wh_2 does), and an unsigned webhook may confirm at all."

### FEAT-13 · history is checked at commit but can be rewritten; a webhook can reach another merchant

**Evidence.** reference/feat-13/db/migration.sql:
- `operation_event` is written by the caller. `trg_opevent_chain_intact` checks seq and
  from_status on INSERT and `trg_status_has_event` checks the last event at commit, but only when
  an `operation` row changes. An UPDATE or DELETE of an event row fires neither, so the history
  the API tells customers can be edited after the fact. `actor` and `metadata` are whatever the
  caller writes.
- `webhook_delivery` has no admission rule: nothing checks that the event exists, that the
  operation belongs to the endpoint's merchant, or that the endpoint is ACTIVE and subscribed to
  the event type. A delivery row for merchant A's operation on merchant B's endpoint is accepted
  and signed: a data leak.
- `f_delivery_order_gate` returns early when the previous delivery_seq does not exist
  (`IF v_prev IS NULL THEN RETURN NEW`), so a gap in delivery_seq skips the ordering rule.
- `trg_attempt_count_matches` fires on `webhook_delivery_attempt` only; raising the counter on
  the delivery with no attempt row passes. Attempt rows can be edited or deleted.
- `url ~ '^https://'` accepts `https://` with no host; `event_types` accepts `[""]`.

**What the backend does (0016).** The pack's status machine (a new operation starts PENDING;
FINALIZED and CHAIN_REVERTED are final; CONFIRMED → ORPHANED → SUBMITTED for reorgs), plus:
- The database writes `operation_event` itself on every status change (`trg_operation_event_write`):
  seq is the next number, from_status the old status. The caller passes who acted and why through
  `set_config('app.operation_actor' / 'app.operation_event_metadata' / 'app.operation_event_tx_hash',
  …, true)`. Writing, editing or deleting an event directly is refused
  (`trg_operation_event_guard`), so the history is complete by construction (T1-T8 T13b).
  Existing operations whose last event did not match their status get one backfilled event
  (`metadata = {"backfill": true}`).
- `trg_delivery_admissible`: the event exists, the operation's `actor_scope` is
  `merchant:<endpoint's merchant>`, and the endpoint is ACTIVE and subscribed (replays skip the
  last check) (T17 T17b T17c). delivery_seq cannot skip a number (T20b).
- `trg_replay_valid`: a replay points at a DEAD delivery of the same endpoint, operation and event
  (T24c T24d); `uq_delivery_event` is partial on `replay_of IS NULL` so replays are new rows.
- `trg_attempt_count_matches` fires from both tables; attempts are append-only (T29b T29c).
- Endpoints: `https://` with a host, `secret_ref` a short `kms://` path, `event_types` a non-empty
  list of non-empty names, as CHECKs (T14-T16).

Every earlier pack's acceptance test now walks operations through the allowed path
(`PENDING → SUBMITTED → CONFIRMED → FINALIZED`, `SUBMITTED → CHAIN_REVERTED`) instead of inserting
or jumping to a final status; FEAT-09's T7 records the admin through `set_config`.

**Open (unchanged from the pack).** Who may move MANUAL_REVIEW on, and to what; webhook payload
versioning; secret rotation (one `secret_ref` per endpoint).

**Issue text.** "FEAT-13: operation_event rows can be updated or deleted without firing either
history check, so the status history can be rewritten. webhook_delivery accepts an operation of
another merchant on an endpoint, an event that does not exist, and a paused or unsubscribed
endpoint. f_delivery_order_gate skips the ordering rule when delivery_seq has a gap.
trg_attempt_count_matches fires from the attempt side only, and attempts can be edited."

### FEAT-14 · the projector reads events the contracts do not emit; the lease names no one

**Evidence.** reference/feat-14/db/migration.sql and tests/acceptance.sql:
- `f_apply_event` moves coupons one code at a time (`CouponTransfer {codeRef, toKind, toId}`)
  and points with the holder kinds spelled out. The delivered Coupon contract is ERC-1155
  (`abi/Coupon.json`): `TransferSingle` / `TransferBatch` carry `(from, to, id, value)`, never a
  code, and no event names a holder kind. The only per-code fact on chain is
  `CouponRedeemed(typeId, owner, amount, string[] redeemCodes)`.
- The pack's `f_replay_projections` resets `coupon_code.state` to ISSUED and lets the projector
  write REDEEMED. In the backend the redeem operation writes it (FEAT-11) and
  `trg_coupon_redeemed_final` keeps it final, so the two writers would fight.
- `coupon_holding` carried `updated_at` and `applied_at` with `DEFAULT now()`, against the pack's
  own rule that a projection has no apply-time column.
- `f_cursor_needs_lease` checks that a live lease exists, not who holds it. Another process
  moves the cursor freely while the first one's lease is valid, and takes a live lease by
  writing its own name. `f_handle_reorg(p_fork_block, p_owner)` never reads `p_owner`.
- Any session can set `app.reorg_in_progress` and step the cursor back.
- `reorg_log` takes old_tip, finality_depth and orphaned_events from the caller. Its outcome
  trigger requires ROLLED_BACK whenever depth ≤ N, but `f_handle_reorg` chooses MANUAL_REVIEW
  when a final event is in range. After N is raised (it is time-versioned for that), the pack's
  own reorg function fails on its own trigger.
- chain_event and raw_block can be edited or deleted, and `finalized_at` can be set by hand on
  a shallow event, which then forces every later reorg over it to MANUAL_REVIEW. An event's
  block_number and timestamp need not match its block, and an event stays live on an orphaned
  block.
- `f_replay_projections` takes no lock, so it can run alongside the projector (open in the pack).

**What the backend does (0017).** The pack's three promises (L8 lease and cursor-with-events,
RFD 5 hash chain, RFD 6 §3.2 replay), with these changes:
- The projector reads the decoder's normalised args for the real events:
  - CouponTransfer `{from, to, ids, values}` gives an amount per coupon type and holder.
  - PointTransfer `{from, to, value}` gives point_balance.
  - CouponRedeemed `{typeId, owner, amount, redeemCodes}` writes the new `coupon_redemption`
    projection, one row per code_ref.
  - The coupon type comes from (contract, typeId) in coupon_type_chain, and the holder comes
    from wallet. The zero address is a mint or a burn; any other address (the marketplace
    holding listed coupons) is held as EXTERNAL.
  - Unknown types and codes, and overdrafts, are refused at apply time (T19 T19b T34 T35).
- coupon_holding loses its apply-time columns and `coupon_code_id`; as_of_block, as_of_time
  and chain_event_id are required. `coupon_code.state` stays the redeem operation's.
- `app.indexer_owner` names the process. Only the lease owner moves the cursor or handles a
  reorg, and a live lease cannot be taken or released by another process (T4b T4c T4d T31c).
- The cursor steps back only through a ROLLED_BACK reorg_log row written in the same
  transaction for exactly that step (T6 T6b).
- reorg_log values are computed by the database. The outcome is MANUAL_REVIEW if and only if
  the reorg is deeper than N or reaches a final event, and rows are append-only (T29b T30 T31b).
- Blocks and events keep what the chain said: `orphaned_at` is set once, `finalized_at` is set
  once and only N blocks below the cursor, and args change only while unapplied (T10b T15d T27b).
- An event carries its block's number and time and is read from a live block. At commit, live
  events sit on live blocks, no live block or event is above the cursor, and the cursor names a
  live block (T11b T15b T15c T36b).
- Apply, replay and reorg take one advisory transaction lock.
- Wallet addresses are stored lowercase (`ck_wallet_address_lower`), so the projector finds a
  wallet through the unique index.

FEAT-06 to FEAT-12's acceptance fixtures now write the cursor next to the blocks they read, so
they also pass `SET CONSTRAINTS ALL IMMEDIATE`.

**Open.**
- N = 12 is still unconfirmed by the chain team.
- What a person does after MANUAL_REVIEW; the indexer stays stopped, which is safe.
- The 200-block limit on walking back to the fork point.
- The decoder itself: topic0 against the ABI signatures. It must label only Coupon-contract logs
  as CouponRedeemed, because the Marketplace emits an event of the same name.
- The point token's lot events (FEAT-05's `point_lot*` projections) are not projected yet. Those
  tables still carry apply-time columns, and `f_replay_projections` leaves them alone until their
  events are decoded.
- Whether `Coupon.redeem` burns through `TransferSingle` to the zero address, as ERC-1155 `_burn`
  does. The holding projection assumes it does.

**Issue text.** "FEAT-14: the projector's CouponTransfer is per code, but the Coupon contract is
ERC-1155 and its transfers carry type and amount only. f_cursor_needs_lease checks that a lease
exists, not that the caller holds it, and a live lease can be taken over. f_handle_reorg ignores
p_owner. app.reorg_in_progress lets any session rewind the cursor. reorg_log trusts
caller-supplied tip, N and counts, and its outcome trigger rejects the MANUAL_REVIEW that
f_handle_reorg chooses for final events once N has been raised. chain_event, raw_block and
finalized_at can be edited. coupon_holding has DEFAULT now() columns."

## Decisions taken while porting

| Decision | Why | Revisit when |
| --- | --- | --- |
| Customer sessions live in `auth_session` only. | One rotation and reuse-detection path for every subject. | FEAT-02 round 2. |
| v1 has no customer-to-customer point transfer (RFD 9). The contract allows it after `allowSender` (B/C/D); the backend offers no API and its signer signs no such transfer, which is enough because customers hold no keys (F10). | Smallest reversible choice; BE-0515 adds it later without schema change. | Product confirms or opens it. |
| Coupon codes on chain are `code_ref` (sha256 of the row id), never plaintext (FEAT-07). | `getRedeemCode` is public; the reference lets the projector match events without the code. | If the contract gains a hashed-code mode. |
| The database quota (`total_supply`) is the only supply cap for coupons. | The contract has none; `totalSupply` there is cumulative. | If the contract adds a per-type cap. |
| Listings keep per-code items (`listing_item`) although the chain lists an amount (FEAT-08). | FEAT-11 needs to know whose code was redeemed; the item count must equal the amount. | FEAT-10 / FEAT-11, if they settle on counts per holder instead. |
| A purchase moves THB to `ESCROW:<purchase>`, not to the seller (FEAT-10). | The Vault holds it until redemption; balances must match the chain. | FEAT-11 releases it. |
| The database writes `operation_event` on every status change; callers cannot write it (FEAT-13). | A history that can only be appended by the status change itself cannot drift from the status or be rewritten; actor and reason travel in transaction settings. | If a status change ever needs an event the database cannot describe (e.g. two events per change). |
| The projector works on chain amounts per holder (ERC-1155), not per code; per-code chain facts are redemptions only (FEAT-14). `coupon_code` state and holder stay the operations' record. | The contract emits no per-code transfer; a second writer of `coupon_code.state` would fight FEAT-11's final REDEEMED. | If the contract gains per-code transfers, or reconciliation between `coupon_redemption` and `coupon_code` is wanted as a rule. |
| The point schema holds all four business models. | The A/B/C/D choice (CHOOSE-BUSINESS.md) is per token and still open. | Never: it stays correct whichever is chosen. |

## Settled in earlier migrations

| Pack | Finding | Where handled |
| --- | --- | --- |
| FEAT-01 | T10 inserts a step named like an existing one, so `uq_step_name` rejects it and the test passes without any `tx_hash` uniqueness. | `tests/sql/feat01_acceptance.sql` checks which constraint rejected the row; 0003 adds `uq_step_txhash`. |
| FEAT-01 | 0002 dropped `uq_provision_inflight`, `uq_signer_nonce`, `uq_step_txhash` and the outbox topic CHECK. | 0003. |
| FEAT-02 | T32's pattern `(…\|hash)` also matches `customer.tel_hash`, which FEAT-03/05/06 need for lookup by phone hash. | `tests/sql/feat02_acceptance.sql` exempts `tel_hash` and says why. |
| FEAT-02 / FEAT-03 | Two tables for customer sessions (`customer_session`, `auth_session.customer_id`). | Decision: `auth_session` only, so customers get rotation and reuse detection; applied with FEAT-02 round 2. |
| FEAT-03 | `refresh` never checks `disabled_at`, so a disabled admin keeps refreshing for 30 days. | `AuthService.refresh` revokes every session of a disabled account (T14). |
| FEAT-03 | `revoked_reason` is free text; a typo silently defeats T15. | 0005. |
