import { FORBIDDEN, SCHEMA, type Env, type Role } from './env.schema.js'
import { loadDotenv } from './dotenv.js'

export class EnvError extends Error {
  constructor(readonly role: Role, readonly problems: string[]) {
    super(`config ของ ${role} ไม่ถูกต้อง\n` + problems.map((p) => `  - ${p}`).join('\n'))
    this.name = 'EnvError'
  }
}

/**
 * อ่าน env แล้วตรวจให้จบตั้งแต่ตอน boot
 * ขาดตัวไหนให้ตายทันทีพร้อมบอกชื่อตัวแปร ไม่ใช่ไปตายตอนรับ request แรก
 */
export function loadEnv<R extends Role>(role: R, src: NodeJS.ProcessEnv = process.env): Env<R> {
  const problems: string[] = []

  for (const key of FORBIDDEN[role]) {
    if (src[key] !== undefined && src[key] !== '') {
      problems.push(`${key} ต้องไม่มีใน environment ของ ${role} · ตัวแปรนี้เป็นของ worker เท่านั้น`)
    }
  }

  const parsed = SCHEMA[role].safeParse(src)
  if (!parsed.success) {
    for (const i of parsed.error.issues) {
      const key = i.path.join('.') || '(ทั้งก้อน)'
      problems.push(i.code === 'invalid_type' && i.message.includes('expected')
        ? `${key} ขาดหรือผิดชนิด · ${i.message}`
        : `${key} · ${i.message}`)
    }
  }

  if (problems.length > 0) throw new EnvError(role, problems)
  return parsed.data as Env<R>
}

/** เรียกจาก main.ts ก่อนอย่างอื่น · พังแล้วออกด้วย exit code 1 ไม่ใช่ throw ลอยๆ */
export function loadEnvOrExit<R extends Role>(role: R): Env<R> {
  try {
    loadDotenv(role)
    return loadEnv(role)
  } catch (e) {
    process.stderr.write(`\n${e instanceof Error ? e.message : String(e)}\n\n`)
    process.exit(1)
  }
}
