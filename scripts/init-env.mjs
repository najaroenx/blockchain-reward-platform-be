// คัดลอก .env*.example เป็น .env* ถ้ายังไม่มี · ไม่เขียนทับของเดิม
import { existsSync, copyFileSync } from 'node:fs'
let made = 0
for (const suffix of ['', '.api', '.worker', '.indexer']) {
  const src = `.env${suffix}.example`
  const dst = `.env${suffix}`
  if (!existsSync(src) || existsSync(dst)) continue
  copyFileSync(src, dst)
  console.log(`  สร้าง ${dst}`)
  made++
}
console.log(made ? 'เสร็จแล้ว' : 'มีครบอยู่แล้ว ไม่ได้แตะอะไร')
