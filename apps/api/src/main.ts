import 'reflect-metadata'
import { Module, Controller, Get, Inject } from '@nestjs/common'
import { NestFactory } from '@nestjs/core'
import { loadEnvOrExit } from '@libs/config'
import { makeLogger, type Logger } from '@libs/logger'
import { CouponTypeModule } from './coupon/coupon-type.module.js'

// ลำดับสำคัญ · อ่าน env ให้จบก่อนสร้างอะไรทั้งนั้น
const env = loadEnvOrExit('api')
const log = makeLogger({ role: 'api', level: env.LOG_LEVEL })

function couponModules() {
  if (env.COUPON_TYPES_ENABLED !== 'true') return []
  const { COUPON_JWT_PUBLIC_KEY: publicKey, COUPON_JWT_ISSUER: issuer, COUPON_JWT_AUDIENCE: audience, COUPON_RPC_URL: rpcUrl, COUPON_CHAIN_ID: chainId } = env
  if (!publicKey || !issuer || !audience || !rpcUrl || !chainId) throw new Error('Missing coupon configuration')
  return [CouponTypeModule.register({ databaseUrl: env.DATABASE_URL, publicKey, issuer, audience, rpcUrl, chainId })]
}

@Controller()
class HealthController {
  constructor(@Inject('LOGGER') private readonly log: Logger) {}
  @Get('/healthz')
  health(): { ok: true; role: 'api' } {
    this.log.debug({ msg: 'healthz' })
    return { ok: true, role: 'api' }
  }
}

@Module({
  imports: couponModules(),
  controllers: [HealthController],
  providers: [{ provide: 'LOGGER', useValue: log }],
})
class ApiModule {}

async function main(): Promise<void> {
  const app = await NestFactory.create(ApiModule, { logger: false })
  app.enableShutdownHooks()
  await app.listen(env.PORT)
  log.info({ msg: 'api พร้อมใช้งาน', port: env.PORT })
}
void main()
