// eslint 10 ใช้ flat config อย่างเดียว
// issue เขียนว่า .eslintrc.cjs กับ .eslintignore — สองไฟล์นั้น eslint 10 ไม่อ่านแล้ว
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import importX from 'eslint-plugin-import-x'
import { createTypeScriptImportResolver } from 'eslint-import-resolver-typescript'

const APPS = ['api', 'worker', 'indexer']

/**
 * ห้าม import ข้าม process
 * ใช้ no-restricted-paths เพราะมันดู "path ที่ resolve แล้ว"
 * ส่วน no-restricted-imports ดูแค่ข้อความใน import ซึ่งเลี่ยงได้ด้วย ../../ ธรรมดา
 */
const zones = APPS.flatMap((from) =>
  APPS.filter((t) => t !== from).map((target) => ({
    target: `./apps/${from}`,
    from: `./apps/${target}`,
    message: `apps/${from} ห้าม import จาก apps/${target} · ย้ายของที่ใช้ร่วมกันไป libs/domain`,
  })),
)

export default tseslint.config(
  { ignores: ['node_modules/**', 'dist/**', 'reference/**', '**/*.config.js', 'scripts/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { 'import-x': importX },
    settings: { 'import-x/resolver-next': [createTypeScriptImportResolver({ project: './tsconfig.json' })] },
    rules: {
      'import-x/no-restricted-paths': ['error', { zones }],
      // reference/ เป็นเอกสาร ไม่ใช่โค้ดที่รัน · ห้ามดึงมาใช้
      'no-restricted-imports': ['error', {
        patterns: [{ group: ['**/reference/**'], message: 'reference/ เป็นของอ่านอย่างเดียว ห้าม import' }],
      }],
    },
  },
)
