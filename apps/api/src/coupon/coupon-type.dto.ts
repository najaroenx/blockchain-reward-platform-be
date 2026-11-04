import { z } from 'zod'

const UINT256_MAX = (1n << 256n) - 1n
const thaiDate = z.string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.0+)?\+07:00$/, 'ต้องระบุเวลาไทยพร้อม +07:00 และไม่มีเศษวินาที')
  .refine((value) => {
    const ms = Date.parse(value)
    if (!Number.isFinite(ms)) return false
    return formatThaiDate(new Date(ms)).slice(0, 19) === value.slice(0, 19)
  }, 'วันที่ไม่ถูกต้อง')

export function formatThaiDate(date: Date): string {
  return new Date(date.getTime() + 7 * 60 * 60 * 1000).toISOString().slice(0, 19) + '+07:00'
}

export const createCouponTypeSchema = z.strictObject({
  name: z.string().transform((value) => value.normalize('NFC').trim())
    .refine((value) => Array.from(value).length >= 1 && Array.from(value).length <= 120, 'ชื่อต้องยาว 1–120 ตัวอักษร'),
  faceValue: z.string().regex(/^[0-9]{1,78}$/)
    .refine((value) => /^[0-9]{1,78}$/.test(value) && BigInt(value) <= UINT256_MAX, 'faceValue เกิน uint256')
    .transform((value) => BigInt(value).toString()),
  totalSupply: z.number().int().min(1).max(1_000_000),
  startsAt: thaiDate.transform((value) => formatThaiDate(new Date(value))),
  expiresAt: thaiDate.transform((value) => formatThaiDate(new Date(value))),
}).superRefine((value, ctx) => {
  if (Date.parse(value.startsAt) >= Date.parse(value.expiresAt)) {
    ctx.addIssue({ code: 'custom', path: ['startsAt'], message: 'startsAt ต้องอยู่ก่อน expiresAt' })
  }
})

export type CreateCouponTypeRequest = z.infer<typeof createCouponTypeSchema>
export const couponStatuses = ['REQUESTED', 'LIVE', 'FAILED', 'RETIRED'] as const
export const couponStatusSchema = z.enum(couponStatuses)

export const acceptedCouponTypeSchema = z.object({
  couponTypeId: z.string(),
  name: z.string(),
  faceValue: z.string(),
  totalSupply: z.number(),
  startsAt: z.string(),
  expiresAt: z.string(),
  status: z.literal('REQUESTED'),
  operationId: z.string(),
})
export type AcceptedCouponType = z.infer<typeof acceptedCouponTypeSchema>
