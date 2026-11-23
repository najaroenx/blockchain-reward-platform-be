import { createHash, randomUUID } from 'node:crypto'
import { Inject, Injectable } from '@nestjs/common'
import { Pool } from 'pg'
import { z } from 'zod'
import { type MerchantId } from './coupon-type.auth.js'
import {
  acceptedCouponTypeSchema, couponStatusSchema, formatThaiDate,
  type AcceptedCouponType, type CreateCouponTypeRequest,
} from './coupon-type.dto.js'
import { CouponTypeError, mapCouponDatabaseError, validationError } from './coupon-type.error.js'
import { COUPON_SUPPLY_READER, type CouponSupplyReader } from './coupon-type.supply.js'

export const COUPON_DATABASE = Symbol('COUPON_DATABASE')
const priorSchema = z.object({
  fingerprint: z.instanceof(Buffer), response_status: z.number().nullable(), response_body: z.unknown(),
})
const listRowSchema = z.object({
  id: z.string(), name: z.string(), face_value: z.string(), total_supply: z.number(),
  status: couponStatusSchema, starts_at: z.date(), expires_at: z.date(),
  contract_address: z.string().nullable(), chain_type_id: z.string().nullable(),
})

@Injectable()
export class CouponTypeService {
  constructor(
    @Inject(COUPON_DATABASE) private readonly db: Pool,
    @Inject(COUPON_SUPPLY_READER) private readonly supply: CouponSupplyReader,
  ) {}

  async createType(merchantId: MerchantId, key: string, dto: CreateCouponTypeRequest): Promise<AcceptedCouponType> {
    const scope = `merchant:${merchantId}`
    const fingerprint = createHash('sha256').update(JSON.stringify({
      endpoint: 'POST /v1/coupon-types', name: dto.name, faceValue: dto.faceValue,
      totalSupply: dto.totalSupply, startsAt: dto.startsAt, expiresAt: dto.expiresAt,
    })).digest()
    const client = await this.db.connect()
    try {
      await client.query('BEGIN')
      // The unique key serializes competing requests before any business writes.
      const inserted = await client.query(`
        INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
        VALUES ($1, $2, $3, $4, now() + interval '72 hours')
        ON CONFLICT (scope, key) DO NOTHING`, [`ik_${randomUUID()}`, scope, key, fingerprint])
      if (inserted.rowCount === 0) {
        const result = await client.query('SELECT fingerprint, response_status, response_body FROM idempotency_key WHERE scope = $1 AND key = $2', [scope, key])
        const prior = priorSchema.parse(result.rows[0])
        if (!prior.fingerprint.equals(fingerprint)) {
          throw new CouponTypeError(409, 'IDEMPOTENCY_KEY_REUSED', 'Idempotency-Key นี้ถูกใช้กับข้อมูลอื่นแล้ว')
        }
        if (prior.response_status === null) {
          throw new CouponTypeError(409, 'IDEMPOTENCY_KEY_IN_PROGRESS', 'คำขอเดิมยังไม่เสร็จ')
        }
        const body = acceptedCouponTypeSchema.parse(prior.response_body)
        if (prior.response_status !== 202) throw new Error('Unexpected coupon response status')
        await client.query('COMMIT')
        return body
      }

      // Validate the current time after replay so retries retain the accepted result.
      if (Date.parse(dto.expiresAt) <= Date.now()) {
        throw validationError('expiresAt', 'expiresAt ต้องอยู่ในอนาคต')
      }
      const merchant = await client.query('SELECT status FROM merchant WHERE id = $1 FOR SHARE', [merchantId])
      const active = z.object({ status: z.literal('ACTIVE') }).safeParse(merchant.rows[0])
      if (!active.success) {
        throw new CouponTypeError(422, 'MERCHANT_WALLET_NOT_READY', 'ร้านยังไม่พร้อมสร้างประเภทคูปอง ต้องมีสถานะ ACTIVE')
      }

      const couponTypeId = `ct_${randomUUID()}`
      const operationId = `op_${randomUUID()}`
      await client.query(`
        INSERT INTO coupon_type (id, merchant_id, name, face_value, total_supply, starts_at, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)`, [couponTypeId, merchantId, dto.name, dto.faceValue, dto.totalSupply, dto.startsAt, dto.expiresAt])
      const payload = {
        couponTypeId, merchantId, name: dto.name, faceValue: dto.faceValue, totalSupply: dto.totalSupply,
        startsAt: dto.startsAt, expiresAt: dto.expiresAt,
        startDate: String(Date.parse(dto.startsAt) / 1000), expireDate: String(Date.parse(dto.expiresAt) / 1000),
      }
      await client.query(`
        INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
        VALUES ($1, $2, 'COUPON_TYPE_CREATE', $3, $4)`, [operationId, scope, couponTypeId, JSON.stringify(payload)])
      await client.query(`
        INSERT INTO operation_step (id, operation_id, step_index, name)
        VALUES ($1, $2, 1, 'createCouponType')`, [`st_${randomUUID()}`, operationId])
      await client.query(`
        INSERT INTO outbox (id, operation_id, topic, payload)
        VALUES ($1, $2, 'coupon.type.create.v1', $3)`, [`obx_${randomUUID()}`, operationId, JSON.stringify(payload)])
      await client.query(`
        INSERT INTO operation_event (id, operation_id, seq, to_status, actor)
        VALUES ($1, $2, 1, 'PENDING', 'MERCHANT_ADMIN')`, [`oev_${randomUUID()}`, operationId])

      const body = acceptedCouponTypeSchema.parse({ couponTypeId, ...dto, status: 'REQUESTED', operationId })
      await client.query(`
        UPDATE idempotency_key SET operation_id = $1, response_status = 202, response_body = $2
        WHERE scope = $3 AND key = $4`, [operationId, JSON.stringify(body), scope, key])
      await client.query('COMMIT')
      return body
    } catch (error) {
      await client.query('ROLLBACK')
      throw mapCouponDatabaseError(error)
    } finally {
      client.release()
    }
  }

