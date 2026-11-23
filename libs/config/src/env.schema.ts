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

const api = z.object({
  ...shared, PORT: z.coerce.number().int().positive().default(3000),
  COUPON_TYPES_ENABLED: z.enum(['true', 'false']).default('false'),
  COUPON_JWT_PUBLIC_KEY: z.string().min(1).optional(),
  COUPON_JWT_ISSUER: z.string().min(1).optional(),
  COUPON_JWT_AUDIENCE: z.string().min(1).optional(),
  COUPON_RPC_URL: z.url().optional(),
  COUPON_CHAIN_ID: z.coerce.number().int().positive().safe().optional(),
}).superRefine((env, ctx) => {
  if (env.COUPON_TYPES_ENABLED !== 'true') return
  for (const field of ['COUPON_JWT_PUBLIC_KEY', 'COUPON_JWT_ISSUER', 'COUPON_JWT_AUDIENCE', 'COUPON_RPC_URL', 'COUPON_CHAIN_ID'] as const) {
    if (env[field] === undefined) ctx.addIssue({ code: 'custom', path: [field], message: 'จำเป็นเมื่อเปิด coupon types' })
  }
})
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
