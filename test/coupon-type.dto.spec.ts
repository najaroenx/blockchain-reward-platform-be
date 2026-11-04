import { describe, expect, it } from 'vitest'
import { createCouponTypeSchema, formatThaiDate } from '../apps/api/src/coupon/coupon-type.dto.js'
import { mapCouponDatabaseError, CouponTypeError } from '../apps/api/src/coupon/coupon-type.error.js'

const valid = {
  name: ' คูปอง ', faceValue: '100000000000000000000', totalSupply: 500,
  startsAt: '2027-01-01T00:00:00+07:00', expiresAt: '2027-03-31T23:59:59+07:00',
}

describe('coupon type validation', () => {
  it('normalizes names, integer strings and zero fractions for stable replay', () => {
    const result = createCouponTypeSchema.parse({ ...valid, name: ' e\u0301 ', faceValue: '00010', startsAt: '2027-01-01T00:00:00.000+07:00' })
    expect(result.name).toBe('é')
    expect(result.faceValue).toBe('10')
    expect(result.startsAt).toBe('2027-01-01T00:00:00+07:00')
  })

  it.each([
    { startsAt: undefined }, { startsAt: null }, { startsAt: '2027-01-01T00:00:00Z' },
    { startsAt: '2027-01-01T00:00:00+08:00' }, { startsAt: '2027-01-01T00:00:00' },
    { startsAt: '2027-02-30T00:00:00+07:00' }, { startsAt: '2027-01-01T24:00:00+07:00' },
    { startsAt: '2027-01-01T00:00:00.001+07:00' }, { expiresAt: valid.startsAt },
    { expiresAt: '2026-12-31T23:59:59+07:00' }, { totalSupply: 0 }, { totalSupply: 1_000_001 },
    { totalSupply: 1.5 }, { totalSupply: '1' }, { faceValue: '-1' }, { faceValue: 100 },
    { faceValue: '1.5' }, { faceValue: 'a' }, { faceValue: '1e18' },
    { faceValue: (1n << 256n).toString() }, { name: '   ' }, { name: 'ก'.repeat(121) },
    { extra: true },
  ])('rejects invalid boundary input %j', (patch) => {
    expect(createCouponTypeSchema.safeParse({ ...valid, ...patch }).success).toBe(false)
  })

  it('accepts zero face value, maximum uint256, and Thai dates in the past', () => {
    expect(createCouponTypeSchema.parse({ ...valid, faceValue: '0', startsAt: '2020-01-01T00:00:00+07:00' }).faceValue).toBe('0')
    expect(createCouponTypeSchema.parse({ ...valid, faceValue: ((1n << 256n) - 1n).toString() }).faceValue).toHaveLength(78)
  })

  it('preserves the instant when formatting Thai time', () => {
    expect(formatThaiDate(new Date('2027-03-31T16:59:59Z'))).toBe('2027-03-31T23:59:59+07:00')
  })
})

describe('coupon database error mapping', () => {
  it('maps only the merchant trigger constraint to 422', () => {
    const error = mapCouponDatabaseError({ code: '23514', constraint: 'ck_coupon_type_merchant_active' })
    expect(error).toBeInstanceOf(CouponTypeError)
    if (!(error instanceof CouponTypeError)) throw new Error('Expected coupon error')
    expect(error.getStatus()).toBe(422)
    expect(error.getResponse()).toMatchObject({ error: { code: 'MERCHANT_WALLET_NOT_READY' } })
  })

  it('does not misclassify unrelated CHECKs or unique violations', () => {
    for (const error of [{ code: '23514', constraint: 'other' }, { code: '23505', constraint: 'other' }, new Error('network')]) {
      expect(mapCouponDatabaseError(error)).toBe(error)
    }
  })
})
