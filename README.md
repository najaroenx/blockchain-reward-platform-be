# Blockchain Reward Platform — NestJS backend

Backend สำหรับ coupon marketplace บน EVM กำลังย้าย stack กลับไปเป็น NestJS/TypeScript
จาก Python backend เดิม (ดู `legacy-python/` — ยังอยู่ครบ ใช้เป็นอ้างอิง business logic/schema
ที่ verify กับ smart contract แล้ว ดู `AGENTS.md` ก่อน implement ทุก feature)

โครงสร้างเป็น Nest monorepo สามแอป ตรงกับสามบทบาทเดิม:

```text
apps/
  api/      HTTP API
  worker/   transaction worker (ยังไม่ implement)
  indexer/  chain indexer (ยังไม่ implement)
```

สถานะ: scaffold เปล่า ยังไม่มี business logic ใด ๆ ย้ายมาจาก `legacy-python/`

## ติดตั้ง

ต้องมี Node.js (เทสกับ v22) และ npm

```bash
npm install
```

## รันแต่ละแอป

```bash
npm run start:dev -- api
npm run start:dev -- worker
npm run start:dev -- indexer
```

## Test

```bash
npm run test       # unit tests ทุกแอป
npm run test:e2e:api
npm run test:e2e:worker
npm run test:e2e:indexer
npm run lint
npm run build
```
