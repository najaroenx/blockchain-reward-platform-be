import 'reflect-metadata'
import { Module, Controller, Get, Inject } from '@nestjs/common'
import { NestFactory } from '@nestjs/core'
import { loadEnvOrExit } from '@libs/config'
import { makeLogger, type Logger } from '@libs/logger'

// ลำดับสำคัญ · อ่าน env ให้จบก่อนสร้างอะไรทั้งนั้น
const env = loadEnvOrExit('api')
const log = makeLogger({ role: 'api', level: env.LOG_LEVEL })

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
  controllers: [HealthController],
  providers: [{ provide: 'LOGGER', useValue: log }],
})
class ApiModule {}

async function main(): Promise<void> {
  const app = await NestFactory.create(ApiModule, { logger: false })
  await app.listen(env.PORT)
  log.info({ msg: 'api พร้อมใช้งาน', port: env.PORT })
}
void main()
