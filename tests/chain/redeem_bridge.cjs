// Isolated Hardhat fixture, copied into a git archive by verify_redeem_chain.py.
const readline = require("node:readline");
const { ethers, network, upgrades } = require("hardhat");
if (network.name !== "hardhat") throw new Error("Redeem fixtures require the isolated hardhat network");
const mnemonic = "test test test test test test test test test test test junk";
let state;

async function setup({ variant = "Marketplace", signer = "operator", scenario = "normal" }) {
  await network.provider.send("hardhat_reset");
  const [owner, seller, buyer, operator, other, outsider] = await ethers.getSigners();
  const thb = await (await ethers.getContractFactory("THB")).deploy("Test THB", "THB", 18);
  const coupon = await (await ethers.getContractFactory("Coupon")).deploy("Test coupon", "C");
  const vault = await (await ethers.getContractFactory("Vault")).deploy(thb.target);
  const factory = await ethers.getContractFactory(variant);
  const args = [thb.target, coupon.target, vault.target];
  const marketplace = variant === "Marketplace" ? await factory.deploy(...args)
    : await upgrades.deployProxy(factory, args, { kind: "uups" });
  await marketplace.waitForDeployment();
  await coupon.setMarketplace(marketplace.target);
  await vault.setMarketplace(marketplace.target);
  for (const actor of [seller, buyer, other]) await marketplace.addToWhitelist(actor.address);
  const now = (await ethers.provider.getBlock("latest")).timestamp;
  await coupon.createCouponType("test", scenario === "not-started" ? now + 3600 : now - 1, now + 7200);
  if (["direct", "not-started"].includes(scenario)) {
    await coupon.mint(buyer.address, 1, 3, ["FIRST", "SECOND", "THIRD"]);
  } else if (scenario !== "empty") {
    await coupon.mint(seller.address, 1, 4, ["FIRST", "SECOND", "THIRD", "FOURTH"]);
    await coupon.connect(seller).setApprovalForAll(marketplace.target, true);
    await marketplace.connect(seller).listCoupon(1, 4, 100n, thb.target);
    for (const actor of [buyer, other]) {
      await thb.mint(actor.address, 1000n);
      await thb.connect(actor).approve(vault.target, 1000n);
    }
    await marketplace.connect(buyer).buyCoupon(1, 2);
    await marketplace.connect(other).buyCoupon(1, 1);
  }
  await coupon.connect(buyer).setApprovalForAll(operator.address, true);
  if (scenario === "expired") {
    await network.provider.send("evm_increaseTime", [8000]);
    await network.provider.send("evm_mine");
  }
  const roles = { owner: [owner, 0], holder: [buyer, 2], operator: [operator, 3], outsider: [outsider, 5] };
  const [actor, index] = roles[signer];
  const wallet = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, `m/44'/60'/0'/0/${index}`).connect(ethers.provider);
  if (wallet.address !== actor.address) throw new Error("Local signer derivation mismatch");
  const implementation = variant === "Marketplace" ? null : await upgrades.erc1967.getImplementationAddress(marketplace.target);
  const hashes = {};
  for (const address of [coupon.target, marketplace.target, vault.target, thb.target, ...(implementation ? [implementation] : [])]) {
    hashes[address.toLowerCase()] = ethers.keccak256(await ethers.provider.getCode(address));
  }
  state = { owner, seller, buyer, operator, other, outsider, thb, coupon, vault, marketplace, wallet,
    scenario, variant, implementation, hashes, prepares: 0, broadcasts: 0, callbackBroken: false };
  return { chainId: "1337", coupon: coupon.target, marketplace: marketplace.target, vault: vault.target,
    holder: buyer.address, signer: wallet.address, variant, implementation, runtimeHashes: hashes };
}

async function verify(payload) {
  if ((await ethers.provider.getNetwork()).chainId !== 1337n || payload.chainId !== "1337" ||
      payload.couponContractAddress.toLowerCase() !== state.coupon.target.toLowerCase()) return { code: "REDEEM_UNAVAILABLE" };
  for (const [address, hash] of Object.entries(state.hashes)) {
    if (ethers.keccak256(await ethers.provider.getCode(address)) !== hash) return { code: "REDEEM_UNAVAILABLE" };
  }
  if (state.implementation && (await upgrades.erc1967.getImplementationAddress(state.marketplace.target)) !== state.implementation)
    return { code: "REDEEM_UNAVAILABLE" };
  if ((await state.coupon.marketplace()).toLowerCase() !== state.marketplace.target.toLowerCase() ||
      (await state.vault.marketplace()).toLowerCase() !== state.marketplace.target.toLowerCase()) return { code: "REDEEM_UNAVAILABLE" };
  const signer = state.wallet.address.toLowerCase(), holder = payload.walletAddress.toLowerCase();
  if ((await state.coupon.owner()).toLowerCase() !== signer && signer !== holder &&
      !(await state.coupon.isApprovedForAll(holder, signer))) return { code: "SIGNER_UNAUTHORIZED" };
  return { code: null };
}

