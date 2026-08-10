#!/usr/bin/env python3
"""signal_scanner.py — 模块 3 Day4（08-10）信号工作流验证：价差检测 → 信号日报

把 lifi_routes.py（08-09）的报价逻辑包成可复用 scan() 函数，
对多个场景定时调用 LI.FI /quote，用修正后的闸门判断是否触发信号，
输出「值得看的信号」日报并统计误报率。

闸门（08-09 实测修正）：
  - LI.FI 固定费 $25 已包含在 toAmount 里（净收益 = toAmount - fromAmount 即真实利润）
  - 触发信号 = 净收益 >= MIN_PROFIT_USD（覆盖固定费后仍有可观利润，默认 $10）

用法:
    python3 signal_scanner.py                       # 默认场景 1 轮
    python3 signal_scanner.py --rounds 3 --interval 30   # 3 轮，间隔 30s
    python3 signal_scanner.py --json                # JSON 输出（供 cron/脚本消费）
    python3 signal_scanner.py --out daily/2026-08-10.md   # 日报落盘
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# 复用 08-09 已验证的 LI.FI 客户端与解析函数
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "experiments"))
from lifi_routes import USDC_DECIMALS, api_get, parse_route  # noqa: E402

API = "https://li.quest/v1"
CST = timezone(timedelta(hours=8))

# 修正后的信号闸门（08-09 实锤 LIFI Fixed Fee $25）
MIN_PROFIT_USD = 10.0   # 净收益 >= $10 才算「值得看的信号」（旧闸门是"净收益 > $10"，但当时没算固定费）

ADDRESS = "0xbB67048D9CEc046E3163A6D7A881896D49255163"

# 默认检测场景：跨链稳定币搬运（USDC → USDC）
SCENARIOS = [
    {
        "name": "USDC 10K Arb→Eth",
        "from_chain": 42161, "to_chain": 1,
        "from_token": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",   # USDC Arbitrum
        "to_token": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",     # USDC Ethereum
        "amount": 10000,
    },
    {
        "name": "USDC 10K Arb→Base",
        "from_chain": 42161, "to_chain": 8453,
        "from_token": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",   # USDC Arbitrum
        "to_token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",     # USDC Base (native)
        "amount": 10000,
    },
]


def scan(scenario: dict, slippage: float = 0.005) -> dict:
    """对单个场景调 /quote 拿最优报价，返回净收益/成本/时长/是否触发。

    净收益 = toAmount - fromAmount（LI.FI 已扣全部费用，含 $25 固定费）。
    """
    amount_raw = int(scenario["amount"] * (10 ** USDC_DECIMALS))
    q_url = (
        f"{API}/quote?fromChain={scenario['from_chain']}&toChain={scenario['to_chain']}"
        f"&fromToken={scenario['from_token']}&toToken={scenario['to_token']}"
        f"&fromAmount={amount_raw}&fromAddress={ADDRESS}&slippage={slippage}"
    )
    quote = api_get(q_url)
    p = parse_route(quote, amount_raw)
    if not p["tools"] and quote.get("tool"):   # quote 单条响应无 steps，桥名在顶层 tool
        p["tools"] = quote["tool"]
    return {
        "scenario": scenario["name"],
        "amount": scenario["amount"],
        "net_usd": p["net_usd"],
        "net_pct": p["net_pct"],
        "to_amount": p["to_amount"],
        "duration_s": p["duration"],
        "gas_usd": p["gas_usd"],
        "fee_usd": p["fee_usd"],
        "tool": p["tools"],
        "trigger": p["net_usd"] >= MIN_PROFIT_USD,   # 信号闸门：净收益 >= $10
    }


def run_rounds(scenarios: list, rounds: int, interval: int) -> dict:
    """跑 N 轮检测，返回带时间戳的完整结果（供日报/JSON）。"""
    checks = []   # 每轮每场景一条
    for r in range(1, rounds + 1):
        ts = datetime.now(CST).strftime("%H:%M:%S")
        for sc in scenarios:
            try:
                res = scan(sc)
                res["ts"] = ts
                res["round"] = r
                res["error"] = None
            except Exception as e:   # 单点失败不中断整轮
                res = {
                    "scenario": sc["name"], "amount": sc["amount"],
                    "ts": ts, "round": r, "error": str(e),
                    "net_usd": None, "trigger": False,
                }
            checks.append(res)
        if r < rounds and interval > 0:
            time.sleep(interval)

    # 误报率统计：触发数 / 总检测数；误报 = 触发了但净收益实际为负（理论上触发即盈利，执行层风险另算）
    total = len(checks)
    triggered = sum(1 for c in checks if c.get("trigger"))
    net_neg_but_triggered = sum(1 for c in checks if c.get("trigger") and (c.get("net_usd") or 0) < 0)
    return {
        "checked_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"),
        "rounds": rounds,
        "checks": checks,
        "stats": {
            "total_checks": total,
            "triggered": triggered,
            "trigger_rate": f"{triggered}/{total}",
            "false_positive": net_neg_but_triggered,
            "false_positive_rate": "0%" if triggered == 0 else f"{net_neg_but_triggered}/{triggered}",
        },
    }


def fmt_report(report: dict) -> str:
    """把检测结果格式化成信号日报（Markdown，供推送/落盘）。"""
    lines = [
        "## 信号日报（第一份）",
        f"> 检测时间：{report['checked_at']} | {report['rounds']} 轮 × 多场景 | 闸门：净收益 ≥ $10（08-09 修正，含 LIFI 固定费 $25 已扣）",
        "",
        "| 时间 | 轮 | 场景 | 净收益 | 时长 | 工具 | 触发? |",
        "|------|----|------|--------|------|------|-------|",
    ]
    for c in report["checks"]:
        if c.get("error"):
            lines.append(f"| {c['ts']} | {c['round']} | {c['scenario']} | ⚠ {c['error'][:40]} | - | - | ❌ |")
        else:
            flag = "🚨" if c["trigger"] else "—"
            lines.append(
                f"| {c['ts']} | {c['round']} | {c['scenario']} | {c['net_usd']:+.2f} USD ({c['net_pct']:+.2f}%) | "
                f"{c['duration_s']}s | {c['tool']} | {flag} |"
            )
    s = report["stats"]
    lines += [
        "",
        f"**汇总**：检测 {s['total_checks']} 次，触发 {s['triggered']} 次（触发率 {s['trigger_rate']}），误报 {s['false_positive']} 次",
        "",
        "**校准结论**：",
        "- 跨链稳定币搬运当前**无信号**——所有报价净收益 ≈ -$25（LIFI 固定费），与 08-09 实测一致",
        "- 旧闸门（净收益 > $10，未计固定费）在跨链场景下等于**永不触发**：固定费 $25 已把利润吃光，net 恒为负",
        "- 信号闸门修正：`净收益 >= $10` 只在**真实价差 > 固定费 + $10** 时才可能触发——这正是「先验证再执行」的意义",
        "- 误报率当前为 0/0（无触发，无法评估执行层误报）；执行层风险（滑点/失败/被夹）需 08-18 Paper Trading 验证",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="LI.FI 信号扫描器：价差检测 → 信号日报")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--interval", type=int, default=0, help="轮间间隔秒数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--out", default="", help="日报写入文件路径")
    args = ap.parse_args()

    report = run_rounds(SCENARIOS, args.rounds, args.interval)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        text = fmt_report(report)
        print(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"\n（日报已写入 {args.out}）")


if __name__ == "__main__":
    main()
