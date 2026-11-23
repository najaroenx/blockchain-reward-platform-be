import { CanActivate, ExecutionContext, Inject, Injectable, createParamDecorator } from '@nestjs/common'
import { jwtVerify, importSPKI } from 'jose'
import { z } from 'zod'
import { CouponTypeError } from './coupon-type.error.js'

export const merchantIdSchema = z.string().regex(/^mch_[A-Za-z0-9_-]+$/).brand<'MerchantId'>()
export type MerchantId = z.infer<typeof merchantIdSchema>
const claimsSchema = z.object({
  sub: z.string().min(1), kind: z.literal('MERCHANT_ADMIN'), mid: merchantIdSchema,
  sid: z.string().min(1), jti: z.string().min(1), iat: z.number().int(), exp: z.number().int(),
})

export interface MerchantTokenVerifier {
  verify(token: string): Promise<MerchantId>
}
export const MERCHANT_TOKEN_VERIFIER = Symbol('MERCHANT_TOKEN_VERIFIER')

export async function createMerchantTokenVerifier(options: {
  publicKey: string; issuer: string; audience: string
}): Promise<MerchantTokenVerifier> {
  const key = await importSPKI(options.publicKey, 'RS256')
  return {
    async verify(token) {
      try {
        const { payload } = await jwtVerify(token, key, {
          algorithms: ['RS256'], issuer: options.issuer, audience: options.audience,
          requiredClaims: ['sub', 'kind', 'mid', 'sid', 'iat', 'exp', 'jti'],
        })
        const claims = claimsSchema.safeParse(payload)
        if (!claims.success) throw new Error('Invalid merchant claims')
        return claims.data.mid
      } catch {
        throw new CouponTypeError(401, 'UNAUTHORIZED', 'ต้องใช้ access token ของผู้ดูแลร้านที่ถูกต้อง')
      }
    },
  }
}

type CouponRequest = { headers: { authorization?: string }; merchantId?: MerchantId }

@Injectable()
export class CouponMerchantGuard implements CanActivate {
  constructor(@Inject(MERCHANT_TOKEN_VERIFIER) private readonly verifier: MerchantTokenVerifier) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<CouponRequest>()
    const match = /^Bearer ([^\s]+)$/i.exec(request.headers.authorization ?? '')
    const token = match?.[1]
    if (!token) throw new CouponTypeError(401, 'UNAUTHORIZED', 'ต้องมี Bearer access token')
    request.merchantId = await this.verifier.verify(token)
    return true
  }
}

export const CouponMerchant = createParamDecorator((_data: unknown, context: ExecutionContext): MerchantId => {
  const id = context.switchToHttp().getRequest<CouponRequest>().merchantId
  if (!id) throw new CouponTypeError(401, 'UNAUTHORIZED', 'ไม่พบผู้ดูแลร้านที่ยืนยันตัวตนแล้ว')
  return id
})
