const fs = require("node:fs");
const { artifacts, ethers, upgrades } = require("hardhat");

const recordPath = process.env.DEPLOY_RECORD;
const revisions = {
  couponMarketplace: process.env.DEPLOY_COUPON_REVISION,
  pointFactory: process.env.DEPLOY_POINT_REVISION,
};
const contractNames = ["thb", "coupon", "pointFactory", "vault", "marketplace", "marketplaceImplementation"];

function save(record, create = false) {
  const contents = JSON.stringify(record, null, 2) + "\n";
  if (create) {
    fs.writeFileSync(recordPath, contents, { flag: "wx" });
  } else {
    const temporary = `${recordPath}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, contents, { flag: "wx" });
    fs.renameSync(temporary, recordPath);
  }
}

async function runtimeHash(address) {
  const code = await ethers.provider.getCode(address);
  if (code === "0x") throw new Error(`No deployed code at ${address}`);
  return ethers.keccak256(code);
}

async function verify(record, pointAbi) {
  const contracts = record.contracts;
  const blocks = [];
  for (const name of contractNames) {
    const entry = contracts[name];
    if (!entry || await runtimeHash(entry.address) !== entry.runtimeHash) {
      throw new Error(`Deployment code does not match the record for ${name}`);
    }
    if (name !== "marketplaceImplementation") {
      const receipt = await ethers.provider.getTransactionReceipt(entry.transactionHash);
      if (!receipt || receipt.status !== 1 || receipt.contractAddress !== entry.address ||
          receipt.blockHash !== entry.blockHash || receipt.blockNumber !== entry.blockNumber) {
        throw new Error(`Deployment receipt does not match the record for ${name}`);
      }
      blocks.push(receipt.blockNumber);
    }
  }
  if (record.deployBlock !== Math.min(...blocks)) throw new Error("Invalid deployment start block");

  async function read(name, address) {
    return new ethers.Contract(address, (await artifacts.readArtifact(name)).abi, ethers.provider);
  }
  const coupon = await read("Coupon", contracts.coupon.address);
  const vault = await read("Vault", contracts.vault.address);
  const marketplace = await read("MarketplaceUpgradeable", contracts.marketplace.address);
  const factory = new ethers.Contract(contracts.pointFactory.address, pointAbi, ethers.provider);
  const [couponMarket, vaultMarket, vaultThb, marketThb, marketCoupon, marketVault, impl, version] =
    await Promise.all([
      coupon.marketplace(), vault.marketplace(), vault.thbToken(), marketplace.thbToken(),
      marketplace.couponContract(), marketplace.vault(),
      upgrades.erc1967.getImplementationAddress(contracts.marketplace.address),
      factory.pointTokenVersion(),
    ]);
  if (couponMarket !== contracts.marketplace.address || vaultMarket !== contracts.marketplace.address ||
      vaultThb !== contracts.thb.address || marketThb !== contracts.thb.address ||
      marketCoupon !== contracts.coupon.address || marketVault !== contracts.vault.address ||
      impl !== contracts.marketplaceImplementation.address || version !== 3n) {
    throw new Error("Deployment wiring or Point Factory ABI does not match the pinned contracts");
  }
}

async function main() {
  const chainId = Number(process.env.DEPLOY_CHAIN_ID);
  const network = process.env.DEPLOY_NETWORK;
  const genesis = await ethers.provider.getBlock(0);
  if (!genesis || (await ethers.provider.getNetwork()).chainId !== BigInt(chainId)) {
    throw new Error("RPC chain does not match the requested deployment network");
  }
  const point = JSON.parse(fs.readFileSync(process.env.DEPLOY_POINT_ARTIFACT, "utf8"));
  if (fs.existsSync(recordPath)) {
    const previous = JSON.parse(fs.readFileSync(recordPath, "utf8"));
    if (previous.status !== "ready") throw new Error("Incomplete deployment record requires review");
    if (previous.schemaVersion !== 1 || previous.network !== network || previous.chainId !== chainId ||
        previous.genesisHash !== genesis.hash ||
        Object.keys(revisions).some(key => previous.revisions?.[key] !== revisions[key])) {
      throw new Error("The record belongs to a different chain or revision; use a new record for a new release");
    }
    await verify(previous, point.abi);
    console.log(`Verified existing ${network} deployment at block ${previous.deployBlock}: ${recordPath}`);
    return;
  }

  if (network === "dlp" && !process.env.REWARD_DEPLOY_PRIVATE_KEY) {
    throw new Error("A DLP testnet deployer is required in REWARD_DEPLOY_PRIVATE_KEY");
  }
  const [owner] = await ethers.getSigners();
  if (!owner) throw new Error("The selected RPC has no deployment signer");
  const record = {
    schemaVersion: 1, status: "deploying", network, chainId, genesisHash: genesis.hash,
    revisions, deployer: owner.address, contracts: {},
  };
  save(record, true);

  async function track(name, contract) {
    const receipt = await contract.deploymentTransaction()?.wait();
    const address = await contract.getAddress();
    if (!receipt || receipt.status !== 1 || receipt.contractAddress !== address) {
      throw new Error(`Deployment receipt missing for ${name}`);
    }
    record.contracts[name] = {
      address, blockNumber: receipt.blockNumber, blockHash: receipt.blockHash,
      transactionHash: receipt.hash, runtimeHash: await runtimeHash(address),
    };
    record.deployBlock = Math.min(record.deployBlock ?? receipt.blockNumber, receipt.blockNumber);
    save(record);
    return contract;
  }

  const thb = await track("thb", await ethers.deployContract("THB", ["Thai Baht Token", "THB", 18], owner));
  const coupon = await track("coupon", await ethers.deployContract("Coupon", ["Marketplace Coupons", "MPC"], owner));
  await track("pointFactory", await new ethers.ContractFactory(point.abi, point.bytecode, owner).deploy());
  const vault = await track("vault", await ethers.deployContract("Vault", [thb.target], owner));
  const marketFactory = await ethers.getContractFactory("MarketplaceUpgradeable", owner);
  const marketplace = await track("marketplace", await upgrades.deployProxy(
    marketFactory, [thb.target, coupon.target, vault.target], { kind: "uups" },
  ));
  const implementation = await upgrades.erc1967.getImplementationAddress(marketplace.target);
  record.contracts.marketplaceImplementation = { address: implementation, runtimeHash: await runtimeHash(implementation) };
  save(record);
  for (const contract of [vault, coupon]) {
    const receipt = await (await contract.setMarketplace(marketplace.target)).wait();
    if (receipt?.status !== 1) throw new Error("Marketplace authorization did not complete");
  }
  await verify(record, point.abi);
  record.status = "ready";
  save(record);
  console.log(`Recorded ${network} deployment starting at block ${record.deployBlock}: ${recordPath}`);
}

main().catch(error => {
  console.error(error.shortMessage ?? error.message);
  process.exitCode = 1;
});
