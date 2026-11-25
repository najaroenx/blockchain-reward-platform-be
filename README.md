# Blockchain Reward Platform — Python backend

Backend สำหรับตลาดคูปองและแต้มสะสมบน EVM ใช้ Python 3.12, FastAPI, Pydantic,
SQLAlchemy, Alembic และ PostgreSQL ใช้ `src/marketplace/` แบบ Hybrid Feature-based
แยก feature ก่อน แล้วแบ่ง layers ภายใน และรันเป็น modular monolith

`reward-api` รับคำขอ HTTP และอ่านข้อมูลที่บันทึกไว้ `reward-worker` ประมวลผลงานจากคิว
ส่วน `reward-indexer` เป็น entry point สำหรับอ่านเหตุการณ์บน chain เพื่ออัปเดตข้อมูลที่ API ใช้อ่าน
ดู [กติกาการพัฒนา](#development-guidelines) ก่อนเพิ่มหรือแก้ feature

สถานะ: API, config แยก process, logging, database migrations และ catalog สำหรับอ่าน projection
ใช้งานได้ ส่วน CLI worker/indexer **ยังไม่เปิด execution production**
มี redeem handler/projector ที่ตรวจ integration บน local chain แล้ว ดู [สถานะ redeem](docs/redeem-state.md)
มี route และ use case สำหรับรับคำขอ redeem แล้ว แต่ค่าเริ่มต้นยังไม่รับงานจนกว่าจะต่อ adapters
สำหรับ wallet mapping, deployment ที่ยืนยันแล้ว และการบันทึก admission จริง
มีตัวตรวจ JWT แบบ ES256 จาก #182 แล้ว ยังไม่มี purchase endpoint
มี coupon-type draft API (BE-0602 Phase 1) ที่บันทึก/อ่านข้อมูลใน database เท่านั้น ปิดด้วยค่าเริ่มต้น
ผ่าน `COUPON_TYPES_ENABLED` และยังไม่ส่งธุรกรรม `createCouponType` ขึ้น chain
มี login/session/lockout และ ES256 JWT signer สำหรับ merchant-admin แล้ว (#177/#182) ใช้
`JWT_ISSUER`/`JWT_AUDIENCE` ร่วมกับตัวตรวจ JWT ของ redeem ข้างต้น
มี refresh-token rotation และการจับ token เก่าใช้ซ้ำแล้ว โดยเพิกถอนทั้ง session family (#178)

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
| `.env.api` | `HOST`, `PORT`, `COUPON_TYPES_ENABLED`, `MOCK_AUTH_ENABLED`, `MOCK_AUTH_TOKEN`, `MOCK_AUTH_USER_ID`, `MOCK_AUTH_WALLET_ADDRESS`, `LOGIN_IP_LIMIT`, `LOGIN_IP_WINDOW_SECONDS`, `JWT_PRIVATE_KEY_FILE`, `JWT_PUBLIC_KEY_FILE`, `JWT_ISSUER`, `JWT_AUDIENCE` |
| `.env.worker` | `RPC_URL`, `CHAIN_ID`, `KMS_KEY_ARN`, `KMS_REGION`, `WORKER_LEASE_SECONDS` |
| `.env.indexer` | `RPC_URL`, `CHAIN_ID`, `FINALITY_DEPTH` |

API และ indexer ปฏิเสธ KMS variables ทั้งจากไฟล์ shared และ environment ที่ส่งเข้ามา
ค่า RPC, chain ID และ KMS ในตัวอย่างเป็น format examples ไม่ใช่ deployment ที่ยืนยันแล้ว

```bash
.venv/bin/reward-worker --check
.venv/bin/reward-indexer --check
```

`--check` ตรวจรูปแบบ config และรายงาน chain ID ของแต่ละ process

Worker runtime ของ #150 อ่าน PostgreSQL outbox เฉพาะ `operation.kind` ที่ลงทะเบียนใน
`marketplace.worker.main.main(handlers=...)` ตัวเรียกส่ง mapping จากชนิด operation ไปยัง handler
ที่รับ `(connection, job)` โดยเขียนผลผ่าน connection ที่ส่งให้ เพื่อ commit พร้อมการรับงานสำเร็จ
Worker หลาย process จองงานแยกกันด้วย `FOR UPDATE SKIP LOCKED` และบันทึก lease กับ retry ใน
`worker_attempt` ตั้งอายุ lease ด้วย `WORKER_LEASE_SECONDS` ใน `.env.worker` แล้วเริ่ม Worker ใหม่
ค่าเริ่มต้นคือ 30 วินาที

เมื่อ lease หมดอายุ Worker ที่ poll จะปิด attempt เดิมและ claim งานใหม่ใน transaction เดียวกัน
เงื่อนไขคืองานยังเป็น `PENDING` และยังไม่มีการบันทึกว่าเริ่มส่งธุรกรรม ขั้น `PREPARED` มี hash
ของธุรกรรมที่เซ็นแล้วและยัง claim ใหม่ได้ ตั้งแต่ `SUBMITTED` เป็นต้นไป Worker จะรอให้ Indexer
ตรวจผลจาก chain ส่วนการเซ็นและส่งธุรกรรมอยู่ใน #151 และ #152

ตั้ง `TEST_POSTGRES_URL` แล้วรัน `.venv/bin/pytest -q tests/integration/test_worker_outbox.py`
เพื่อทดสอบการหยิบงาน, retry และต่ออายุ lease บน PostgreSQL

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

Catalog ใช้ `coupon_expires_at` ของ listing projection และคำนวณสถานะทุกครั้งที่อ่าน
รายการยัง active ในวินาทีที่ตรงกับ `expireDate` และหายจากหน้าตลาดตั้งแต่วินาทีถัดไป
หน้า detail ยังเปิดได้และแสดง `active: false` โดยค่า `active` ที่ index จาก chain ยังอยู่ในฐานข้อมูล
ตอนสร้าง projection หรือ backfill ให้เติม `coupon_expires_at` จาก `Coupon.getCouponData(typeId)`
ของ deployment ที่ตรวจสอบแล้ว โดยใช้ Unix seconds หากเขียน SQL ใน SQLite โดยตรง ให้เติมเลขศูนย์ด้านหน้าจนครบ 78 หลัก
แถวเก่าที่ค่านี้ยังเป็น `NULL` จะปรากฏเฉพาะในหน้า detail ด้วย `active: false`

## Coupon-type drafts (BE-0602 Phase 1, ปิดด้วยค่าเริ่มต้น)

```text
POST /api/v1/coupon-types
GET  /api/v1/coupon-types?limit=25&offset=0
GET  /api/v1/coupon-types/{coupon_type_id}
```

Route นี้ลงทะเบียนเฉพาะเมื่อ `COUPON_TYPES_ENABLED=true` (ค่าเริ่มต้นคือ `false`) POST ต้องมี
`Idempotency-Key` เสมอ คำขอซ้ำด้วย key และ body เดิมจะได้ response เดิม ส่วน key เดิมที่ body
ต่างจะได้ `409 idempotency_key_conflict` ชื่อซ้ำได้ `409 duplicate_coupon_type_name` การตรวจ
`start_date < expire_date` ล้อ `InvalidDateRange` ของ `BaseCoupon.sol`

ทุกแถวมี `chain_type_id: null`, `issued_count: 0` และ `remaining_supply == total_supply` เสมอ
เพราะ API นี้ไม่ส่งธุรกรรมขึ้น chain — การ submit `createCouponType` จริงต้องรอการตัดสินใจเรื่อง
deployment, signing, authentication และ queue ใน [architecture](docs/architecture.md) ก่อน
ดู [contract validation](docs/contract-validation.md) สำหรับหลักฐานที่ยืนยันกับ contract แล้ว

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

`POST /v1/auth/refresh` อ่าน refresh cookie แล้วออก JWT และ cookie ใบใหม่
หากใช้ token เก่าที่หมุนแล้วซ้ำ จะตอบ `401 SESSION_COMPROMISED` และเพิกถอนทั้ง family
อ่าน [refresh behavior และ acceptance](docs/auth-refresh.md) สำหรับ error codes และ migration T14

## PostgreSQL สำหรับ local development

ใช้ PostgreSQL ที่รันอยู่ หรือเปิด PostgreSQL สำหรับ local development แยกด้วย Docker

```bash
docker run --detach --name reward-postgres-dev \
  --env POSTGRES_USER=reward \
  --env POSTGRES_PASSWORD=local-development-only \
  --env POSTGRES_DB=reward \
  --publish 127.0.0.1:5432:5432 \
  --volume reward-postgres-dev:/var/lib/postgresql/data \
  postgres:17-alpine
docker exec reward-postgres-dev pg_isready -h 127.0.0.1 -U reward -d reward
```

เมื่อ PostgreSQL พร้อม ให้ตั้ง `DATABASE_URL` สำหรับรัน API บนเครื่องโดยตรง

```bash
export DATABASE_URL='postgresql+psycopg://reward:local-development-only@127.0.0.1:5432/reward'
.venv/bin/alembic upgrade head
.venv/bin/reward-api
```

อย่าใช้คำสั่ง migrations นี้กับฐานข้อมูล production หรือ schema เก่าที่มีข้อมูลโดยยังไม่ review
ชุดตรวจ auth/migration ผ่าน PostgreSQL 17.11 แล้ว รวม Postman ผ่าน HTTP จริง
ดูคำสั่งสร้างฐานข้อมูลทดสอบแยกและรันซ้ำใน [merchant login](docs/auth-login.md#repeatable-checks)

## รัน Backend ด้วย Docker Compose

เว้น `REWARD_DATABASE_URL` ว่างใน `.env` เพื่อให้สคริปต์เปิด PostgreSQL 17 ใน Compose
และเก็บข้อมูลใน Docker volume `reward_postgres` ฐานข้อมูลนี้เข้าถึงได้จาก Backend containers

ถ้ามี PostgreSQL ที่รันแยก ให้ตั้ง `REWARD_DATABASE_URL` ใน `.env` เป็น URL ที่ container เข้าถึงได้

```sh
REWARD_DATABASE_URL=postgresql+psycopg://reward:local-development-only@postgres.example:5432/reward
```

ใช้ Docker Compose และ Python 3 เรียกสคริปต์จาก root ของ repo Backend

```sh
./start-backend.sh
curl --fail --retry 15 --retry-all-errors --retry-delay 1 http://127.0.0.1:3000/readyz
```

Compose build image จาก source ใน checkout นี้ แล้วรัน Alembic กับฐานข้อมูลที่เลือกก่อนเปิด
API, Worker และ Indexer โดย Worker กับ Indexer รัน `--check` แล้วจบเมื่อ config ผ่าน
สคริปต์เตรียม Docker network
`blockchain-reward-platform_loaffinity-singlenode` ให้ทั้งสอง process
เรียก Besu ที่ `http://besu-node:8545` ตั้ง `REWARD_CHAIN_NETWORK` หรือ
`REWARD_RPC_URL` ใน `.env` ถ้าชื่อ network หรือ RPC ของทีมต่างจากค่าเริ่มต้น

ปิดชุด Backend รวมถึง PostgreSQL local ด้วย `docker compose --profile local-db down`

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
ส่วน `coupons` มีทั้ง coupon-type draft API (CRUD ในฐานข้อมูล ปิดด้วยค่าเริ่มต้น) และ route, DTO,
use case สำหรับ redeem admission โดย redeem ยังไม่มี production adapters

![ภาพตัวอย่าง Feature-based Clean Architecture แสดง Domain, Application, Presentation และ Infrastructure](docs/images/feature-based-clean-architecture.png)

ภาพใช้ `loyalty_points` เป็นตัวอย่างของ Ports and Adapters ผังด้านล่างใช้ `listings` แสดงตำแหน่งไฟล์จริง
ดู [Development guidelines](#development-guidelines) สำหรับกฎการวางโค้ดและทิศทางการ import

```text
src/marketplace/
├── main.py                                      # ประกอบ HTTP API และ dependencies
├── settings.py                                  # Config แยกตาม process
├── logging_config.py                            # JSON logs และการปิดข้อมูลลับ
├── infrastructure/                              # Database engine, ตารางส่วนกลาง และ ABI อ้างอิง
├── auth/                                        # Login, JWT และ refresh session
├── coupons/                                     # Coupon-type drafts และ redeem
├── deposits/                                    # คำขอฝากเงินและ payment instructions
├── merchants/                                   # สมัครร้านค้าและเข้าคิว provision
├── operations/                                  # อ่านสถานะ ประวัติ และรายละเอียดงาน
├── point_types/                                 # รับคำขอสร้างชนิดแต้ม
├── listings/
│   ├── domain/entities/listing.py               # Listing และ Marketplace
│   ├── application/interfaces/listings.py       # ListingReader (ABC)
│   ├── application/use_cases/catalog.py         # Catalog use case
│   ├── infrastructure/models.py                 # Projection models
│   ├── infrastructure/repositories/listings.py # SqlListingReader
│   ├── presentation/router.py
│   └── presentation/schemas.py
├── worker/
│   ├── main.py                                  # ประกอบ dependencies ของ CLI
│   ├── domain/entities/job.py                   # ClaimedJob และกติกา retry
│   ├── application/interfaces/outbox.py         # OutboxRepository (ABC)
│   ├── application/use_cases/process_outbox.py  # OutboxWorker
│   ├── infrastructure/repositories/outbox.py    # PostgreSQL claims, lease และการปิดงาน
│   └── presentation/cli.py
└── indexer/
    ├── main.py                                  # ประกอบ dependencies ของ CLI
    ├── domain/entities/deployment.py
    ├── application/interfaces/deployments.py
    ├── application/use_cases/check_deployment.py
    ├── infrastructure/deployment_file.py
    └── presentation/cli.py
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
`listings/application/interfaces/listings.py` แล้ว inject provider ให้ router
คำสั่ง `reward-api` ชี้ไปที่ `marketplace.main:main` ใน `pyproject.toml`

Dependency ชี้เข้าหาชั้นใน: `presentation → application → domain` ส่วน infrastructure
ทำตาม ports โดย Domain และ Application ไม่ import FastAPI หรือ SQLAlchemy
Import Linter ตรวจขอบเขตทั้ง 6 ข้อในทุก package ที่มีเลเยอร์ผ่าน `make check`
กฎ escrow และ settlement ยังคงอยู่ใน smart contract

`main.py` register ทั้ง coupon-type draft router (เมื่อ `COUPON_TYPES_ENABLED=true`), redeem router
และ auth router (login + JWKS)
`coupons/infrastructure/repositories/coupon_types.py` เก็บ draft กับ idempotency key ของมันเองในฐานข้อมูล แยกจาก
`infrastructure/mock_auth.py` ที่มี mock token adapter และ `infrastructure/jwt_auth.py` ที่ตรวจ
access token ES256 สำหรับ AC2 ส่วน mint และ wallet/deployment adapters ของ redeem ยังไม่มี implementation

## Development guidelines

### โครงสร้างสำหรับ feature ใหม่

ใช้ **Feature-Based Clean Architecture** กับทุก feature ใหม่ เก็บโค้ดเฉพาะของ feature ไว้ใต้
`src/marketplace/<feature_name>/` และจัดให้ครบสี่เลเยอร์ตามผังนี้ ใช้ `src/marketplace/infrastructure/`
สำหรับกลไกที่หลาย feature ใช้ร่วมกัน เช่น database engine และการจอง idempotency key

```text
src/marketplace/<feature_name>/
├── presentation/
│   ├── router.py
│   └── schemas.py
├── application/
│   ├── use_cases/
│   │   └── create_<feature_name>.py
│   └── interfaces/
│       └── <feature_name>_repository.py
├── domain/
│   ├── entities/
│   │   └── <feature_name>.py
│   └── exceptions.py
└── infrastructure/
    ├── models.py
    └── repositories/
        └── postgres_<feature_name>_repository.py
```

### หน้าที่และขอบเขตของแต่ละเลเยอร์

- **`domain`** เก็บ Entity, Value Object, กฎธุรกิจที่ Backend เป็นเจ้าของ และกฎเปลี่ยนสถานะ
  ให้ Entity ตรวจความถูกต้องของกฎตัวเองตอนสร้างหรือเปลี่ยนสถานะ เขียนเป็น Pure Python
  ใช้ Python standard library และโค้ดภายใน Domain แยกการเข้าถึง DB, network, SQLAlchemy, FastAPI และ Pydantic ไปไว้ในเลเยอร์ที่รับผิดชอบ
- **`application`** ให้ Use Case ประสานขั้นตอน เรียก Domain ให้ตัดสินตามกติกา และเรียก port เมื่อต้องเข้าถึงระบบภายนอก
  ประกาศ port ใน `application/interfaces/` ด้วย `abc.ABC` และ `@abstractmethod` เช่น `UserRepositoryPort`
  ใช้ชนิดข้อมูลของ Domain หรือ Application ในสัญญาของ port และเก็บรายละเอียด DB ไว้ใน adapter
- **`infrastructure`** เก็บ ORM models, คำสั่ง SQL และ adapters ให้ repository สืบทอดและ implement port ที่ประกาศใน Application
  เช่น `PostgresUserRepositoryAdapter(UserRepositoryPort)` แปลง ORM model เป็น Domain Entity ตอนอ่าน และแปลงกลับตอนเขียน
- **`presentation`** รับ HTTP request หรือคำสั่ง CLI ใช้ Pydantic หรือ DTO ตรวจรูปแบบ request และ response
  รับ Use Case ที่ประกอบแล้ว ส่งพารามิเตอร์ให้ Use Case และแปลงผลให้ตรงกับช่องทางเรียกใช้ ให้ Domain เป็นผู้ตัดสินกฎธุรกิจ

### ทิศทางการ import และการประกอบ dependencies

```text
presentation ──> application ──> domain
infrastructure ──> application (interfaces)
infrastructure ──> domain
```

จำกัด imports ระหว่างเลเยอร์ตามผัง ประกอบ adapter เข้ากับ port ที่ entry point เช่น `marketplace/main.py`
หรือ CLI entry point จากนั้นส่ง Use Case ที่ประกอบแล้วให้ router หรือ CLI handler

### ทดสอบ Use Case ผ่าน Repository port

- ให้ Use Case รับ repository ผ่าน constructor และให้ `main.py` ประกอบ implementation ตัวอย่าง `Catalog`
  ในโค้ดปัจจุบันรับ `ListingReader` ซึ่งใช้ `SqlListingReader` อ่านจากฐานข้อมูลเมื่อรันจริง [Catalog](src/marketplace/listings/application/use_cases/catalog.py)
- ใน unit test ส่ง Fake Repository ที่คืนข้อมูลตามกรณีทดสอบ เช่น คิวว่าง หรือมีงานที่ handler ทำไม่สำเร็จ
  ใช้ integration test บน PostgreSQL เพื่อตรวจการจองงานพร้อมกัน และการ commit หรือ rollback ของ handler กับผลในคิว
- `worker/main.py` สร้าง `WorkerOutbox` แล้วส่งให้ `OutboxWorker` ผ่าน constructor
  ให้ handler กับ `finish()` ใช้ transaction ร่วมกัน ดูการส่ง Fake Repository ใน [Worker unit tests](tests/unit/test_worker_use_case.py)
  [Worker entry point](src/marketplace/worker/main.py) · [Worker use case](src/marketplace/worker/application/use_cases/process_outbox.py)

### รักษา transaction ของ API และ Worker

- เพิ่ม Alembic migration เมื่อเปลี่ยนตาราง คอลัมน์ หรือ constraint แล้วทดสอบ upgrade จาก schema ก่อนหน้าโดยมีข้อมูลเดิมอยู่ในฐานข้อมูล
- เมื่อ API รับงานเข้าคิว ให้ commit `Operation` และ `Outbox` ใน transaction เดียวกัน
  ถ้า endpoint ใช้ `Idempotency-Key` ให้บันทึก response สำหรับคำขอซ้ำใน transaction เดียวกันด้วย
- เพิ่มชนิดงานใน `OperationKind` ก่อนลงทะเบียน handler ใช้ `WorkerOutbox` เพื่อจองงาน ต่อ lease และบันทึกผล
  เขียนผลของ handler ผ่าน `connection` ที่ runtime ส่งให้ เพื่อให้ผลและการปิดงาน commit หรือ rollback พร้อมกัน
  [WorkerOutbox](src/marketplace/worker/infrastructure/repositories/outbox.py) · [Worker runtime](src/marketplace/worker/application/use_cases/process_outbox.py)

### ตรวจ smart contract ก่อนลงมือ

- เทียบ issue กับ contract source, interface หรือ ABI และ tests ของ commit ที่เกี่ยวข้อง ตรวจ deployment, ผู้เรียกและผู้เซ็น,
  สิทธิ์ของ wallet, การเปลี่ยนสถานะ, event และขอบเขต transaction หากพบความต่าง ให้ระบุไฟล์อ้างอิงก่อนกำหนดขอบเขตงาน [AGENTS.md](AGENTS.md)
- รักษากฎที่ contract เป็นเจ้าของ เช่น escrow และ settlement ไว้บน chain ให้ Backend ดูแล authentication,
  wallet authorization, การส่งธุรกรรม, idempotency และการอ่าน events ตามพฤติกรรมที่ยืนยันแล้ว

### ตรวจงานก่อนส่ง PR

1. รัน `make check` เพื่อตรวจ lint, types, import boundaries, API artifacts และ tests
2. หากแก้ query หรือ transaction ที่พึ่ง PostgreSQL เช่น `FOR UPDATE SKIP LOCKED` ให้รัน integration tests กับ PostgreSQL
   สำหรับทดสอบที่ตั้งผ่าน `TEST_POSTGRES_URL` ดู [เทสต์ Worker](tests/integration/test_worker_outbox.py)
3. หากเปลี่ยน API contract ให้อัปเดต OpenAPI และ tests ที่เกี่ยวข้องใน PR เดียวกัน

## สิ่งที่ยังต้องตัดสินใจ

อ่าน [architecture](docs/architecture.md) สำหรับ folder structure, dependency rules,
ขอบเขตที่ทำแล้ว และตารางสิ่งที่ยังต้องตัดสินใจ
อ่าน [contract validation](docs/contract-validation.md) ก่อนเพิ่ม transaction endpoint

ประเด็นที่ยังเปิดอยู่: deployment/contract addresses, custody และ signing/KMS, authentication
กับ wallet ownership, points token, queue และ idempotency/nonce policy, finality/reorg policy,
production database/deployment และความลับของ redeem codes

SQL เดิมเก็บใน `docs/legacy/0001_typescript_reference.sql` เพื่อเทียบ requirements เท่านั้น
ไม่อยู่ใน Alembic migration path และไม่ได้อ้างว่าพฤติกรรมเก่าทั้งหมดตรงกับ contract ล่าสุด
