from eth_abi.abi import decode, encode
from eth_abi.exceptions import DecodingError, EncodingError
from eth_utils.crypto import keccak

from marketplace.coupons.application.dto import RedeemCouponPayload
from marketplace.coupons.application.execution import (
    ChainLog,
    ReceiptCorrelationError,
    RedeemReceipt,
)
from marketplace.coupons.infrastructure.contract_errors import classify_redeem_revert

REDEEMED_TOPIC = "0x" + keccak(text="CouponRedeemed(uint256,address,uint256,string[])").hex()
CODE_TOPIC = "0x" + keccak(text="RedeemCodeUsed(uint256,address,string)").hex()


def correlate_receipt(
    payload: RedeemCouponPayload, tx_hash: str, receipt: RedeemReceipt
) -> tuple[dict[str, object], tuple[ChainLog, ...]]:
    if (
        receipt.chain_id != payload.chain_id
        or receipt.contract_address.lower() != payload.coupon_contract_address.lower()
        or receipt.tx_hash.lower() != tx_hash.lower()
        or len(receipt.block_hash) != 66
        or receipt.block_number < 0
        or receipt.mined_at.tzinfo is None
    ):
        raise ReceiptCorrelationError("Receipt identity does not match the admitted operation")
    if receipt.status == 0:
        if receipt.logs:
            raise ReceiptCorrelationError("Reverted transaction cannot contain committed logs")
        code = classify_redeem_revert(
            receipt.error_name or "unknown",
            origin=receipt.error_origin,
            reason=receipt.error_reason,
        )
        return {"code": code.value}, ()
    if receipt.status != 1:
        raise ReceiptCorrelationError("Invalid receipt status")

    logs = tuple(
        log
        for log in receipt.logs
        if log.address.lower() == payload.coupon_contract_address.lower()
        and log.topics
        and log.topics[0].lower() in {REDEEMED_TOPIC, CODE_TOPIC}
    )
    redeemed: list[tuple[int, str, int, tuple[str, ...]]] = []
    codes: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    try:
        for log in logs:
            if (
                log.tx_hash.lower() != receipt.tx_hash.lower()
                or log.block_hash.lower() != receipt.block_hash.lower()
            ):
                raise ReceiptCorrelationError("Coupon log belongs to another receipt")
            if (
                len(log.topics) != 3
                or any(not topic.startswith("0x") or len(topic) != 66 for topic in log.topics)
                or log.log_index < 0
                or log.log_index in seen
            ):
                raise ReceiptCorrelationError("Invalid or duplicate coupon log identity")
            seen.add(log.log_index)
            (type_id,) = decode(["uint256"], bytes.fromhex(log.topics[1].removeprefix("0x")))
            (holder,) = decode(["address"], bytes.fromhex(log.topics[2].removeprefix("0x")))
            data = bytes.fromhex(log.data.removeprefix("0x"))
            if log.topics[0].lower() == REDEEMED_TOPIC:
                amount, values = decode(["uint256", "string[]"], data)
                if encode(["uint256", "string[]"], [amount, values]) != data:
                    raise ReceiptCorrelationError("Noncanonical coupon event")
                redeemed.append((type_id, holder.lower(), amount, tuple(values)))
            else:
                (code,) = decode(["string"], data)
                if encode(["string"], [code]) != data:
                    raise ReceiptCorrelationError("Noncanonical coupon code event")
                codes.append((type_id, holder.lower(), code))
    except (DecodingError, EncodingError, ValueError, TypeError) as error:
        raise ReceiptCorrelationError("Invalid coupon event encoding") from error
    if len(redeemed) != 1 or len(codes) != 1:
        raise ReceiptCorrelationError("Expected one redeemed coupon and one consumed code")
    type_id, holder, amount, revealed = redeemed[0]
    if (
        type_id != payload.type_id
        or holder != payload.wallet_address.lower()
        or amount != 1
        or len(revealed) != 1
        or not revealed[0]
        or codes[0] != (type_id, holder, revealed[0])
    ):
        raise ReceiptCorrelationError("Coupon events do not match the admitted wallet/type/amount")
    return {
        "walletAddress": holder,
        "typeId": str(type_id),
        "amount": "1",
        "redeemCodes": list(revealed),
    }, logs
