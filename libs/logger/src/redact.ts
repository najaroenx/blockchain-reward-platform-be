/**
 * redact ที่ชั้น serializer ไม่ใช่ที่จุดเรียก
 *
 * คนเขียนโค้ดคนที่ห้าจะไม่รู้ว่าต้องเรียกอะไรก่อน log
 * แต่ serializer ทำงานกับทุกบรรทัดเสมอ ไม่ว่าใครเขียน  (BE-0001)
 */
export const SECRET_KEYS = [
  'password', 'passwordhash', 'privatekey', 'secret', 'token',
  'authorization', 'idempotencykey', 'keyref', 'keyarn', 'mnemonic', 'seed', 'code',
] as const

export const MASK = '[redacted]'

const hit = (key: string): boolean => {
  const k = key.toLowerCase().replace(/[-_]/g, '')
  return SECRET_KEYS.some((s) => k.includes(s))
}

/**
 * เดินทั้งก้อนแล้วปิดค่าที่ชื่อ key สื่อถึงความลับ ไม่ว่าจะซ้อนลึกแค่ไหน
 * กัน object ที่อ้างถึงตัวเอง และจำกัดความลึกไว้ ไม่งั้น log หนึ่งบรรทัดทำ process ตายได้
 */
export function redactDeep(value: unknown, depth = 0, seen = new WeakSet<object>()): unknown {
  if (depth > 8 || value === null || typeof value !== 'object') return value
  const obj = value as object
  if (seen.has(obj)) return '[circular]'
  seen.add(obj)

  if (Array.isArray(value)) return value.map((v) => redactDeep(v, depth + 1, seen))
  if (value instanceof Error) {
    return { name: value.name, message: value.message, stack: value.stack }
  }

  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = hit(k) ? MASK : redactDeep(v, depth + 1, seen)
  }
  return out
}
