import { describe, it, expect } from 'vitest'
import { Writable } from 'node:stream'
import pino from 'pino'
import { redactDeep, MASK } from '@libs/logger'

/** จับบรรทัดที่ pino เขียนออกจริง · ไม่ได้เช็คแค่ฟังก์ชัน */
function capture(): { lines: string[]; stream: Writable } {
  const lines: string[] = []
  const stream = new Writable({
    write(chunk, _enc, cb) { lines.push(String(chunk)); cb() },
  })
  return { lines, stream }
}

describe('redact · AC ข้อ 4 ของ BE-0001', () => {
  it('log ที่มี password หรือ key ถูกปิดก่อนออกจริง', () => {
    const { lines, stream } = capture()
    const log = pino(
      { formatters: { log: (o) => redactDeep(o) as Record<string, unknown> } },
      stream,
    )

    log.info({
      msg: 'สมัครร้าน',
      email: 'owner@shop.co.th',
      password: 'hunter2',
      admin: { passwordHash: '$argon2id$abc', displayName: 'สมชาย' },
      wallet: { address: '0xabc', keyRef: 'kms/merchant-1' },
      idempotencyKey: 'ik_123',
      tokens: [{ token: 't1' }, { token: 't2' }],
    })

    const out = lines.join('')
    // ค่าลับต้องไม่หลุดออกไปสักตัว
    for (const leak of ['hunter2', '$argon2id$abc', 'kms/merchant-1', 'ik_123', 't1', 't2']) {
      expect(out, `ค่า ${leak} หลุดออก log`).not.toContain(leak)
    }
    // ของที่ไม่ใช่ความลับต้องยังอยู่ ไม่งั้น log ใช้ไม่ได้
    expect(out).toContain('owner@shop.co.th')
    expect(out).toContain('สมชาย')
    expect(out).toContain(MASK)
  })

  it('ชื่อ key แบบ snake_case และ kebab-case ก็โดนด้วย', () => {
    const r = redactDeep({ private_key: 'x', 'key-ref': 'y', ok: 'z' }) as Record<string, unknown>
    expect(r['private_key']).toBe(MASK)
    expect(r['key-ref']).toBe(MASK)
    expect(r['ok']).toBe('z')
  })

  it('object ที่อ้างถึงตัวเองไม่ทำให้ตาย', () => {
    const a: Record<string, unknown> = { name: 'a' }
    a['self'] = a
    expect(() => redactDeep(a)).not.toThrow()
  })
})

describe('ไม่มีค่า env ที่เป็นความลับหลุดออก log ตอน boot', () => {
  it('ชื่อ key ที่สื่อถึง KMS ก็ถูกปิด', () => {
    const r = redactDeep({
      kmsKeyArn: 'arn:aws:kms:x', keyRef: 'kms/m-1', chainId: 31337,
    }) as Record<string, unknown>
    expect(r['kmsKeyArn']).toBe(MASK)
    expect(r['keyRef']).toBe(MASK)
    expect(r['chainId']).toBe(31337)
  })
})
