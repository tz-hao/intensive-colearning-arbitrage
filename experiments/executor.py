#!/usr/bin/env python3
"""executor.py — 执行链路搭建：从"信号"到"真上链"（模块 5 · Day 1 · 08-16）

五步链路（对应 PLAN.md 08-16 任务）：
  1. 钱包接入   — web3 从私钥恢复签名器；token 授权 approve 给 LI.FI Diamond（权限最小化）
  2. 拿可执行交易 — GET /quote → 新版 API 直接返回顶层 transactionRequest（to/data/value/gasLimit/gasPrice）
  3. 签名 + 广播 — 本地签名后发送到源链 RPC，记录 txHash
  4. 状态追踪   — GET /status 轮询 txHash：PENDING → DONE / FAILED
  5. 封装产出   — 入参 token/amount/fromChain/toChain；出参 txHash + 状态日志

用法:
  # 干跑（默认）：真实报价 + 真实签名，但不广播（安全，不需要 gas）
  python3 executor.py --amount 10000
  python3 executor.py --amount 0.05 --token eth --from-chain 8453 --to-chain 42161

  # 真广播（需要源链有 gas + 私钥）
  PRIVATE_KEY=0x... python3 executor.py --amount 10000 --broadcast

  # 只查状态（轮询一个已广播的 txHash）
  python3 executor.py --status 0x<txHash> --from-chain 42161 --to-chain 1

  # 测试网（注意：LI.FI 测试网路由有限，多数 404；主网演示用 --dry-run 最稳）
  PRIVATE_KEY=0x... python3 executor.py --amount 0.01 --token eth \
      --from-chain 84532 --to-chain 421614 --broadcast --rpc https://sepolia.base.org

关键 API 事实（08-16 实测）：
  - /quote 响应顶层直接带 transactionRequest；POST /v1/transactionRequest 已 404（新版废弃）
  - approve 目标是 quote.estimate.approvalAddress（= LI.FI Diamond 合约），不是随便的桥合约
  - native token（ETH 等）不需要 approve
  - /status 只需 txHash 参数（fromChain/toChain 可选，加速查询）
  - 状态值: NOT_FOUND / INVALID / PENDING / DONE / FAILED
"""

import argparse
import json
import os
import sys
import time
import urllib.request

# ─────────────────────────── 常量 ───────────────────────────
API = "https://li.quest/v1"
PROXY = os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7897"

NATIVE = "0x0000000000000000000000000000000000000000"  # 零地址 = native 币

# 默认参数：USDC Arbitrum → USDC Ethereum（与 lifi_routes.py / signal_scanner.py 对齐）
DEFAULT = {
    "from_chain": 42161,
    "to_chain": 1,
    "from_token": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC (Arbitrum)
    "to_token": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",    # USDC (Ethereum)
    "slippage": 0.005,
}
USDC_DECIMALS = 6

# 源链 RPC（广播用；按 chainId 匹配）
RPC_BY_CHAIN = {
    42161: "https://arb1.arbitrum.io/rpc",
    8453: "https://mainnet.base.org",
    1: "https://eth.llamarpc.com",
    421614: "https://sepolia-rollup.arbitrum.io/rpc",
    84532: "https://sepolia.base.org",
}


# ─────────────────────────── HTTP 工具 ───────────────────────────
def opener():
    proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    return urllib.request.build_opener(proxy)


def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "executor/0.1"})
    with opener().open(req, timeout=60) as r:
        return json.loads(r.read().decode())


def rpc_call(url: str, method: str, params: list, timeout=30) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": "executor/0.1"}
    )
    with opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def h2i(v: str) -> int:
    """hex → int（兼容 '0x0' / 无前缀）"""
    return int(v, 16) if isinstance(v, str) else int(v)


