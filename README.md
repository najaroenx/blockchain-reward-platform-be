# Blockchain Reward Platform — Python backend

Backend สำหรับ coupon marketplace บน EVM ใช้ Python 3.12, FastAPI, Pydantic,
SQLAlchemy, Alembic และ PostgreSQL ใช้ `src/marketplace/` แบบ Hybrid Feature-based
แยก feature ก่อน แล้วแบ่ง layers ภายใน และรันเป็น modular monolith

สถานะ: API, config แยก process, logging, database migrations และ catalog สำหรับอ่าน projection
ใช้งานได้ ส่วน worker/indexer มีคำสั่งตรวจ config แต่ **ยังไม่ส่งธุรกรรมหรืออ่าน chain จริง**
ระบบเดิมก็ยังไม่มีส่วนประมวลผลเหล่านั้น ไม่มี purchase/redeem endpoint เปิดไว้ในรอบนี้
มี login/session/lockout และ ES256 JWT signer สำหรับ merchant-admin แล้ว

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
deployment, worker, indexer, catalog หรือ JWT signer พร้อมใช้งาน

## Config แยก process

โหลด `.env` → `.env.<role>` → environment จาก shell/CI โดยค่าด้านหลังมีลำดับสูงกว่า
การโหลดไม่เปลี่ยน environment กลาง และไม่อ่านไฟล์ของ role อื่น

| ไฟล์ | ตัวแปร |
| --- | --- |
| `.env` | `DATABASE_URL`, `APP_ENV`, `LOG_LEVEL` |
| `.env.api` | `HOST`, `PORT`, `LOGIN_IP_LIMIT`, `LOGIN_IP_WINDOW_SECONDS`, `JWT_PRIVATE_KEY_FILE`, `JWT_ISSUER`, `JWT_AUDIENCE` |
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
ส่วน wallet authorization, idempotency และการส่งธุรกรรมยังต้องออกแบบตามการตัดสินใจด้านล่าง

## Merchant login

`POST /v1/auth/login` รับเฉพาะ email/password ตรวจ Argon2id และบันทึก session/attempt/lockout
อีเมลไม่มีในระบบ, รหัสผิด และ admin ถูกปิดใช้ ตอบ `401 INVALID_CREDENTIALS` ด้วยข้อความเดียวกัน
ผิดครบ 5 ครั้งใน 15 นาทีจะล็อกบัญชี และมี rate limit ต่อ peer IP ค่าเริ่มต้น 20 คำขอใน 60 วินาที

API เซ็น JWT จริงด้วย ES256 เมื่อกำหนด `JWT_PRIVATE_KEY_FILE`, `JWT_ISSUER` และ `JWT_AUDIENCE` ครบ
อ่าน PEM จากไฟล์ที่ mount ผ่านระบบ secrets ตอนเริ่ม process และเผยแพร่ public key ที่ `/.well-known/jwks.json`
ถ้าไม่ตั้งค่าทั้งสามใน development/test จะตอบ `503 AUTH_UNAVAILABLE` โดยไม่สร้าง session หรือ cookie ค้าง
Production บังคับให้ตั้งค่า signer ครบและใช้ HTTPS issuer ส่วน refresh token ส่งผ่าน Secure/HttpOnly cookie
และเก็บใน DB เฉพาะ SHA-256 อ่านรายละเอียดใน [JWT signing](docs/jwt-signing.md)
การ login ไม่ได้ยืนยัน wallet ownership หรือสิทธิ์ signer บน chain

อ่าน [ขอบเขต login และผลตรวจ](docs/auth-login.md), [OpenAPI](docs/api/login.openapi.json)
และ [Postman collection](postman/login.postman_collection.json)

```bash
.venv/bin/python scripts/verify_login_api.py
# ใช้ --export เมื่อตั้งใจ regenerate API artifacts
```

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
ชุดตรวจ auth/migration ผ่าน PostgreSQL 17.11 แล้ว รวม Postman ผ่าน HTTP จริง
ดูคำสั่งสร้างฐานข้อมูลทดสอบแยกและรันซ้ำใน [merchant login](docs/auth-login.md#repeatable-checks)

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
`listings` มี implementation สำหรับอ่านรายการขาย, `auth` ดูแล login/session/lockout
ส่วน `coupons` เป็นโครง package สำหรับเพิ่ม use cases ต่อไปใน branch นี้

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
│       ├── auth/                     # Login, concurrency และ session constraints
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
        ├── worker.py                # ตอนนี้ตรวจ config เท่านั้น
        ├── indexer.py               # ตอนนี้ตรวจ config เท่านั้น
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
        ├── coupons/                 # โครงสร้างเท่านั้น ยังไม่มี use case หรือ endpoint
        │   ├── domain/
        │   ├── application/
        │   ├── infrastructure/
        │   └── presentation/
        ├── auth/                    # Merchant login, session และ lockout
        │   ├── domain/
        │   ├── application/         # Login use case และ TokenSigner port
        │   ├── infrastructure/      # Argon2id, SQL models และ repository
        │   └── presentation/        # Login route และ HTTP schemas
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
ยังไม่มี API สร้างคูปองหรือ mint และยังไม่ได้ register router ของ coupons ใน `main.py`
เพิ่ม implementation ภายใน feature นั้นเมื่อกำหนด use case และ authorization แล้ว

## สิ่งที่ยังต้องตัดสินใจ

อ่าน [architecture](docs/architecture.md) สำหรับ folder structure, dependency rules,
ขอบเขตที่ทำแล้ว และตารางสิ่งที่ยังต้องตัดสินใจ
อ่าน [contract validation](docs/contract-validation.md) ก่อนเพิ่ม transaction endpoint

ประเด็นที่ยังเปิดอยู่: deployment/contract addresses, custody และ signing/KMS, authentication
กับ wallet ownership, points token, queue และ idempotency/nonce policy, finality/reorg policy,
production database/deployment และความลับของ redeem codes

SQL เดิมเก็บใน `docs/legacy/0001_typescript_reference.sql` เพื่อเทียบ requirements เท่านั้น
ไม่อยู่ใน Alembic migration path และไม่ได้อ้างว่าพฤติกรรมเก่าทั้งหมดตรงกับ contract ล่าสุด
