# Blockchain Reward Platform

Backend monorepo สำหรับระบบแต้มสะสมและตลาดคูปองบน EVM แบ่งเป็น 3 process: HTTP API, worker และ indexer ใช้ TypeScript, NestJS, pnpm workspace และ PostgreSQL connection string

> API มี `GET /healthz` และ coupon types ที่เปิดผ่าน feature flag โดยเชื่อม PostgreSQL และอ่าน supply ผ่าน RPC ได้ ส่วน worker และ indexer ยังไม่ประมวลผลคิวหรือส่งธุรกรรม ดู [วิธีเปิดใช้ BE-0602](docs/BE-0602.md)

## สิ่งที่ต้องมี

- Node.js `>=22.13`
- pnpm `>=10`
- PostgreSQL และ EVM RPC ยังไม่จำเป็นสำหรับการบูต process ปัจจุบัน เพราะระบบตรวจรูปแบบ URL แต่ยังไม่ได้เชื่อมต่อบริการจริง

ตรวจเวอร์ชัน:

```bash
node --version
pnpm --version
```

## ติดตั้งและตั้งค่า

```bash
pnpm install
pnpm env:init
```

`pnpm env:init` จะคัดลอก `.env.example`, `.env.api.example`, `.env.worker.example` และ `.env.indexer.example` โดยไม่เขียนทับไฟล์เดิม ตรวจค่าตาม role ด้านล่างก่อนเริ่ม process สำหรับ coupon types ให้ตั้งค่าเพิ่มเติมตาม [BE-0602](docs/BE-0602.md)

| ไฟล์ | ตัวแปร | ตัวอย่าง/ค่าเริ่มต้น |
|---|---|---|
| `.env` | `DATABASE_URL` | `postgresql://postgres:dev@localhost:5432/reward` |
| `.env` | `LOG_LEVEL` | `debug` (ค่าเริ่มต้น `info`) |
| `.env.api` | `PORT` | `3000` (ค่าเริ่มต้น `3000`) |
| `.env.worker` | `RPC_URL`, `CHAIN_ID` | `http://localhost:8545`, `31337` |
| `.env.worker` | `KMS_KEY_ARN`, `KMS_REGION` | ใช้ ARN และ region ของ KMS ที่ต้องการ |
| `.env.indexer` | `RPC_URL`, `CHAIN_ID` | `http://localhost:8545`, `31337` |
| `.env.indexer` | `FINALITY_DEPTH` | `12` |

ตัวอย่างรูปแบบไฟล์:

```dotenv
# .env
DATABASE_URL=postgresql://postgres:dev@localhost:5432/reward
LOG_LEVEL=debug
```

```dotenv
# .env.api
PORT=3000
```

```dotenv
# .env.worker
RPC_URL=http://localhost:8545
CHAIN_ID=31337
KMS_KEY_ARN=arn:aws:kms:ap-southeast-1:000000000000:key/dev
KMS_REGION=ap-southeast-1
```

```dotenv
# .env.indexer
RPC_URL=http://localhost:8545
CHAIN_ID=31337
FINALITY_DEPTH=12
```

ค่า ARN ด้านบนเป็นเพียงตัวอย่าง อย่าใส่ credential จริงลง Git และอย่าใส่ `KMS_KEY_ARN` หรือ `KMS_REGION` ใน `.env` หรือ environment ที่แชร์กับ API เพราะ API จะปฏิเสธการเริ่มทำงาน ไฟล์ `.env*` ที่เป็นค่าท้องถิ่นถูก ignore โดย Git ยกเว้น `.env.example`

แต่ละ process โหลด `.env` ก่อน แล้วโหลด `.env.<role>`; ตัวแปรที่กำหนดใน shell หรือ CI มีลำดับความสำคัญสูงกว่าไฟล์ ตรวจ environment ตอนเริ่ม process และหยุดทันทีเมื่อค่าขาดหรือผิดรูปแบบ

## เริ่มใช้งาน

รันทั้งสาม process พร้อมกัน:

```bash
pnpm dev
```

หรือรันแยก:

```bash
pnpm dev:api
pnpm dev:worker
pnpm dev:indexer
```

API พร้อมใช้งานที่ `http://localhost:3000`. ตรวจสุขภาพด้วย:

```bash
curl http://localhost:3000/healthz
```

คาดว่าจะได้:

```json
{"ok":true,"role":"api"}
```

กด `Ctrl+C` เพื่อหยุด process

## ตรวจคุณภาพ

```bash
pnpm typecheck
pnpm lint
pnpm test
```

## โครงสร้าง

```text
apps/api       NestJS HTTP API และ health check
apps/worker    process สำหรับงาน background (ปัจจุบันยังเป็น placeholder)
apps/indexer   process สำหรับอ่านข้อมูล chain (ปัจจุบันยังเป็น placeholder)
libs/config    โหลด dotenv และตรวจ environment แยกตาม role
libs/domain    types และ logic ของ domain ที่ใช้ร่วมกัน
libs/logger    Pino logger พร้อม redact ข้อมูลสำคัญ
test/          unit tests สำหรับ config และ logger
```

ห้าม import ข้ามโฟลเดอร์ `apps/`; โค้ดที่ใช้ร่วมกันควรอยู่ใน `libs/` เพื่อแยกขอบเขตของแต่ละ process
