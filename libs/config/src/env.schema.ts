import { z } from 'zod'

/**
 * env ของสาม process ไม่เหมือนกัน และความต่างนั้นคือเรื่องความปลอดภัย ไม่ใช่เรื่องความสะอาด
 *
 * api ต้อง "ไม่มี" ตัวแปรของ KMS อยู่ใน environment เลย ไม่ใช่แค่ไม่เรียกใช้
 * ถ้ามีอยู่ วันหนึ่งจะมีคนเรียก  (BE-0001 · L6)
 */
export const ROLES = ['api', 'worker', 'indexer'] as const
export type Role = (typeof ROLES)[number]

const url = z.string().min(1)
const shared = {
  DATABASE_URL: url,
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace']).default('info'),
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
}
const chain = {
  RPC_URL: url,
  CHAIN_ID: z.coerce.number().int().positive(),
}

/** ตัวแปรที่ process นั้น "ห้ามมี" · มีเมื่อไรคือ config ผิด ไม่ใช่แค่ไม่ใช้ */
export const FORBIDDEN: Record<Role, readonly string[]> = {
  api: ['KMS_KEY_ARN', 'KMS_REGION'],
  worker: [],
  indexer: ['KMS_KEY_ARN', 'KMS_REGION'],
}

const api = z.object({ ...shared, PORT: z.coerce.number().int().positive().default(3000) })
const worker = z.object({
  ...shared, ...chain,
  KMS_KEY_ARN: z.string().min(1),
  KMS_REGION: z.string().min(1),
})
const indexer = z.object({
  ...shared, ...chain,
  FINALITY_DEPTH: z.coerce.number().int().min(1),
})

export const SCHEMA = { api, worker, indexer } as const
export type Env<R extends Role> = z.infer<(typeof SCHEMA)[R]>
