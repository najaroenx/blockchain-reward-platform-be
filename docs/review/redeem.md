# Redeem — หลักฐานตรวจสอบ

รันใหม่วันที่ 2026-11-16 · scope local ที่ตกลงไว้ · [PR #3](https://github.com/najaroenx/blockchain-reward-platform-be/pull/3)

ดาวน์โหลด [ชุดหลักฐาน ZIP](redeem-2026-11-16.zip) · source commit [`3f1629d`](https://github.com/najaroenx/blockchain-reward-platform-be/commit/3f1629dadc78111176886b716db425ef9c9dcb9e)
Contract revision `1dc42d555046830c7320e46052696f62ab380dad` · PostgreSQL 17.11

| การตรวจ | ผลรอบนี้ |
|---|---|
| Lint, format, mypy, 6 import boundaries และ OpenAPI/Postman validation | ผ่าน |
| Tests ทั่วไป | 226 ผ่าน |
| PostgreSQL: admission 22 + JWT 43 | 65 ผ่าน |
| Handler/projector + local EVM + PostgreSQL | 21 ผ่าน |
| Fail / error / skip | 0 ทุกชุด |
| ลบฐานทดสอบชั่วคราว | ยืนยันแล้ว |

**AC3 ยังเหลือ**: mapping ผู้ใช้ใน JWT กับ wallet และตรวจสิทธิ์ใช้งาน
AC9–13 ผ่านในขอบเขต local; FEAT-11 T8 เป็นงานแยกใน #213
ดู [สถานะ AC และ mapping](../redeem-state.md)

ภายใน ZIP มี README พร้อมตาราง AC → หลักฐาน, JUnit XML ของทั้ง 3 ชุด, logs,
manifest ระบุคำสั่ง/exit code/source commit, source hashes, OpenAPI และ Postman fixture files
รอบนี้ validate ไฟล์ Postman; ไม่ได้รัน Newman ใหม่

SHA-256 ของ ZIP:
```text
05f76cdb7760d7702bdb1d882007c44bc32cbdccbb51b8836f58a8fe59f08a46
```

หลังแตก ZIP ตรวจไฟล์ภายในด้วย `shasum -a 256 -c SHA256SUMS`

สร้างชุดหลักฐานใหม่: กำหนด `REDEEM_VERIFY_POSTGRES_ADMIN_URL` เป็น admin connection ของ
PostgreSQL บน loopback แล้วรันคำสั่งด้านล่าง โดยใช้ output directory ใหม่แต่ละครั้ง
```bash
.venv/bin/python scripts/verify_redeem_artifacts.py --output artifacts/redeem/new-review
```

ต้องมี Docker/PostgreSQL local และ dependencies ของ contract ตาม lockfile ที่ pin ไว้
สคริปต์ใช้ฐานทดสอบแยก, ลบทิ้งเมื่อจบ และไม่บันทึก process environment ลงรายงาน
