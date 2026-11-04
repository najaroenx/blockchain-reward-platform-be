import { DynamicModule, Inject, Module, type OnApplicationShutdown } from '@nestjs/common'
import { Pool } from 'pg'
import { createMerchantTokenVerifier, MERCHANT_TOKEN_VERIFIER, CouponMerchantGuard } from './coupon-type.auth.js'
import { CouponTypeController } from './coupon-type.controller.js'
import { COUPON_DATABASE, CouponTypeService } from './coupon-type.service.js'
import { COUPON_SUPPLY_READER, RpcCouponSupplyReader } from './coupon-type.supply.js'

@Module({})
export class CouponTypeModule implements OnApplicationShutdown {
  constructor(
    @Inject(COUPON_DATABASE) private readonly db: Pool,
    @Inject(COUPON_SUPPLY_READER) private readonly supply: RpcCouponSupplyReader,
  ) {}

  static register(options: {
    databaseUrl: string; publicKey: string; issuer: string; audience: string; rpcUrl: string; chainId: number
  }): DynamicModule {
    return {
      module: CouponTypeModule,
      controllers: [CouponTypeController],
      providers: [
        CouponTypeService, CouponMerchantGuard,
        { provide: COUPON_DATABASE, useFactory: () => new Pool({ connectionString: options.databaseUrl, max: 20, connectionTimeoutMillis: 5000 }) },
        { provide: MERCHANT_TOKEN_VERIFIER, useFactory: () => createMerchantTokenVerifier(options) },
        { provide: COUPON_SUPPLY_READER, useFactory: () => new RpcCouponSupplyReader(options.rpcUrl, options.chainId) },
      ],
    }
  }

  async onApplicationShutdown(): Promise<void> {
    this.supply.close()
    await this.db.end()
  }
}