# ─────────────────────────── ① 拿可执行交易 ───────────────────────────
def get_quote(from_chain, to_chain, from_token, to_token, amount_raw, address, slippage):
    """GET /quote → 返回 quote（含顶层 transactionRequest + estimate.approvalAddress）"""
    q_url = (
        f"{API}/quote?fromChain={from_chain}&toChain={to_chain}"
        f"&fromToken={from_token}&toToken={to_token}"
        f"&fromAmount={amount_raw}&fromAddress={address}&slippage={slippage}"
    )
    quote = api_get(q_url)
    txr = quote.get("transactionRequest")
    if not txr:
        raise RuntimeError(f"quote 响应没有 transactionRequest（tool={quote.get('tool')}）")
    return quote


def s2i(v) -> int:
    """十进制字符串 → int（LI.FI 的 toAmount/fromAmount 等是十进制字符串，不是 hex！）"""
    return int(str(v)) if v is not None else 0


def build_tx(quote, address, nonce):
    """quote.transactionRequest → 标准待签名交易 dict（web3 可签名格式）"""
    txr = quote["transactionRequest"]
    tx = {
        "to": txr["to"],
        "data": txr["data"],
        "value": h2i(txr.get("value", "0x0")),
        "gas": h2i(txr["gasLimit"]),
        "gasPrice": h2i(txr["gasPrice"]),
        "chainId": h2i(txr["chainId"]),
        "nonce": nonce,
    }
    return tx


# ─────────────────────────── ② 授权检查 + approve ───────────────────────────
def get_allowance(rpc, token, owner, spender):
    """eth_call allowance(token, owner, spender) → 当前授权额（raw）"""
    # selector: allowance(address,address)
    sel = "0xdd62ed3e"
    data = sel + owner[2:].lower().rjust(64, "0") + spender[2:].lower().rjust(64, "0")
    res = rpc_call(rpc, "eth_call", [{"to": token, "data": data}, "latest"])
    return h2i(res.get("result", "0x0"))


def build_approve_tx(token, spender, amount_raw, chain_id, nonce, rpc):
    """构造 approve 交易（权限最小化：只给 LI.FI Diamond 授权所需金额，不设无限额度）"""
    # approve(address,uint256)
    sel = "0x095ea7b3"
    data = sel + spender[2:].lower().rjust(64, "0") + hex(amount_raw)[2:].rjust(64, "0")
    gas_price_res = rpc_call(rpc, "eth_gasPrice", [])
    gas_price = h2i(gas_price_res.get("result", "0x0"))
    return {
        "to": token,
        "data": data,
        "value": 0,
        "gas": 60000,  # approve 固定消耗 ~46k gas
        "gasPrice": gas_price,
        "chainId": chain_id,
        "nonce": nonce,
    }


# ─────────────────────────── ③ 签名 ───────────────────────────
def sign_tx(account, tx):
    """本地签名 → (signed_raw_hex, txHash)"""
    signed = account.sign_transaction(tx)
    return "0x" + signed.raw_transaction.hex(), signed.hash.hex()


# ─────────────────────────── ④ 广播 ───────────────────────────
def broadcast(rpc, signed_raw_hex):
    """eth_sendRawTransaction → txHash"""
    res = rpc_call(rpc, "eth_sendRawTransaction", [signed_raw_hex])
    err = res.get("error")
    if err:
        raise RuntimeError(f"广播失败: {err.get('message')}")
    return res.get("result")


# ─────────────────────────── ⑤ 状态追踪 ───────────────────────────
def get_status(tx_hash, from_chain=None, to_chain=None):
    """GET /status → 状态 dict（NOT_FOUND/PENDING/DONE/FAILED + 明细）。
    404 = 交易未上链/未索引，调用方按 NOT_FOUND 处理。"""
    url = f"{API}/status?txHash={tx_hash}"
    if from_chain:
        url += f"&fromChain={from_chain}"
    if to_chain:
        url += f"&toChain={to_chain}"
    try:
        return api_get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "NOT_FOUND", "substatus": "", "substatusMessage": "交易未上链或未被索引"}
        raise


