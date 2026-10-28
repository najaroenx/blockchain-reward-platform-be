import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import type { Role } from './env.schema.js'

/** หา root ของ workspace จากไฟล์ pnpm-workspace.yaml ไม่ว่าจะรันจากโฟลเดอร์ไหน */
export function findRoot(from: string = process.cwd()): string {
  let dir = resolve(from)
  for (;;) {
    if (existsSync(join(dir, 'pnpm-workspace.yaml'))) return dir
    const up = dirname(dir)
    if (up === dir) return resolve(from)
    dir = up
  }
}

/**
 * อ่าน .env แล้วตามด้วย .env.<role>
 *
 * แยกไฟล์ต่อ process โดยตั้งใจ · ตัวแปรของ KMS อยู่ใน .env.worker ไฟล์เดียว
 * api จึงไม่มีทางเห็นมันเลย ไม่ใช่แค่ไม่อ่าน  (BE-0001)
 *
 * ของที่ตั้งมาจาก shell หรือ CI ชนะไฟล์เสมอ · Node ไม่เขียนทับค่าที่มีอยู่แล้ว
 */
export function loadDotenv(role: Role, root = findRoot()): string[] {
  const loaded: string[] = []
  for (const name of ['.env', `.env.${role}`]) {
    const p = join(root, name)
    if (!existsSync(p)) continue
    process.loadEnvFile(p)
    loaded.push(name)
  }
  return loaded
}
