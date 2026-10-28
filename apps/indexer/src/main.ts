import 'reflect-metadata'
import { loadEnvOrExit } from '@libs/config'
import { makeLogger } from '@libs/logger'

const env = loadEnvOrExit('indexer')
const log = makeLogger({ role: 'indexer', level: env.LOG_LEVEL })

// ของจริงมาใน BE-0207 · L8 บังคับว่า indexer รันได้ instance เดียว
log.info({ msg: 'indexer พร้อมใช้งาน', chainId: env.CHAIN_ID, finalityDepth: env.FINALITY_DEPTH })

const tick = setInterval(() => log.debug({ msg: 'ยังไม่ได้อ่าน log · BE-0207' }), 30_000)
for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => { clearInterval(tick); log.info({ msg: 'indexer ปิดแล้ว' }); process.exit(0) })
}