async function prepare(payload) {
  const data = state.coupon.interface.encodeFunctionData("redeemFrom", [payload.walletAddress, BigInt(payload.typeId), 1n]);
  const fees = await ethers.provider.getFeeData();
  const nonce = await ethers.provider.getTransactionCount(state.wallet.address, "pending");
  const raw = await state.wallet.signTransaction({ type: 2, chainId: 1337, nonce,
    to: state.coupon.target, data, value: 0, gasLimit: 2000000n,
    maxFeePerGas: fees.maxFeePerGas, maxPriorityFeePerGas: fees.maxPriorityFeePerGas });
  const tx = ethers.Transaction.from(raw);
  if (tx.chainId !== BigInt(payload.chainId) || tx.to.toLowerCase() !== payload.couponContractAddress.toLowerCase() ||
      tx.from !== state.wallet.address || tx.data !== data || tx.value !== 0n || tx.nonce !== nonce)
    throw new Error("Signed transaction does not match verified payload");
  state.prepares++;
  return { raw, hash: tx.hash, signer: tx.from, nonce: String(nonce) };
}

async function broadcast(raw) {
  if (state.scenario === "signer-revoked" && !state.callbackBroken) {
    await state.coupon.connect(state.buyer).setApprovalForAll(state.operator.address, false);
    state.callbackBroken = true;
  }
  if (state.scenario === "callback-revert" && !state.callbackBroken) {
    // Configuration changes after verification, before broadcast. Use operator signer
    // so this owner transaction does not consume the prepared transaction's nonce.
    await state.vault.setMarketplace(state.outsider.address);
    state.callbackBroken = true;
  }
  const hash = ethers.keccak256(raw);
  state.broadcasts++;
  try { await ethers.provider.broadcastTransaction(raw); }
  catch (error) {
    if (!(await ethers.provider.getTransactionReceipt(hash)) && !(await ethers.provider.getTransaction(hash))) throw error;
  }
  return { hash };
}

async function receipt(hash) {
  const value = await ethers.provider.getTransactionReceipt(hash);
  if (!value) return null;
  const block = await ethers.provider.getBlock(value.blockNumber);
  let errorName = null, errorReason = null, errorOrigin = "unknown";
  if (value.status === 0) {
    const trace = await network.provider.send("debug_traceTransaction", [hash, { disableMemory: true, disableStorage: true, disableStack: true }]);
    // Coupon business reverts execute before any external call. A trace containing
    // an external call cannot prove Coupon origin from a duplicate error selector.
    errorOrigin = trace.structLogs.some(log => ["CALL", "STATICCALL", "DELEGATECALL", "CALLCODE"].includes(log.op)) ? "callback" : "coupon";
    try {
      const decoded = state.coupon.interface.parseError("0x" + trace.returnValue.replace(/^0x/, ""));
      errorName = decoded.name;
      errorReason = decoded.name === "Error" ? decoded.args[0] : null;
    } catch (_) { errorOrigin = "unknown"; }
  }
  return { chainId: "1337", to: value.to, hash: value.hash, blockHash: value.blockHash,
    blockNumber: String(value.blockNumber), minedAt: new Date(block.timestamp * 1000).toISOString(), status: value.status,
    logs: value.logs.map(log => ({ address: log.address, topics: log.topics, data: log.data, index: log.index,
      hash: log.transactionHash, blockHash: log.blockHash })), errorName, errorOrigin, errorReason };
}

async function inspect() {
  const escrows = [];
  for (const id of [1, 2]) {
    const e = await state.vault.escrows(id);
    escrows.push({ active: e.active, remaining: String(e.remainingAmount), settled: String(e.couponsSettled) });
  }
  return { balance: String(await state.coupon.balanceOf(state.buyer.address, 1)),
    available: String(await state.coupon.getAvailableRedeemCodeCount(1)),
    firstUsed: (await state.coupon.getRedeemCodeCount(1)) > 0n ? await state.coupon.isRedeemCodeUsed(1, 0) : false,
    sellerBalance: String(await state.thb.balanceOf(state.seller.address)), escrows,
    backing: (await state.marketplace.getHeldBacking(state.buyer.address, 1)).map(x => ({ id: String(x.escrowId), units: String(x.units) })),
    prepares: state.prepares, broadcasts: state.broadcasts };
}

const commands = { setup, verify, prepare, broadcast: ({ raw }) => broadcast(raw),
  receipt: ({ hash }) => receipt(hash), inspect,
  revoke: async () => { await state.coupon.connect(state.buyer).setApprovalForAll(state.operator.address, false); return true; },
  automine: async ({ enabled }) => { await network.provider.send("evm_setAutomine", [enabled]); return true; },
  mine: async () => { await network.provider.send("evm_mine"); return true; } };
let queue = Promise.resolve();
readline.createInterface({ input: process.stdin }).on("line", line => {
  queue = queue.then(async () => {
    const request = JSON.parse(line);
    try {
      const result = await commands[request.method](request.args || {});
      console.log(JSON.stringify({ bridge: true, id: request.id, result }));
    } catch (error) {
      console.log(JSON.stringify({ bridge: true, id: request.id, error: String(error.message) }));
    }
  });
});
console.log(JSON.stringify({ bridge: true, ready: true }));
