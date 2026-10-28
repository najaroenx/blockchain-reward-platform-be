/**
 * ของที่สาม process ใช้ร่วมกัน · type · สูตรคำนวณ · idempotency
 * ห้าม import ข้าม apps/ เด็ดขาด อะไรที่ต้องใช้ร่วมกันย้ายมาที่นี่
 *
 * ตอนนี้ว่างโดยตั้งใจ · ของจริงมาใน BE-0101 (idempotency) และ BE-0102 (operation)
 */
export const PROCESSES = ['api', 'worker', 'indexer'] as const
export type Process = (typeof PROCESSES)[number]
