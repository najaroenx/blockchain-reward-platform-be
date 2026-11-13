# Redeem — สถานะงาน

อัปเดต 2026-11-13 · issue #224 · branch `BE-0608`

| AC | สถานะ | ทำแล้ว / เหลือ |
|---|---|---|
| 1 | ผ่าน | รับเฉพาะ typeId และกำหนด amount=1 |
| 2 | ผ่าน | JWT ES256, issuer/audience/expiry; token ที่ผิดไม่ถึง admission |
| 3 | รอ | mapping ผู้ใช้ใน JWT กับ wallet และตรวจสิทธิ์ใช้ wallet |
| 4–7 | ผ่าน DB tests | payload + operation/outbox atomic, 202 หลัง commit, replay/conflict, 20 requests เหลืองานชุดเดียว |
| 8 | ผ่าน DB tests | rollback ตอน outbox/acknowledgement/commit; orphan=0 และ retry ได้ |
| 9 | ผ่าน local | ตรวจ code/proxy/wiring และสิทธิ์ owner/holder/operator บน deployment local ที่ตกลงไว้ |
| 10–13 | ผ่าน local | handler/projector: balance/FIFO, error หลัง 202, callback rollback, event correlation และ retry tx เดิม |
| 14 | ผ่าน mapping | บันทึกใน [#213](https://github.com/najaroenx/blockchain-reward-platform/issues/213) แล้ว; T8 ยัง PENDING |
| 15 | ผ่าน | DTO/OpenAPI/Postman/error mapping |

หลักฐานล่าสุด: 226 tests ทั่วไปผ่าน; admission PostgreSQL 22 tests ผ่าน;
handler/projector + local EVM + PostgreSQL 21 tests ผ่าน
([DB tests](../tests/integration/coupons/test_admission.py), [chain tests](../tests/integration/coupons/test_execution_chain.py)).
ติดตามงานผ่าน [PR #3](https://github.com/najaroenx/blockchain-reward-platform-be/pull/3) → `recreate/python-backend`; checkbox บน issue ยังไม่อัปเดตตามงานล่าสุด

ขอบเขตตรวจรับรอบนี้: local chain จาก contract commit `1dc42d5`; ยังไม่มี production
ทำ handler/projector บน local ตามที่ยืนยันแล้ว; บันทึก raw tx ก่อนส่งและคืนผลเดิมเมื่อ retry
AC ของ #224 เหลือ AC3; การเตรียม production ไม่ใช่เงื่อนไขค้างของ AC9–13 ในรอบนี้
ค่าเริ่มต้นของ API ยังตอบ 503 เมื่อผ่าน token แต่ adapters ที่เหลือยังไม่พร้อม

## FEAT-11 → tests ปัจจุบัน (สำหรับ #213)

ต้นฉบับ: [acceptance.sql](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-11/tests/acceptance.sql)
blob `26c3018dcb686dc578c1200ffd7d7171efa2b141` · ไม่ได้รัน SQL เดิมบน schema Python

| เดิม | สถานะ mapping | หลักฐาน / สิ่งทดแทน |
|---|---|---|
| T1 key ซ้ำ | PASS ด้วย test ทดแทน | exact replay, first INSERT, 20 concurrent requests ใน test_admission.py |
| T2 จอง code ซ้ำ | SUPERSEDED | contract เลือก FIFO; ใช้ idempotency และทดสอบ FIFO บน chain แทน |
| T3 ปล่อย/จอง code ใหม่ | SUPERSEDED | ไม่มี reservation รายใบ; retry หลัง rollback และคนละ key เป็นคนละงาน |
| T8 เงินติดลบใน DB | PENDING | ยังไม่มี escrow projection/constraint ใน schema Python; รอ #213 |
| T9 active escrow เดียวต่อ type | SUPERSEDED | local test มี 2 purchase escrows ใน type เดียว; จ่ายเฉพาะ backing ที่ถูกใช้ |
| T10 orphan=0 | PASS ด้วย test ทดแทน | LEFT JOIN operation/outbox หลัง failure ทั้ง 3 จุดและ retry ได้ 0 |
| T11 burn แล้วต้องจ่ายเสมอ | SUPERSEDED | ตรวจ atomic settlement/rollback และ redeem ไม่มี escrow แยกกัน |
| T12 REDEEMED ต้องมีเวลา | SUPERSEDED | receipt projection ต้องมี mined_at; DB ปฏิเสธเมื่อไม่มีเวลา ไม่ใช้ coupon_code เดิมนับ PASS |

SUPERSEDED หมายถึงเปลี่ยนวิธีตรวจสำหรับ API ใหม่นี้ ไม่ได้อนุมัติลบตาราง/constraint เดิม
AC9–13 ผ่านตามขอบเขต local ที่ยืนยันไว้; การปิดงาน #244 ทั้งใบต้องตรวจ scope ของใบนั้นแยกกัน
