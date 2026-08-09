#!/usr/bin/env python3
"""lifi_routes.py — LI.FI 路由查询：多路由按净收益排序输出 Top3

模块 3 Day3（08-09）产出 | 对应 PLAN.md 阶段一 08-09 任务
核心逻辑：调 GET /quote（单条最优）+ POST /advanced/routes（多条比较）
         逐条算净收益（跨链同币场景：净收益 = toAmount - fromAmount，LI.FI 已扣全部费用）
         按净收益降序输出 Top3

用法:
    python3 lifi_routes.py                          # 默认: 10,000 USDC Arb → Eth
    python3 lifi_routes.py --amount 50000           # 5 万 USDC
    python3 lifi_routes.py --from-chain 10 --to-chain 8453 --from-token <addr> --to-token <addr>
"""

import argparse
import json
import os
import sys
import urllib.request

API = "https://li.quest/v1"
PROXY = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or "http://127.0.0.1:7897"
)

# 默认参数：USDC Arbitrum → USDC Ethereum（10,000 USDC，slippage 0.5%）
DEFAULT = {
    "from_chain": 42161,
    "to_chain": 1,
    "from_token": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC (Arbitrum)
    "to_token": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",    # USDC (Ethereum)
    "address": "0xbB67048D9CEc046E3163A6D7A881896D49255163",
    "slippage": 0.005,
}
USDC_DECIMALS = 6


def opener():
    proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    return urllib.request.build_opener(proxy)


def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "lifi-routes/0.1"})
    with opener().open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def api_post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "lifi-routes/0.1"},
    )
    with opener().open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _sum_cost(route: dict, key: str) -> float:
    """费用求和：兼容 quote（费用在顶层 estimate）和 route（费用在每步 estimate）。"""
    total = 0.0
    for c in route.get("estimate", {}).get(key, []):
        total += float(c.get("amountUSD") or 0)
    for s in route.get("steps", []):
        for c in s.get("estimate", {}).get(key, []):
            total += float(c.get("amountUSD") or 0)
    return total


def parse_route(route: dict, amount_in_raw: int) -> dict:
    """解析一条路由 → 净收益/成本/时长/路径。跨链同币：净收益 = toAmount - fromAmount。"""
    est = route.get("estimate", {})
    # quote 响应 toAmount 在 estimate 里；advanced/routes 的 route 在顶层——两者兼容
    to_raw = int(route.get("toAmount") or est.get("toAmount") or 0)
    gas_usd = _sum_cost(route, "gasCosts")
    fee_usd = _sum_cost(route, "feeCosts")
    duration = int(est.get("executionDuration") or 0)
    if not duration:  # advanced/routes 的时长在每步 estimate 里
        duration = sum(
            int(s.get("estimate", {}).get("executionDuration") or 0)
            for s in route.get("steps", [])
        )
    tools = " → ".join(s.get("tool", "?") for s in route.get("steps", []))

    net_raw = to_raw - amount_in_raw
    net_usd = net_raw / (10 ** USDC_DECIMALS)
    net_pct = net_raw / amount_in_raw * 100 if amount_in_raw else 0.0
    return {
        "to_amount": to_raw / (10 ** USDC_DECIMALS),
        "net_usd": net_usd,
        "net_pct": net_pct,
        "gas_usd": gas_usd,
        "fee_usd": fee_usd,
        "duration": duration,
        "tools": tools,
        "route_id": route.get("id", ""),
    }


def fmt(r: dict) -> str:
    flag = "✅" if r["net_usd"] > 0 else "❌"
    return (
        f"{flag} 净收益 {r['net_usd']:+.2f} USD ({r['net_pct']:+.2f}%) | "
        f"到账 {r['to_amount']:.2f} USDC | 时长 {r['duration']}s | "
        f"gas ${r['gas_usd']:.2f} + 费 ${r['fee_usd']:.2f} | {r['tools']}"
    )


def main():
    ap = argparse.ArgumentParser(description="LI.FI 多路由净收益排序 Top3")
    ap.add_argument("--from-chain", type=int, default=DEFAULT["from_chain"])
    ap.add_argument("--to-chain", type=int, default=DEFAULT["to_chain"])
    ap.add_argument("--from-token", default=DEFAULT["from_token"])
    ap.add_argument("--to-token", default=DEFAULT["to_token"])
    ap.add_argument("--amount", type=float, default=10000, help="USDC 金额")
    ap.add_argument("--address", default=DEFAULT["address"])
    ap.add_argument("--slippage", type=float, default=DEFAULT["slippage"])
    ap.add_argument("--max-routes", type=int, default=5)
    args = ap.parse_args()

    amount_raw = int(args.amount * (10 ** USDC_DECIMALS))
    print(f"▶ 场景: {args.amount:,.0f} USDC 从链 {args.from_chain} → 链 {args.to_chain} (slippage {args.slippage:.1%})\n")

    # ── 1. 单条最优 GET /quote ──────────────────────────────
    q_url = (
        f"{API}/quote?fromChain={args.from_chain}&toChain={args.to_chain}"
        f"&fromToken={args.from_token}&toToken={args.to_token}"
        f"&fromAmount={amount_raw}&fromAddress={args.address}&slippage={args.slippage}"
    )
    print("=== ① 单条最优 (GET /quote) ===")
    try:
        quote = api_get(q_url)
        q = parse_route(quote, amount_raw)
        print(fmt(q))
    except Exception as e:
        print(f"  ⚠ /quote 失败: {e}")

    # ── 2. 多条比较 POST /advanced/routes ──────────────────
    body = {
        "fromChainId": args.from_chain,
        "toChainId": args.to_chain,
        "fromTokenAddress": args.from_token,
        "toTokenAddress": args.to_token,
        "fromAmount": str(amount_raw),
        "fromAddress": args.address,
        "options": {
            "slippage": args.slippage,
            "maxNumberOfRoutes": args.max_routes,
        },
    }
    print(f"\n=== ② 多路由比较 (POST /advanced/routes, max={args.max_routes}) ===")
    try:
        routes = api_post(f"{API}/advanced/routes", body).get("routes", [])
        if not routes:
            print("  ⚠ 没有返回路由")
            sys.exit(0)
        parsed = sorted((parse_route(r, amount_raw) for r in routes), key=lambda x: -x["net_usd"])
        for i, r in enumerate(parsed, 1):
            print(f"#{i}  {fmt(r)}")
        print(f"\n共返回 {len(routes)} 条路由，Top3 如上；净收益均为 {parsed[0]['net_usd']:+.2f} USD 起")
    except Exception as e:
        print(f"  ⚠ /advanced/routes 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
