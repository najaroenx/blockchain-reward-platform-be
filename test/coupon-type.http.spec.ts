import 'reflect-metadata'
import { readFile } from 'node:fs/promises'
import { createServer, type Server } from 'node:http'
import { randomUUID } from 'node:crypto'
import { NestFactory } from '@nestjs/core'
import { generateKeyPair, exportSPKI, SignJWT } from 'jose'
import { Interface } from 'ethers'
import { z } from 'zod'
import newman from 'newman'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { CouponTypeModule } from '../apps/api/src/coupon/coupon-type.module.js'
import { acceptedCouponTypeSchema, formatThaiDate } from '../apps/api/src/coupon/coupon-type.dto.js'
import { startPostgres } from './postgres.js'

const migration = new URL('../prisma/migrations/0002_coupon_type_start/migration.sql', import.meta.url)
const baseMigration = new URL('../prisma/migrations/0001_init/migration.sql', import.meta.url)
const issuer = 'https://auth.test'
const audience = 'reward-api'
const abi = new Interface(['function getTotalSupply(uint256) view returns (uint256)'])
const rpcRequestSchema = z.object({ id: z.number(), method: z.string(), params: z.array(z.unknown()).optional() })
const errorBodySchema = z.object({ error: z.object({ code: z.string(), requestId: z.string() }) })
const listSchema = z.object({ couponTypes: z.array(z.object({
  couponTypeId: z.string(), issuedCount: z.number(), remainingSupply: z.number(),
  startsAt: z.string(), expiresAt: z.string(), chain: z.object({ contractAddress: z.string(), chainTypeId: z.string() }).nullable(),
})) })

