import 'reflect-metadata'
import { loadEnvOrExit } from '@libs/config'
import { makeLogger } from '@libs/logger'

const env = loadEnvOrExit('worker')
const log = makeLogger({ role: 'worker', level: env.LOG_LEVEL })

// ของจริงมาใน BE-0103 · ตอนนี้แค่พิสูจน์ว่า process ขึ้นได้และอ่าน config ถูก
log.info({ msg: 'worker พร้อมใช้งาน', chainId: env.CHAIN_ID, kms: 'ตั้งค่าแล้ว' })
// ห้าม log ค่า ARN หรือ key ref ออกมา แม้จะไม่ใช่ตัวกุญแจเอง

const tick = setInterval(() => log.debug({ msg: 'ยังไม่มีคิวงาน · BE-0103' }), 30_000)
for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => { clearInterval(tick); log.info({ msg: 'worker ปิดแล้ว' }); process.exit(0) })
}
