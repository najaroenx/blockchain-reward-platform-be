# Blockchain Reward Platform — Python backend

Backend สำหรับ coupon marketplace บน EVM ใช้ Python 3.12, FastAPI, Pydantic,
SQLAlchemy, Alembic และ PostgreSQL ใช้ `src/marketplace/` แบบ Hybrid Feature-based
แยก feature ก่อน แล้วแบ่ง layers ภายใน และรันเป็น modular monolith

สถานะ: API, config แยก process, logging, database migrations และ catalog สำหรับอ่าน projection
ใช้งานได้ ส่วน CLI worker/indexer **ยังไม่เปิด execution production**
มี redeem handler/projector ที่ตรวจ integration บน local chain แล้ว ดู [สถานะ redeem](docs/redeem-state.md)
มี route และ use case สำหรับรับคำขอ redeem แล้ว แต่ค่าเริ่มต้นยังไม่รับงานจนกว่าจะต่อ adapters
สำหรับ wallet mapping, deployment ที่ยืนยันแล้ว และการบันทึก admission จริง
มีตัวตรวจ JWT แบบ ES256 จาก #182 แล้ว ยังไม่มี purchase endpoint

## ติดตั้ง

ต้องมี Python 3.12 หรือ 3.13 ไม่ต้องติดตั้ง Node.js, pnpm หรือ uv แบบ global

```bash
python3.12 scripts/bootstrap.py
.venv/bin/reward-env-init
.venv/bin/alembic upgrade head
.venv/bin/reward-api
```

ตัวติดตั้งสร้าง `.tools/uv` สำหรับเครื่องมือติดตั้ง และ `.venv` สำหรับ dependencies ของระบบ
จาก `uv.lock` โดยไม่แก้ global Python packages ตัวสร้าง env ไม่ทับไฟล์เดิม
ใช้ `python3.12 scripts/bootstrap.py --lock` เฉพาะเมื่อตั้งใจเปลี่ยน dependency resolution

ค่าเริ่มต้นใช้ SQLite `reward.db` สำหรับทดลองในเครื่อง จึงบูตได้โดยไม่ต้องมี Docker
Production บังคับ PostgreSQL; ห้ามนำ SQLite มาใช้แทน production database

```bash
curl http://127.0.0.1:3000/healthz
# {"ok":true,"role":"api"}
curl http://127.0.0.1:3000/readyz
# {"ok":true,"database":"ready"}
```

OpenAPI/Swagger: `http://127.0.0.1:3000/docs` และ `/openapi.json`
`/healthz` วัด process liveness; `/readyz` ทดสอบ database connection จริง แต่ไม่รับรองว่า
deployment, worker, indexer หรือ catalog พร้อมใช้งาน

## Config แยก process

โหลด `.env` → `.env.<role>` → environment จาก shell/CI โดยค่าด้านหลังมีลำดับสูงกว่า
การโหลดไม่เปลี่ยน environment กลาง และไม่อ่านไฟล์ของ role อื่น

| ไฟล์ | ตัวแปร |
| --- | --- |
| `.env` | `DATABASE_URL`, `APP_ENV`, `LOG_LEVEL` |
| `.env.api` | `HOST`, `PORT`, `MOCK_AUTH_ENABLED`, `MOCK_AUTH_TOKEN`, `MOCK_AUTH_USER_ID`, `MOCK_AUTH_WALLET_ADDRESS` |
| `.env.worker` | `RPC_URL`, `CHAIN_ID`, `KMS_KEY_ARN`, `KMS_REGION` |
| `.env.indexer` | `RPC_URL`, `CHAIN_ID`, `FINALITY_DEPTH` |

API และ indexer ปฏิเสธ KMS variables ทั้งจากไฟล์ shared และ environment ที่ส่งเข้ามา
ค่า RPC, chain ID และ KMS ในตัวอย่างเป็น format examples ไม่ใช่ deployment ที่ยืนยันแล้ว

```bash
.venv/bin/reward-worker --check
.venv/bin/reward-indexer --check
```

สองคำสั่งนี้ตรวจ config เท่านั้น ไม่ได้ตรวจว่า RPC/KMS ติดต่อได้จริง ไม่เชื่อม queue
และไม่สร้างข้อมูล projection ถ้ารันโดยไม่มี `--check` จะหยุดพร้อมอธิบายสิ่งที่ยังไม่ได้เปิดใช้

## Catalog API ที่มีในรอบนี้

```text
GET /api/v1/marketplaces/{chain_id}/{marketplace_address}/listings?limit=25&offset=0
GET /api/v1/marketplaces/{chain_id}/{marketplace_address}/listings/{listing_id}
```

