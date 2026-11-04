import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:net'
import EmbeddedPostgres from 'embedded-postgres'
import { Pool } from 'pg'

export async function freePort(): Promise<number> {
  const server = createServer()
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Missing test server port')
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  return address.port
}

export async function startPostgres() {
  const port = await freePort()
  const directory = await mkdtemp(join(tmpdir(), 'coupon-pg-'))
  const postgres = new EmbeddedPostgres({
    databaseDir: join(directory, 'data'), port, user: 'postgres', password: 'coupon-test',
    persistent: false, autoRehydrateSymlinks: true,
    initdbFlags: ['--no-sync'],
    onLog: () => {}, onError: () => {},
    postgresFlags: ['-h', '127.0.0.1', '-F'],
  })
  await postgres.initialise()
  await postgres.start()
  const databaseUrl = `postgresql://postgres:coupon-test@127.0.0.1:${port}/postgres`
  const pool = new Pool({ connectionString: databaseUrl })
  return { postgres, pool, databaseUrl }
}
