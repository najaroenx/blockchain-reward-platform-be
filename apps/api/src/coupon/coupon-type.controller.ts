import { Body, Controller, Get, Headers, HttpCode, Inject, Post, Query, Res, UseFilters, UseGuards } from '@nestjs/common'
import { CouponMerchant, CouponMerchantGuard, type MerchantId } from './coupon-type.auth.js'
import { couponStatusSchema, createCouponTypeSchema } from './coupon-type.dto.js'
import { CouponTypeError, CouponTypeErrorFilter, validationError } from './coupon-type.error.js'
import { CouponTypeService } from './coupon-type.service.js'

@Controller('/v1/coupon-types')
@UseGuards(CouponMerchantGuard)
@UseFilters(CouponTypeErrorFilter)
export class CouponTypeController {
  constructor(@Inject(CouponTypeService) private readonly service: CouponTypeService) {}

  @Post()
  @HttpCode(202)
  async create(
    @CouponMerchant() merchantId: MerchantId,
    @Headers('idempotency-key') key: string | undefined,
    @Body() body: unknown,
    @Res({ passthrough: true }) response: { setHeader(name: string, value: string): void },
  ) {
    if (key === undefined || key.length === 0) {
      throw new CouponTypeError(400, 'IDEMPOTENCY_KEY_REQUIRED', 'ต้องมี Idempotency-Key')
    }
    if (key.length < 8 || key.length > 255) throw validationError('Idempotency-Key', 'Idempotency-Key ต้องยาว 8–255 ตัวอักษร')
    const dto = createCouponTypeSchema.safeParse(body)
    if (!dto.success) {
      const issue = dto.error.issues[0]
      throw validationError(issue?.path.join('.') || 'body', issue?.message ?? 'ข้อมูลไม่ถูกต้อง')
    }
    const accepted = await this.service.createType(merchantId, key, dto.data)
    response.setHeader('Location', `/v1/operations/${accepted.operationId}`)
    return accepted
  }

  @Get()
  async list(@CouponMerchant() merchantId: MerchantId, @Query('status') status: unknown) {
    const parsed = couponStatusSchema.optional().safeParse(status)
    if (!parsed.success) throw validationError('status', 'status ไม่ถูกต้อง')
    return this.service.listTypes(merchantId, parsed.data)
  }
}