อ่าน projection ของ chain และ marketplace ที่ระบุเท่านั้น ไม่เรียก smart contract และไม่คำนวณ
escrow ซ้ำใน backend รายการแบบ list แสดงเฉพาะ active listings, detail อ่านรายการที่ inactive ได้
จำนวนและราคา uint256 ส่งเป็น decimal strings เพื่อไม่สูญเสีย precision; ราคาเป็นหน่วยย่อยของ token
และยังไม่ได้หารด้วย token decimals

ถ้าไม่มี checkpoint จาก indexer จะตอบ `503 projection_unavailable` แทน empty catalog
หากมี checkpoint แต่ไม่พบ listing จะตอบ `404 listing_not_found` ทุกผลมี `indexed_block`
เพื่อบอกระดับข้อมูลที่อ่าน API ไม่มี endpoint สำหรับเติม listing ปลอม
หลังติดตั้งใหม่ catalog จึงยังตอบ 503 จนกว่าจะมี indexer ที่ยืนยัน deployment และสร้าง projection

Catalog reads ปัจจุบันเป็น public และมีเฉพาะข้อมูล listing บน chain ไม่มี endpoint เขียน listings

## Redeem admission ที่ยังไม่ต่อ production adapters

`POST /v1/coupons/redeem` รับเฉพาะ `typeId` แบบ canonical uint256 decimal string
และบังคับ `Idempotency-Key` ยาว 8 ถึง 255 ตัวอักษร Use case สร้าง payload จาก wallet และ
deployment ที่ adapters resolve ให้ โดยกำหนด `amount=1` ฝั่ง server
Validation ของ route นี้ตอบ HTTP 400 และปฏิเสธ field เกินทั้งหมด รวมถึง wallet และ amount

`main.py` register route นี้แล้ว โดยรับ `RedeemDependencies` ผ่าน `create_app(..., redeem=...)`
สามารถต่อ authentication ก่อน แล้วค่อยต่อ use case สำหรับ deployment และ DB admission ได้
เมื่อไม่ได้ตั้งค่า JWT และปิด mock auth คำขอที่มี bearer token ตอบ `503 REDEEM_UNAVAILABLE`
ส่วนคำขอที่ไม่มี bearer token ตอบ 401