  async listTypes(merchantId: MerchantId, status?: z.infer<typeof couponStatusSchema>) {
    const result = await this.db.query(`
      SELECT t.id, t.name, t.face_value::text, t.total_supply, t.status, t.starts_at, t.expires_at,
             CASE WHEN c.contract_address IS NULL THEN NULL ELSE '0x' || encode(c.contract_address, 'hex') END AS contract_address,
             c.chain_type_id::text
      FROM coupon_type t LEFT JOIN coupon_type_chain c ON c.coupon_type_id = t.id
      WHERE t.merchant_id = $1 AND ($2::coupon_type_status IS NULL OR t.status = $2::coupon_type_status)
      ORDER BY t.created_at, t.id`, [merchantId, status ?? null])
    const rows = z.array(listRowSchema).parse(result.rows)
    const couponTypes = []
    for (const row of rows) {
      const chain = row.contract_address !== null && row.chain_type_id !== null
        ? { contractAddress: row.contract_address, chainTypeId: row.chain_type_id } : null
      if (!chain && (row.status === 'LIVE' || row.status === 'RETIRED')) {
        throw new CouponTypeError(503, 'COUPON_CHAIN_UNAVAILABLE', 'ข้อมูลประเภทคูปองบน chain ยังไม่พร้อม')
      }
      // Read issued supply from the contract, never infer it from legacy code rows.
      const issued = chain ? await this.supply.getIssuedSupply(chain) : 0n
      if (issued < 0n || issued > BigInt(row.total_supply)) {
        throw new CouponTypeError(503, 'COUPON_SUPPLY_INCONSISTENT', 'จำนวนคูปองบน chain ไม่ตรงกับเพดานที่ประกาศ')
      }
      const issuedCount = Number(issued)
      couponTypes.push({
        couponTypeId: row.id, name: row.name, faceValue: row.face_value,
        totalSupply: row.total_supply, issuedCount, remainingSupply: row.total_supply - issuedCount,
        status: row.status, startsAt: formatThaiDate(row.starts_at), expiresAt: formatThaiDate(row.expires_at), chain,
      })
    }
    return { couponTypes }
  }
}
