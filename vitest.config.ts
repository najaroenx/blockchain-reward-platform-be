import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url))

export default defineConfig({
  test: { include: ['test/**/*.spec.ts'], environment: 'node' },
  resolve: {
    alias: {
      '@libs/config': r('./libs/config/src/index.ts'),
      '@libs/logger': r('./libs/logger/src/index.ts'),
      '@libs/domain': r('./libs/domain/src/index.ts'),
    },
  },
})
