import { describe, it, expect } from 'vitest'
import { loadEnv, EnvError } from '@libs/config'

const base = { DATABASE_URL: 'postgresql://localhost/reward', LOG_LEVEL: 'info' }
const chain = { RPC_URL: 'http://localhost:8545', CHAIN_ID: '31337' }
const kms = { KMS_KEY_ARN: 'arn:aws:kms:x', KMS_REGION: 'ap-southeast-1' }

describe('env · AC ข้อ 2 ของ BE-0001', () => {
  it('ครบแล้วผ่าน และ coerce ชนิดให้ถูก', () => {
    const e = loadEnv('indexer', { ...base, ...chain, FINALITY_DEPTH: '12' } as NodeJS.ProcessEnv)
    expect(e.FINALITY_DEPTH).toBe(12)
    expect(e.CHAIN_ID).toBe(31337)
  })

  it('ขาดตัวแปรหนึ่งตัว แล้ว error บอกชื่อตัวนั้น', () => {
    try {
      loadEnv('worker', { ...base, ...kms, CHAIN_ID: '1' } as NodeJS.ProcessEnv)
      throw new Error('ควร throw แต่ไม่ throw')
    } catch (e) {
      expect(e).toBeInstanceOf(EnvError)
      expect((e as EnvError).message).toContain('RPC_URL')
    }
  })

  it('api ที่มี KMS_KEY_ARN ใน env ถูกปฏิเสธ ไม่ใช่แค่เมิน', () => {
    try {
      loadEnv('api', { ...base, ...kms } as NodeJS.ProcessEnv)
      throw new Error('ควร throw แต่ไม่ throw')
    } catch (e) {
      expect(e).toBeInstanceOf(EnvError)
      expect((e as EnvError).message).toContain('KMS_KEY_ARN')
      expect((e as EnvError).message).toContain('worker')
    }
  })

  it('FINALITY_DEPTH เป็น 0 ไม่ผ่าน · ต้องอย่างน้อย 1', () => {
    expect(() => loadEnv('indexer', { ...base, ...chain, FINALITY_DEPTH: '0' } as NodeJS.ProcessEnv))
      .toThrow(EnvError)
  })
})
