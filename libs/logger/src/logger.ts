import pino, { type Logger } from 'pino'
import { redactDeep } from './redact.js'

export type { Logger }

export function makeLogger(opts: { role: string; level: string }): Logger {
  return pino({
    level: opts.level,
    base: { role: opts.role },
    formatters: {
      // ทุกบรรทัดผ่านตรงนี้ ไม่มีทางเลี่ยง
      log: (o) => redactDeep(o) as Record<string, unknown>,
    },
    redact: { paths: ['req.headers.authorization', 'req.headers.cookie'], censor: '[redacted]' },
  })
}
