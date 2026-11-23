import { readFile } from 'node:fs/promises'
import SwaggerParser from '@apidevtools/swagger-parser'
import { describe, expect, it } from 'vitest'
import { loadEnv } from '@libs/config'

describe('coupon API contract and configuration', () => {
  it('validates the OpenAPI document', async () => {
    const path = new URL('../docs/api/coupon-types.openapi.json', import.meta.url).pathname
    const api = await SwaggerParser.validate(path)
    expect(Object.keys(api.paths ?? {})).toEqual(['/v1/coupon-types'])
  })

  it('ships a Postman flow for create, exact replay and list', async () => {
    const text = await readFile(new URL('../docs/api/coupon-types.postman_collection.json', import.meta.url), 'utf8')
    const collection: unknown = JSON.parse(text)
    expect(collection).toMatchObject({ item: [
      { name: 'Create type', request: { method: 'POST' } },
      { name: 'Replay type', request: { method: 'POST' } },
      { name: 'List types', request: { method: 'GET' } },
    ] })
  })

  it('keeps the feature off by default and requires verification and RPC config when enabled', () => {
    expect(loadEnv('api', { DATABASE_URL: 'postgresql://localhost/test' }).COUPON_TYPES_ENABLED).toBe('false')
    expect(() => loadEnv('api', { DATABASE_URL: 'postgresql://localhost/test', COUPON_TYPES_ENABLED: 'true' })).toThrow()
    expect(loadEnv('api', {
      DATABASE_URL: 'postgresql://localhost/test', COUPON_TYPES_ENABLED: 'true',
      COUPON_JWT_PUBLIC_KEY: 'configured-key', COUPON_JWT_ISSUER: 'https://auth.test',
      COUPON_JWT_AUDIENCE: 'reward-api', COUPON_RPC_URL: 'http://localhost:8545', COUPON_CHAIN_ID: '31337',
    }).COUPON_CHAIN_ID).toBe(31337)
  })
})