describe('BE-0602 HTTP + PostgreSQL', () => {
  let database: Awaited<ReturnType<typeof startPostgres>>
  let app: Awaited<ReturnType<typeof NestFactory.create>> | undefined
  let rpc: Server | undefined
  let url: string
  let token: string
  let otherToken: string
  let privateKey: Awaited<ReturnType<typeof generateKeyPair>>['privateKey']
  let rpcSupply = 0n
  let rpcFails = false
  const calls: unknown[] = []

  const requestBody = () => ({
    name: 'คูปอง 100 บาท', faceValue: '100000000000000000000', totalSupply: 500,
    startsAt: '2020-01-01T00:00:00+07:00',
    expiresAt: formatThaiDate(new Date(Date.now() + 86400_000)),
  })

  async function sign(merchantId: string, options: { kind?: string; expired?: boolean } = {}) {
    return new SignJWT({ kind: options.kind ?? 'MERCHANT_ADMIN', mid: merchantId, sid: 'ses_test' })
      .setProtectedHeader({ alg: 'RS256' }).setSubject('adm_test').setJti(randomUUID())
      .setIssuer(issuer).setAudience(audience).setIssuedAt()
      .setExpirationTime(options.expired ? '0s' : '15m').sign(privateKey)
  }

  async function post(body: unknown = requestBody(), key: string = randomUUID(), accessToken = token) {
    return fetch(url, { method: 'POST', headers: {
      'content-type': 'application/json', authorization: `Bearer ${accessToken}`, 'idempotency-key': key,
    }, body: JSON.stringify(body) })
  }

  async function expectError(response: Response, status: number, code: string) {
    expect(response.status).toBe(status)
    expect(errorBodySchema.parse(await response.json()).error.code).toBe(code)
  }

  beforeAll(async () => {
    database = await startPostgres()
    await database.pool.query(await readFile(baseMigration, 'utf8'))
    await database.pool.query(await readFile(migration, 'utf8'))
    const keys = await generateKeyPair('RS256')
    privateKey = keys.privateKey
    token = await sign('mch_one')
    otherToken = await sign('mch_two')
    rpc = createServer(async (request, response) => {
      const chunks: Buffer[] = []
      for await (const chunk of request) chunks.push(Buffer.from(chunk))
      const payload: unknown = JSON.parse(Buffer.concat(chunks).toString())
      const parsed = rpcRequestSchema.parse(payload)
      calls.push(payload)
      response.setHeader('content-type', 'application/json')
      if (parsed.method === 'eth_chainId') {
        response.end(JSON.stringify({ jsonrpc: '2.0', id: parsed.id, result: '0x7a69' }))
      } else if (parsed.method === 'eth_call' && !rpcFails) {
        response.end(JSON.stringify({ jsonrpc: '2.0', id: parsed.id, result: abi.encodeFunctionResult('getTotalSupply', [rpcSupply]) }))
      } else {
        response.end(JSON.stringify({ jsonrpc: '2.0', id: parsed.id, error: { code: -32000, message: 'test RPC unavailable' } }))
      }
    })
    await new Promise<void>((resolve) => rpc?.listen(0, '127.0.0.1', resolve))
    const address = rpc.address()
    if (!address || typeof address === 'string') throw new Error('Missing RPC address')
    app = await NestFactory.create(CouponTypeModule.register({
      databaseUrl: database.databaseUrl, publicKey: await exportSPKI(keys.publicKey), issuer, audience,
      rpcUrl: `http://127.0.0.1:${address.port}`, chainId: 31337,
    }), { logger: false, abortOnError: false })
    await app.listen(0, '127.0.0.1')
    url = `${await app.getUrl()}/v1/coupon-types`
  }, 120_000)

  beforeEach(async () => {
    rpcSupply = 0n
    rpcFails = false
    calls.length = 0
    await database.pool.query('TRUNCATE merchant, operation, idempotency_key CASCADE')
    await database.pool.query(`INSERT INTO merchant (id, name, status, activated_at) VALUES
      ('mch_one', 'One', 'ACTIVE', now()), ('mch_two', 'Two', 'ACTIVE', now()), ('mch_pending', 'Pending', 'PENDING', NULL)`)
  })

  afterAll(async () => {
    await app?.close()
    if (rpc) await new Promise<void>((resolve, reject) => rpc?.close((error) => error ? reject(error) : resolve()))
    await database?.pool.end()
    await database?.postgres.stop()
  })

  it('admits atomically, queues mapped chain dates, and lists merchant data in Thai time', async () => {
    const dto = requestBody()
    const response = await post(dto)
    expect(response.status).toBe(202)
    const body = acceptedCouponTypeSchema.parse(await response.json())
    expect(response.headers.get('location')).toBe(`/v1/operations/${body.operationId}`)
    expect(body).toMatchObject({ ...dto, status: 'REQUESTED' })
    const result = await database.pool.query('SELECT kind, payload FROM operation WHERE id = $1', [body.operationId])
    expect(result.rows).toMatchObject([{ kind: 'COUPON_TYPE_CREATE', payload: {
      startDate: String(Date.parse(dto.startsAt) / 1000), expireDate: String(Date.parse(dto.expiresAt) / 1000),
    } }])
    for (const table of ['coupon_type', 'operation', 'operation_step', 'outbox', 'operation_event', 'idempotency_key']) {
      expect((await database.pool.query(`SELECT count(*)::int AS count FROM ${table}`)).rows).toEqual([{ count: 1 }])
    }
    const list = await fetch(url, { headers: { authorization: `Bearer ${token}` } })
    expect(list.status).toBe(200)
    expect(listSchema.parse(await list.json()).couponTypes).toMatchObject([{ couponTypeId: body.couponTypeId, issuedCount: 0, remainingSupply: 500, chain: null }])
    expect(calls).toHaveLength(0)
    const other = await fetch(url, { headers: { authorization: `Bearer ${otherToken}` } })
    expect(listSchema.parse(await other.json()).couponTypes).toEqual([])
  })

  it('20 simultaneous requests replay the same response bytes and one set of writes', async () => {
    const dto = requestBody()
    const key = randomUUID()
    const responses = await Promise.all(Array.from({ length: 20 }, () => post(dto, key)))
    expect(responses.map((response) => response.status)).toEqual(Array(20).fill(202))
    const texts = await Promise.all(responses.map((response) => response.text()))
    expect(new Set(texts).size).toBe(1)
    for (const table of ['coupon_type', 'operation', 'operation_step', 'outbox', 'operation_event', 'idempotency_key']) {
      expect((await database.pool.query(`SELECT count(*)::int AS count FROM ${table}`)).rows).toEqual([{ count: 1 }])
    }
  })

  it('runs the Postman collection through Newman against the real HTTP API', async () => {
    await new Promise<void>((resolve, reject) => {
      newman.run({
        collection: new URL('../docs/api/coupon-types.postman_collection.json', import.meta.url).pathname,
        envVar: [{ key: 'baseUrl', value: url.replace('/v1/coupon-types', '') }, { key: 'accessToken', value: token }],
        reporters: [],
      }, (error, summary) => {
        if (error) return reject(error)
        if (summary.run.failures.length > 0) return reject(new Error(JSON.stringify(summary.run.failures)))
        expect(summary.run.stats.requests.total).toBe(3)
        resolve()
      })
    })
  }, 30_000)

  it('rejects changed startsAt with the same key, but scopes keys to merchants', async () => {
    const dto = requestBody()
    const key = randomUUID()
    expect((await post(dto, key)).status).toBe(202)
    await expectError(await post({ ...dto, startsAt: '2021-01-01T00:00:00+07:00' }, key), 409, 'IDEMPOTENCY_KEY_REUSED')
    expect((await post(dto, key, otherToken)).status).toBe(202)
  })

  it('replays accepted requests even after expiry passes', async () => {
    const dto = requestBody()
    const key = randomUUID()
    const first = await post(dto, key)
    const text = await first.text()
    // Do not alter the accepted fingerprint. Move the clock seen by validation
    // only; network requests, JWT expiry and PostgreSQL keep their own clocks.
    const { vi } = await import('vitest')
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse(dto.expiresAt) + 1000)
    try {
      const replay = await post(dto, key)
      expect(replay.status).toBe(202)
      expect(await replay.text()).toBe(text)
    } finally { clock.mockRestore() }
  })

  it('reports an existing incomplete admission without creating a second type', async () => {
    const dto = requestBody()
    const key = randomUUID()
    const accepted = await post(dto, key)
    expect(accepted.status).toBe(202)
    await database.pool.query('UPDATE idempotency_key SET response_status = NULL, response_body = NULL WHERE key = $1', [key])
    await expectError(await post(dto, key), 409, 'IDEMPOTENCY_KEY_IN_PROGRESS')
    expect((await database.pool.query('SELECT count(*)::int AS count FROM coupon_type')).rows).toEqual([{ count: 1 }])
  })

  it('maps duplicate names after rollback and allows names from FAILED types', async () => {
    const dto = requestBody()
    const response = await post({ ...dto, name: ' Hello ' })
    const body = acceptedCouponTypeSchema.parse(await response.json())
    const retryKey = randomUUID()
    await expectError(await post({ ...dto, name: 'hello' }, retryKey), 409, 'NAME_TAKEN')
    expect((await database.pool.query('SELECT count(*)::int AS count FROM idempotency_key')).rows).toEqual([{ count: 1 }])
    await database.pool.query("UPDATE coupon_type SET status = 'FAILED' WHERE id = $1", [body.couponTypeId])
    expect((await post({ ...dto, name: 'hello' }, retryKey)).status).toBe(202)
  })

  it('rejects non-ACTIVE merchants with 422 and leaves no admission writes', async () => {
    await expectError(await post(requestBody(), randomUUID(), await sign('mch_pending')), 422, 'MERCHANT_WALLET_NOT_READY')
    for (const table of ['coupon_type', 'operation', 'outbox', 'idempotency_key']) {
      expect((await database.pool.query(`SELECT count(*)::int AS count FROM ${table}`)).rows).toEqual([{ count: 0 }])
    }
  })

  it('validates expiry, timezone, supply and required startsAt over HTTP', async () => {
    for (const patch of [
      { expiresAt: '2021-01-01T00:00:00+07:00' }, { startsAt: undefined }, { startsAt: null },
      { expiresAt: '2030-01-01T00:00:00Z' }, { startsAt: '2027-02-30T00:00:00+07:00' }, { totalSupply: 0 },
    ]) await expectError(await post({ ...requestBody(), ...patch }), 400, 'VALIDATION_FAILED')
  })

  it('requires a valid merchant access token and idempotency key', async () => {
    const missing = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(requestBody()) })
    await expectError(missing, 401, 'UNAUTHORIZED')
    await expectError(await post(requestBody(), randomUUID(), 'not-a-jwt'), 401, 'UNAUTHORIZED')
    await expectError(await post(requestBody(), randomUUID(), await sign('mch_one', { kind: 'CUSTOMER' })), 401, 'UNAUTHORIZED')
    await expectError(await post(requestBody(), randomUUID(), await sign('mch_one', { expired: true })), 401, 'UNAUTHORIZED')
    const noKey = await fetch(url, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: JSON.stringify(requestBody()) })
    await expectError(noKey, 400, 'IDEMPOTENCY_KEY_REQUIRED')
    await expectError(await post(requestBody(), 'short'), 400, 'VALIDATION_FAILED')
  })

  it('lists LIVE supply via the ABI and reports chain failures instead of false counts', async () => {
    const body = acceptedCouponTypeSchema.parse(await (await post()).json())
    const contract = '0x' + 'ab'.repeat(20)
    await database.pool.query("UPDATE coupon_type SET status = 'LIVE', live_at = now() WHERE id = $1", [body.couponTypeId])
    await database.pool.query(`INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
      VALUES ($1, decode($2, 'hex'), 7, 1, decode(repeat('aa', 32), 'hex'))`, [body.couponTypeId, contract.slice(2)])
    rpcSupply = 320n
    const headers = { authorization: `Bearer ${token}` }
    const response = await fetch(`${url}?status=LIVE`, { headers })
    expect(response.status).toBe(200)
    expect(listSchema.parse(await response.json()).couponTypes).toMatchObject([{ issuedCount: 320, remainingSupply: 180, chain: { contractAddress: contract, chainTypeId: '7' } }])
    expect(calls).toContainEqual(expect.objectContaining({ method: 'eth_call', params: [expect.objectContaining({ data: abi.encodeFunctionData('getTotalSupply', [7]) }), 'latest'] }))
    rpcFails = true
    await expectError(await fetch(url, { headers }), 503, 'COUPON_CHAIN_UNAVAILABLE')
    await expectError(await fetch(`${url}?status=INVALID`, { headers }), 400, 'VALIDATION_FAILED')
  })

  it('rolls back all admission data if queue persistence fails', async () => {
    await database.pool.query(`CREATE FUNCTION test_fail_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test queue failure'; END $$;
      CREATE TRIGGER test_fail_outbox BEFORE INSERT ON outbox FOR EACH ROW EXECUTE FUNCTION test_fail_outbox()`)
    try {
      expect((await post()).status).toBe(500)
      for (const table of ['coupon_type', 'operation', 'outbox', 'operation_event', 'idempotency_key']) {
        expect((await database.pool.query(`SELECT count(*)::int AS count FROM ${table}`)).rows).toEqual([{ count: 0 }])
      }
    } finally {
      await database.pool.query('DROP TRIGGER test_fail_outbox ON outbox; DROP FUNCTION test_fail_outbox()')
    }
  })
})
