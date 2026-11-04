import { readFile } from 'node:fs/promises'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { startPostgres } from './postgres.js'
import { Client } from 'pg'

describe('coupon type migration on PostgreSQL', () => {
  let database: Awaited<ReturnType<typeof startPostgres>>
  let client: Client
  let sequence = 0
  let migration: string

  beforeAll(async () => {
    database = await startPostgres()
    migration = await readFile(new URL('../prisma/migrations/0002_coupon_type_start/migration.sql', import.meta.url), 'utf8')
  }, 120_000)

  beforeEach(async () => {
    const name = `coupon_migration_${++sequence}`
    await database.pool.query(`CREATE DATABASE ${name}`)
    const databaseUrl = new URL(database.databaseUrl)
    databaseUrl.pathname = '/' + name
    client = new Client({ connectionString: databaseUrl.toString() })
    await client.connect()
    await client.query(await readFile(new URL('../prisma/migrations/0001_init/migration.sql', import.meta.url), 'utf8'))
    await client.query("INSERT INTO merchant (id, name, status, activated_at) VALUES ('mch_test', 'Test', 'ACTIVE', now()), ('mch_pending', 'Pending', 'PENDING', NULL)")
  })

  afterEach(async () => { await client?.end() })

  afterAll(async () => {
    await database?.pool.end()
    await database?.postgres.stop()
  })

  async function insertLegacy(chain: boolean) {
    await client.query(`INSERT INTO coupon_type (id, merchant_id, name, face_value, total_supply, expires_at, created_at)
      VALUES ('ct_legacy', 'mch_test', 'Legacy', 100, 500, now() + interval '1 day', '2020-01-01T00:00:00Z')`)
    if (chain) await client.query(`INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
      VALUES ('ct_legacy', decode(repeat('aa', 20), 'hex'), 7, 1, decode(repeat('bb', 32), 'hex'))`)
  }

  it('applies to an empty schema and rejects invalid dates and missing starts_at', async () => {
    await client.query(migration)
    await expect(client.query(`INSERT INTO coupon_type (id, merchant_id, name, face_value, total_supply, starts_at, expires_at)
      VALUES ('ct_bad', 'mch_test', 'Bad', 100, 500, now() + interval '2 days', now() + interval '1 day')`))
      .rejects.toMatchObject({ code: '23514', constraint: 'ck_coupon_type_date_range' })
    await expect(client.query(`INSERT INTO coupon_type (id, merchant_id, name, face_value, total_supply, expires_at)
      VALUES ('ct_missing', 'mch_test', 'Missing', 100, 500, now() + interval '1 day')`))
      .rejects.toMatchObject({ code: '23502', column: 'starts_at' })
  })

  it('backfills legacy rows without chain data from created_at', async () => {
    await insertLegacy(false)
    await client.query(migration)
    expect((await client.query('SELECT starts_at = created_at AS matches FROM coupon_type')).rows).toEqual([{ matches: true }])
  })

  it('rolls back instead of guessing dates if a chain projection exists', async () => {
    await insertLegacy(true)
    await expect(client.query(migration)).rejects.toThrow('Missing verified startDate')
    await client.query('ROLLBACK')
    expect((await client.query("SELECT count(*)::int AS count FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'coupon_type' AND column_name = 'starts_at'")).rows).toEqual([{ count: 0 }])
    expect((await client.query('SELECT count(*)::int AS count FROM coupon_type_chain')).rows).toEqual([{ count: 1 }])
  })

  it('uses staged verified chain dates for legacy types', async () => {
    await insertLegacy(true)
    await client.query(`CREATE TEMP TABLE coupon_type_start_backfill (coupon_type_id text PRIMARY KEY, starts_at timestamptz NOT NULL);
      INSERT INTO coupon_type_start_backfill VALUES ('ct_legacy', '2021-01-01T00:00:00+07:00')`)
    try {
      await client.query(migration)
      expect((await client.query("SELECT starts_at = '2021-01-01T00:00:00+07:00'::timestamptz AS matches FROM coupon_type")).rows).toEqual([{ matches: true }])
    } finally {
      await client.query('DROP TABLE IF EXISTS coupon_type_start_backfill')
    }
  })

  it('attributes the non-ACTIVE trigger without masking other CHECKs', async () => {
    await client.query(migration)
    await expect(client.query(`INSERT INTO coupon_type (id, merchant_id, name, face_value, total_supply, starts_at, expires_at)
      VALUES ('ct_inactive', 'mch_pending', 'Inactive', 100, 500, now(), now() + interval '1 day')`))
      .rejects.toMatchObject({ code: '23514', constraint: 'ck_coupon_type_merchant_active' })
    await expect(client.query(`INSERT INTO coupon_type (id, merchant_id, name, face_value, total_supply, starts_at, expires_at)
      VALUES ('ct_zero', 'mch_test', 'Zero', 100, 0, now(), now() + interval '1 day')`))
      .rejects.toMatchObject({ code: '23514', constraint: 'coupon_type_total_supply_check' })
  })
})
