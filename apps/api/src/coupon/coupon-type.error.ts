import { randomUUID } from 'node:crypto'
import { ArgumentsHost, Catch, ExceptionFilter, HttpException } from '@nestjs/common'
import { z } from 'zod'

export class CouponTypeError extends HttpException {
  constructor(status: number, code: string, message: string, details?: { field: string }) {
    super({ error: { code, message, ...(details ? { details } : {}) } }, status)
  }
}

export function validationError(field: string, message: string): CouponTypeError {
  return new CouponTypeError(400, 'VALIDATION_FAILED', message, { field })
}

const pgErrorSchema = z.object({ code: z.string(), constraint: z.string().optional() })

export function mapCouponDatabaseError(error: unknown): unknown {
  const parsed = pgErrorSchema.safeParse(error)
  if (!parsed.success) return error
  const { code, constraint } = parsed.data
  if (code === '23505' && constraint === 'uq_coupon_type_name') {
    return new CouponTypeError(409, 'NAME_TAKEN', 'ร้านของคุณใช้ชื่อนี้ไปแล้ว')
  }
  if (code === '23514' && constraint === 'ck_coupon_type_merchant_active') {
    return new CouponTypeError(422, 'MERCHANT_WALLET_NOT_READY', 'ร้านยังไม่พร้อมสร้างประเภทคูปอง ต้องมีสถานะ ACTIVE')
  }
  if (code === '23514' && (constraint === 'ck_coupon_type_date_range' || constraint === 'ck_coupon_type_expiry')) {
    return validationError('expiresAt', 'ช่วงวันที่ไม่ถูกต้องหรือ expiresAt ไม่อยู่ในอนาคต')
  }
  return error
}

@Catch(CouponTypeError)
export class CouponTypeErrorFilter implements ExceptionFilter<CouponTypeError> {
  catch(error: CouponTypeError, host: ArgumentsHost): void {
    const response = host.switchToHttp().getResponse<{
      status(code: number): { json(body: unknown): void }
    }>()
    const parsed = z.object({ error: z.object({
      code: z.string(), message: z.string(), details: z.object({ field: z.string() }).optional(),
    }) }).parse(error.getResponse())
    response.status(error.getStatus()).json({ error: { ...parsed.error, requestId: randomUUID() } })
  }
}