def track_status(tx_hash, from_chain, to_chain, max_polls=12, interval=10):
    """轮询 /status：PENDING → 继续；DONE/FAILED/NOT_FOUND → 输出结论。"""
    print(f"  轮询 /status（最多 {max_polls} 次，每 {interval}s）…")
    for i in range(1, max_polls + 1):
        try:
            st = get_status(tx_hash, from_chain, to_chain)
        except Exception as e:
            print(f"  [{i}] ⚠ /status 请求失败: {e}")
            time.sleep(interval)
            continue
        status = st.get("status", "?")
        substatus = st.get("substatus", "")
        msg = st.get("substatusMessage", "")
        print(f"  [{i}] status={status} substatus={substatus} {msg}")
        if status == "DONE":
            recv = st.get("receiving", {})
            print(f"  ✅ DONE | 接收金额 {recv.get('amount')} | txLink {st.get('lifiExplorerLink')}")
            return st
        if status == "FAILED":
            print(f"  ❌ FAILED | {msg}")
            return st
        if status == "NOT_FOUND":
            print(f"  ⚠ NOT_FOUND | {msg or '交易不存在或尚未上链'}")
            # 继续轮询（可能刚广播还没进索引）
        if status == "INVALID":
            print(f"  ⚠ INVALID | {msg}")
            return st
        time.sleep(interval)
    print("  ⏱ 轮询超时（状态未定，稍后手动 /status 查询）")
    return None


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="LI.FI 执行链路：quote → 授权 → 签名 → 广播 → /status")
    ap.add_argument("--from-chain", type=int, default=DEFAULT["from_chain"])
    ap.add_argument("--to-chain", type=int, default=DEFAULT["to_chain"])
    ap.add_argument("--from-token", default=DEFAULT["from_token"], help="源 token 地址（ETH 用零地址）")
    ap.add_argument("--to-token", default=DEFAULT["to_token"])
    ap.add_argument("--token", default=None, help="快捷：eth = native 币（自动填零地址）")
    ap.add_argument("--amount", type=float, default=10000, help="转账金额（ETH 单位按 18 位，稳定币按 6 位）")
    ap.add_argument("--slippage", type=float, default=DEFAULT["slippage"])
    ap.add_argument("--broadcast", action="store_true", help="真广播（默认 dry-run 只签名不广播）")
    ap.add_argument("--rpc", default=None, help="覆盖源链 RPC")
    ap.add_argument("--status", default=None, metavar="TXHASH", help="只轮询这个 txHash 的状态")
    ap.add_argument("--max-polls", type=int, default=12)
    ap.add_argument("--interval", type=int, default=10)
    args = ap.parse_args()

    # ── 只查状态模式 ──
    if args.status:
        print(f"▶ 查询 txHash {args.status}")
        track_status(args.status, args.from_chain, args.to_chain, args.max_polls, args.interval)
        return

    # ── token 快捷处理 ──
    from_token = args.from_token
    if args.token and args.token.lower() in ("eth", "native", "matic", "avax"):
        from_token = NATIVE
        args.to_token = NATIVE

    # ── 私钥（签名需要）──
    pk = os.environ.get("PRIVATE_KEY")
    if not pk and not args.broadcast:
        # dry-run：生成临时随机私钥（绝不广播，仅演示签名流程）
        import secrets
        pk = "0x" + secrets.token_hex(32)
        print("⚠ 未设置 PRIVATE_KEY，已生成随机私钥（仅 dry-run 演示，勿用于广播）")
    if not pk:
        print("❌ 广播模式必须设置 PRIVATE_KEY 环境变量")
        sys.exit(1)

    # web3 初始化签名器
    try:
        from eth_account import Account
        account = Account.from_key(pk)
    except Exception as e:
        print(f"❌ 私钥无效: {e}")
        sys.exit(1)
    address = account.address
    print(f"▶ 钱包: {address}")

    # ── 金额精度 ──
    decimals = 6 if from_token != NATIVE else 18
    amount_raw = int(args.amount * (10 ** decimals))
    print(f"▶ 场景: {args.amount} from {args.from_chain} → {args.to_chain} (slippage {args.slippage:.1%})")

    # ── ① 拿可执行交易 ──
    print("\n=== ① 报价 + 可执行交易 (GET /quote) ===")
    quote = get_quote(args.from_chain, args.to_chain, from_token, args.to_token, amount_raw, address, args.slippage)
    tool = quote.get("tool")
    est = quote.get("estimate", {})
    approval_addr = est.get("approvalAddress")
    to_amount = s2i(est.get("toAmount", 0)) / (10 ** decimals)
    print(f"  tool={tool} | 到账 {to_amount:.4f} | approvalAddress={approval_addr}")
    txr = quote["transactionRequest"]
    print(f"  transactionRequest: to={txr['to']} | chainId={h2i(txr['chainId'])} | value={h2i(txr.get('value',0))}")
    print(f"    gasLimit={h2i(txr['gasLimit'])} | gasPrice={h2i(txr['gasPrice'])}")

    rpc = args.rpc or RPC_BY_CHAIN.get(args.from_chain)
    if not rpc:
        print(f"❌ 链 {args.from_chain} 无默认 RPC，用 --rpc 指定")
        sys.exit(1)

    # ── ② 授权检查（native 币跳过）──
    print("\n=== ② 授权检查（approve 给 LI.FI Diamond）===")
    nonce = h2i(rpc_call(rpc, "eth_getTransactionCount", [address, "latest"]).get("result", "0x0"))
    print(f"  nonce={nonce}")
    if from_token == NATIVE:
        print("  native 币（ETH）→ 无需 approve ✅")
    else:
        allowance = get_allowance(rpc, from_token, address, approval_addr)
        print(f"  当前 allowance: {allowance} / 需要 {amount_raw}")
        if allowance >= amount_raw:
            print("  授权充足 ✅ 直接进入签名")
        else:
            print("  授权不足 → 构造 approve 交易（仅授权所需金额，权限最小化）")
            approve_tx = build_approve_tx(from_token, approval_addr, amount_raw, args.from_chain, nonce, rpc)
            signed_approve_raw, approve_hash = sign_tx(account, approve_tx)
            print(f"  签名后的 approve txHash: {approve_hash}")
            if args.broadcast:
                sent = broadcast(rpc, signed_approve_raw)
                print(f"  ✅ approve 已广播: {sent}")
                # 等 approve 确认（轮询 receipt）
                for _ in range(30):
                    rcpt = rpc_call(rpc, "eth_getTransactionReceipt", [sent]).get("result")
                    if rcpt:
                        print(f"  approve 已确认: status={h2i(rcpt.get('status','0x0'))}")
                        nonce += 1
                        break
                    time.sleep(2)
            else:
                print("  （dry-run：不广播 approve，仅演示构造+签名）")
                nonce += 1  # 演示时假设 approve 已占一个 nonce

    # ── ③ 签名 + 广播 ──
    print("\n=== ③ 签名 + 广播 ===")
    tx = build_tx(quote, address, nonce)
    signed_raw, tx_hash = sign_tx(account, tx)
    print(f"  签名完成 | txHash = {tx_hash}")
    print(f"  raw tx 长度: {len(signed_raw)} hex chars")
    if args.broadcast:
        sent = broadcast(rpc, signed_raw)
        print(f"  ✅ 已广播! txHash = {sent}")
        tx_hash = sent
    else:
        print("  （dry-run：不广播。验证签名 → 发到链 RPC 才是真上链）")

    # ── ④ 状态追踪 ──
    print("\n=== ④ 状态追踪 (GET /status) ===")
    # dry-run 时交易没上链，/status 会 NOT_FOUND（预期行为，说明没广播）
    track_status(tx_hash, args.from_chain, args.to_chain, args.max_polls, args.interval)

    print("\n=== 完成 ===")
    print(f"  txHash: {tx_hash}")
    print(f"  广播模式: {'✅ 已上链' if args.broadcast else '❌ dry-run 未广播（需源链 gas + PRIVATE_KEY）'}")


if __name__ == "__main__":
    main()
