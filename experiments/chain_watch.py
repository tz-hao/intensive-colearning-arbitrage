#!/usr/bin/env python3
"""chain_watch.py — 新人链上数据观察演示：真实拉取三类数据

1. 价格类   : CoinGecko 免费 API（无 key）
2. 协议/TVL : DefiLlama 免费 API（无 key）
3. 池子类   : GeckoTerminal 免费 API（无 key）— DEX 流动性池实时数据

全部真实 API 调用，走本地代理（与 lifi_routes.py 同款）。
输出演示：新人拿到这些数据后应该看什么。
"""

import json
import os
import urllib.request

PROXY = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or "http://127.0.0.1:7897"
)
UA = {"User-Agent": "chain-watch/0.1 (colearning demo)"}


def get(url: str, timeout: int = 30) -> dict:
    proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    opener = urllib.request.build_opener(proxy)
    req = urllib.request.Request(url, headers=UA)
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def section(title: str):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


# ── 1. 价格类：CoinGecko 简单价格 ──────────────────────────────
section("① 价格类 | CoinGecko /simple/price（无 key）")
try:
    d = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,usd-coin&vs_currencies=usd&include_24hr_change=true")
    for k, v in d.items():
        print(f"  {k:14s} ${v['usd']:>12,.2f}   24h: {v['usd_24h_change']:+.2f}%")
    print("  → 新人看点：价格本身不重要，24h 变化 + 稳定币钉住(≈$1) 才是"
          "\n    判断市场状态（risk-on/off）和稳定币脱锚风险的起点")
except Exception as e:
    print(f"  ⚠ {e}")

# ── 2. 协议/TVL 类：DefiLlama 前 8 协议 ─────────────────────────
section("② 协议/TVL | DefiLlama /protocols（无 key）")
try:
    d = get("https://api.llama.fi/protocols")
    top = sorted(d, key=lambda x: -(x.get("tvl") or 0))[:8]
    print(f"  {'协议':<18}{'链数':>4}{'TVL(亿美元)':>12}{'24h变化':>9}")
    for p in top:
        ch = p.get("change_1d") or 0
        print(f"  {p['name']:<18}{len(p.get('chains',[])):>4}{p['tvl']/1e8:>12,.1f}{ch:>+8.2f}%")
    print("  → 新人看点：TVL 前几名 = 市场资金集中地；清算套利要盯的借贷协议"
          "（Aave/Compound/Morpho）在这里都能看到规模")
except Exception as e:
    print(f"  ⚠ {e}")

# ── 3. 池子类：GeckoTerminal 以太坊热门池 ──────────────────────
section("③ 池子类 | GeckoTerminal /networks/eth/pools（无 key）")
try:
    d = get("https://api.geckoterminal.com/api/v2/networks/eth/trending_pools")
    pools = d["data"][:6]
    print(f"  {'池子':<40}{'流动性(万$)':>12}{'24h量(万$)':>12}{'24h交易':>8}")
    for p in pools:
        attr = p["attributes"]
        tx = attr["transactions"]["h24"]
        trades = tx["buys"] + tx["sells"]
        print(f"  {attr['name'][:38]:<40}{float(attr['reserve_in_usd'])/1e4:>12,.1f}"
              f"{float(attr['volume_usd']['h24'])/1e4:>12,.1f}{trades:>8}")
    print("  → 新人看点：流动性深度(储备) vs 24h 成交量 = 换手率；"
          "深池子滑点小（套利进出安全），浅池子价差大但一进就砸穿")
except Exception as e:
    print(f"  ⚠ {e}")

# ── 4. 链类：LI.FI /chains（复用共学基建）──────────────────────
section("④ 链类 | LI.FI /chains（复用共学基建，无 key）")
try:
    d = get("https://li.quest/v1/chains")
    chains = sorted(d["chains"], key=lambda x: -(x.get("popularity") or 0))[:6]
    for c in chains:
        nt = c.get("nativeToken") or {}
        sym = nt.get("symbol", "?") if isinstance(nt, dict) else str(nt)
        print(f"  {c['name']:<14} id={c['id']:<6} native={sym}")
    print("  → 新人看点：链的 id 就是 API 参数（42161=Arbitrum, 8453=Base…）；"
          "我们 08-09 的跨链脚本用的就是这套 id")
except Exception as e:
    print(f"  ⚠ {e}")
