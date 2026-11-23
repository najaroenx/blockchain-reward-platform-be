import { FetchRequest, Interface, JsonRpcProvider, getAddress } from 'ethers'
import { CouponTypeError } from './coupon-type.error.js'

export const COUPON_SUPPLY_READER = Symbol('COUPON_SUPPLY_READER')
export interface CouponSupplyReader {
  getIssuedSupply(chain: { contractAddress: string; chainTypeId: string }): Promise<bigint>
}

export class RpcCouponSupplyReader implements CouponSupplyReader {
  private readonly abi = new Interface(['function getTotalSupply(uint256 typeId) view returns (uint256)'])
  private readonly provider: JsonRpcProvider

  constructor(rpcUrl: string, private readonly chainId: number) {
    const request = new FetchRequest(rpcUrl)
    request.timeout = 5000
    this.provider = new JsonRpcProvider(request, undefined, { batchMaxCount: 1 })
  }

  async getIssuedSupply(chain: { contractAddress: string; chainTypeId: string }): Promise<bigint> {
    try {
      const network = await this.provider.getNetwork()
      if (network.chainId !== BigInt(this.chainId)) throw new Error('Wrong coupon chain')
      const result = await this.provider.call({
        to: getAddress(chain.contractAddress), data: this.abi.encodeFunctionData('getTotalSupply', [chain.chainTypeId]),
      })
      const decoded: unknown = this.abi.decodeFunctionResult('getTotalSupply', result)[0]
      if (typeof decoded !== 'bigint') throw new Error('Invalid coupon supply')
      return decoded
    } catch {
      throw new CouponTypeError(503, 'COUPON_CHAIN_UNAVAILABLE', 'ไม่สามารถอ่านจำนวนคูปองจาก chain ได้')
    }
  }

  close(): void { this.provider.destroy() }
}