Tests ต่อ adapters จำลองเพื่อตรวจ HTTP, DTO และ payload ที่มี amount เป็น 1
มี JWT verification จริงผ่าน `JWT_PUBLIC_KEY_FILE`, `JWT_ISSUER` และ `JWT_AUDIENCE`
แล้ว โดยปิด `MOCK_AUTH_ENABLED` ตัวตรวจใช้ public key P-256 ของ signer #182 ตรวจ signature,
issuer, audience, expiry และ claims ก่อนเรียก wallet resolver หรือ admission
Token ที่ถูกปฏิเสธได้ 401; token ที่ผ่านยังได้ 503 จนกว่าจะต่อ adapters ที่เหลือ
มี `SqlRedeemAdmissionStore` และ migration สำหรับ operation/idempotency/outbox แล้ว
ทดสอบ atomic commit, replay, key ชน และ 20 requests พร้อมกันด้วย DB จริง
ยังไม่มี wallet authorization และ deployment resolver จึงยังไม่เปิดรับงานจาก config ค่าเริ่มต้น
อ่านวิธีประกอบ adapters และขอบเขตทดสอบใน [AC4–7](docs/api/redeem.md#durable-admission-and-idempotency-ac47)
อ่านรายละเอียดใน [Redeem admission](docs/coupon-redeem-draft.md)
ส่วน [API contract และ error codes](docs/api/redeem.md) อธิบาย OpenAPI, Postman และผลลัพธ์แต่ละแบบ

### ทดลองด้วย mock token

เปิด mock auth สำหรับ development หรือ test ใน `.env.api`:

```dotenv
MOCK_AUTH_ENABLED=true
MOCK_AUTH_TOKEN=mock-redeem-token
MOCK_AUTH_USER_ID=mock-user
MOCK_AUTH_WALLET_ADDRESS=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

จากนั้นเริ่ม API ด้วย `.venv/bin/reward-api` แล้วส่งคำขอจากอีก terminal:

```bash
curl -i http://127.0.0.1:3000/v1/coupons/redeem \
	-H 'Authorization: Bearer mock-redeem-token' \
	-H 'Idempotency-Key: mock-redeem-key-1' \
	-H 'Content-Type: application/json' \
	-d '{"typeId":"1"}'
```

คำขอตัวอย่างที่ใช้ token ตรงกับค่าที่ตั้งไว้จะผ่าน mock auth แล้วได้ HTTP 503 พร้อมข้อความ
`Redeem admission adapters are not configured.` เพราะยังไม่มีส่วนรับงานจริง
Token ผิดหรือหายได้ 401 ไม่มี operation, outbox หรือธุรกรรมถูกสร้าง
เมื่อ token ตรงแล้ว typeId ผิด, มี field เกิน หรือขาด Idempotency-Key จะได้ 400 ก่อนตรวจส่วนรับงาน

Mock auth เทียบ token ตรงตัวแล้วคืน user และ wallet ที่กำหนดไว้ ไม่ตรวจ JWT, expiry
หรือพิสูจน์ wallet ownership ค่าเริ่มต้นของ flag เป็น `false` และ configuration ปฏิเสธการเปิดใน production
ค่าตัวอย่างด้านบนเป็น defaults ของ mock profile และยังไม่ยืนยัน deployment ใด

### ตรวจ API contract และ Postman

```bash
.venv/bin/python scripts/verify_redeem_api.py
```

คำสั่งนี้ตรวจ OpenAPI ตามมาตรฐาน, ตรวจไฟล์ที่ generate ว่าตรงกับ runtime และตรวจ Postman examples
กับ response schemas โดยรวมอยู่ใน `scripts/verify.py` แล้ว ใช้ `--export` เมื่อเปลี่ยน contract
เพื่อสร้าง [OpenAPI](docs/api/redeem.openapi.json), [collection](postman/redeem.postman_collection.json)
และ [environment](postman/local.postman_environment.json) ใหม่

Postman folder `Local mock` ใช้กับ mock API ในเครื่อง โดยคำขอถูกต้องยังได้ 503
ส่วน `Adapter contract` ใช้ fixtures ที่ script สร้างแยกต่างหากเพื่อตรวจ 202 และ error mapping
เมื่อ Newman อยู่ใน PATH ให้รันทั้งสอง folder ด้วย:

```bash
.venv/bin/python scripts/verify_redeem_api.py --newman newman
```

Script เปิด HTTP servers บน loopback สำหรับแต่ละ folder และส่ง token ของ fixture ให้ Newman
ผลเหล่านี้ไม่ยืนยัน token expiry, wallet ownership, DB commit, idempotency, rate policy หรือ chain จริง

## PostgreSQL สำหรับ local development

เมื่อ Docker daemon ทำงาน:

```bash
docker compose up -d postgres
export DATABASE_URL='postgresql+psycopg://reward:local-development-only@127.0.0.1:5432/reward'
.venv/bin/alembic upgrade head
.venv/bin/reward-api
```

Compose เปิด port เฉพาะ loopback และใช้ credential สำหรับ local เท่านั้น
อย่าใช้คำสั่ง migrations นี้กับฐานข้อมูล production หรือ schema เก่าที่มีข้อมูลโดยยังไม่ review

## ตรวจคุณภาพ

```bash
.venv/bin/python scripts/verify.py
# หรือ make check
```

รัน Ruff, formatting, strict type checks, dependency-boundary checks, API artifact checks และ pytest
Tests ใช้ฐานข้อมูลชั่วคราวจริงและ migration จริง รวม HTTP, pagination, chain/address isolation,
uint256 precision, config precedence, role secrets, redacted logs, rollback และ ABI reference

ตรวจ reference กับ repo contract ที่อยู่ข้างกัน:

```bash
.venv/bin/python scripts/sync_contract_reference.py --check
```

คำสั่งนี้ไม่ attest deployed bytecode และไม่ส่งธุรกรรม ข้อมูล deployment ยัง unverified

## Folder structure

อ่าน [เปรียบเทียบ Layer-based กับ Hybrid Feature-based](docs/layer-based-vs-feature-based.md)
สำหรับเหตุผลการจัดโครงสร้าง หน้าที่แต่ละ layer และหลักแยกส่วนกลาง

ใช้ **`src/` Layout + Hybrid Feature-based** โดยแยก feature ก่อน แล้วแบ่ง
`domain`, `application`, `infrastructure` และ `presentation` ภายในแต่ละ feature
`listings` มี implementation สำหรับอ่านรายการขาย ส่วน `coupons` มี route, DTO และ use case
สำหรับ redeem admission โดยยังไม่มี production adapters

```text
blockchain-reward-platform-be/
├── pyproject.toml                   # Dependencies และ tool configuration
├── uv.lock                          # Dependency lockfile
├── .env*.example                    # ตัวอย่าง environment แยก process
├── alembic.ini                      # ตั้งค่า database migrations
├── migrations/                      # Alembic migrations
├── scripts/                         # ติดตั้งและตรวจระบบ
├── tests/
│   ├── conftest.py                  # Fixtures กลาง
│   ├── unit/                        # Config, logging และ contract reference
│   └── integration/
│       ├── coupons/                  # HTTP ของ redeem โดยใช้ adapters จำลอง
│       ├── listings/                 # HTTP และ database ของ listings
│       ├── test_migrations.py
│       └── test_processes.py
├── docs/                            # Architecture, ผลตรวจ และ SQL เก่า
├── compose.yaml                     # PostgreSQL สำหรับ local
├── Makefile                         # คำสั่ง install/run/check
└── src/
    └── marketplace/
        ├── __init__.py
        ├── main.py                  # ประกอบแอปและ dependencies ของ features
        ├── settings.py              # Config แยกตาม process
        ├── logging_config.py        # JSON logs และปิดข้อมูลลับ
        ├── worker/                  # Service กลางสำหรับส่งธุรกรรม
        │   └── main.py              # ตอนนี้ตรวจ config เท่านั้น
        ├── indexer/                 # Service กลางสำหรับอ่าน chain
        │   └── main.py              # ตอนนี้ตรวจ config เท่านั้น
        ├── listings/
        │   ├── domain/
        │   │   └── entities.py      # Listing และ Marketplace
        │   ├── application/
        │   │   ├── use_cases.py     # Catalog use cases
        │   │   ├── ports.py         # Interface ของ repository
        │   │   ├── dto.py           # ผลลัพธ์จาก use cases
        │   │   └── exceptions.py    # ข้อผิดพลาดระดับ application
        │   ├── infrastructure/
        │   │   ├── models.py        # ORM models ของ listings
        │   │   └── repository.py    # Query ฐานข้อมูลของ listings
        │   └── presentation/
        │       ├── router.py        # HTTP endpoints ของ listings
        │       └── schemas.py       # Request/response models
        ├── coupons/                 # Redeem admission; production adapters ยังไม่พร้อม
        │   ├── domain/
        │   ├── application/
        │   ├── infrastructure/
        │   └── presentation/
        └── infrastructure/          # ของส่วนกลางที่ features ใช้ร่วมกัน
            ├── database.py          # Connection และ session
            └── blockchain/
                └── abi/             # ABI และ contract reference manifest
```

| ชั้นภายใน feature | หน้าที่ |
| --- | --- |
| `domain` | ข้อมูลและกฎธุรกิจที่ backend เป็นเจ้าของ โดยไม่ผูกกับ framework |
| `application` | Use cases, interfaces และผลลัพธ์ระดับ application |
| `infrastructure` | ORM และ adapters ที่เป็นของ feature นั้น |
| `presentation` | HTTP endpoints และ request/response validation |

`listings/infrastructure/` เป็นเจ้าของตาราง projection และ queries ของ listings
ส่วน `infrastructure/` ที่ระดับ package ดูแล connection และ reference assets ที่ใช้ร่วมกัน
จึงไม่มีการ import listings กลับเข้ามาใน shared infrastructure

ลำดับการทำงานของ request:

```text
HTTP → listings/presentation → listings/application → listings/infrastructure → database
```

`main.py` ประกอบ dependencies และส่ง repository ให้ use cases ผ่าน
`listings/application/ports.py` แล้ว inject provider ให้ router
คำสั่ง `reward-api` ชี้ไปที่ `marketplace.main:main` ใน `pyproject.toml`

Dependency ชี้เข้าหาชั้นใน: `presentation → application → domain` ส่วน infrastructure
ทำตาม ports โดย Domain และ Application ไม่ import FastAPI หรือ SQLAlchemy
Import Linter ตรวจขอบเขตทั้ง 6 ข้อผ่าน `make check`
กฎ escrow และ settlement ยังคงอยู่ใน smart contract

แต่ละ layer ของ `coupons/` มี `__init__.py` เพื่อเก็บโครงสร้างเป็น Python package
`main.py` register redeem router แล้ว และ `coupons/infrastructure/mock_auth.py` มี mock token adapter
ส่วน `coupons/infrastructure/jwt_auth.py` ตรวจ access token ES256 สำหรับ AC2
ส่วน API สร้างคูปอง, mint และ wallet/deployment adapters ยังไม่มี implementation

## สิ่งที่ยังต้องตัดสินใจ

อ่าน [architecture](docs/architecture.md) สำหรับ folder structure, dependency rules,
ขอบเขตที่ทำแล้ว และตารางสิ่งที่ยังต้องตัดสินใจ
อ่าน [contract validation](docs/contract-validation.md) ก่อนเพิ่ม transaction endpoint

ประเด็นที่ยังเปิดอยู่: deployment/contract addresses, custody และ signing/KMS, authentication
กับ wallet ownership, points token, queue และ idempotency/nonce policy, finality/reorg policy,
production database/deployment และความลับของ redeem codes

SQL เดิมเก็บใน `docs/legacy/0001_typescript_reference.sql` เพื่อเทียบ requirements เท่านั้น
ไม่อยู่ใน Alembic migration path และไม่ได้อ้างว่าพฤติกรรมเก่าทั้งหมดตรงกับ contract ล่าสุด
