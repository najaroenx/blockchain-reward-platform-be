import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { findRoot } from '@libs/config'

describe('แยกไฟล์ env ต่อ process', () => {
  const root = findRoot(import.meta.dirname)

  it('ตัวแปรของ KMS อยู่ใน .env.worker.example ไฟล์เดียว', () => {
    const others = ['.env.example', '.env.api.example', '.env.indexer.example']
    for (const f of others) {
      const p = `${root}/${f}`
      if (!existsSync(p)) continue
      const body = readFileSync(p, 'utf8')
      expect(body, `${f} ต้องไม่มี KMS_KEY_ARN`).not.toContain('KMS_KEY_ARN')
      expect(body, `${f} ต้องไม่มี KMS_REGION`).not.toContain('KMS_REGION')
    }
    expect(readFileSync(`${root}/.env.worker.example`, 'utf8')).toContain('KMS_KEY_ARN')
  })

  it('หา root ของ workspace เจอจากโฟลเดอร์ย่อย', () => {
    expect(existsSync(`${findRoot(`${root}/apps/api/src`)}/pnpm-workspace.yaml`)).toBe(true)
  })
})
