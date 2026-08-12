#!/usr/bin/env python3
"""cost_model.py — 模块 4 Day2（08-12）成本模型精确化：净收益公式落地

任务（PLAN.md 08-12）：
    把模块 1 的净收益公式落到代码：gas 预测、swap fee、滑点、MEV 折扣
    精确到「滑点随资金量的曲线」（模块 1 第三节洞察 → 08-11 假设的衔接）

净收益公式（同链场景，无桥费）：
    净收益 = 毛价差 - 各腿 swap fee - 两腿滑点成本 - gas 成本 - MEV 期望折扣
           = spread*V - legs*fee*V - slippage(V, 池深) - gas_usd - (1-p_mev)*名义利润

三个子模型：
    1. SlippageModel   — 恒定乘积 AMM（x*y=k）：资金量 → 价格冲击曲线
    2. GasModel        — gas 用量 × gas price（gwei）→ USD，L2 折扣
    3. MevModel        — 同链价差是 MEV bot 主战场：窗口 < 1 块 → 成功率 p_mev

用法:
    python3 cost_model.py                 # 默认：三角路径 + 价差路径曲线表
    python3 cost_model.py --pool 2000000  # 自定义池深（USDC 侧储备）
    python3 cost_model.py --json          # JSON 输出（供脚本消费）
"""

import argparse
import json

# ── 常量 ──────────────────────────────────────────────────────────────
DEFAULT_POOL_X = 2_000_000    # 池子 USDC 侧储备（演示默认：2M USDC 中深池）
DEFAULT_SPREAD = 0.006        # 毛价差 0.6%（假设 1 触发阈值，来自 08-11 hypotheses）
FEE_SWAP = 0.003              # Uniswap V3 0.3% 档（低 0.05% 池更薄，0.3% 常见）
FIXED_FEE_LIFI = 25.0         # LI.FI 固定费 $25（跨链场景专用，同链为 0）

# gas 参数（近似值，来自各链常见 swap 实测量级）
GAS_USAGE_SWAP = {
    "ethereum": 150_000,      # L1 Uniswap V3 swap ~120-180k
    "arbitrum": 250_000,      # L2 单笔更高但 gas price 低 2 个数量级
    "base":     250_000,
}
GAS_PRICE_GWEI = {"ethereum": 12.0, "arbitrum": 0.05, "base": 0.02}
ETH_USD = 3500.0

# MEV 抢跑概率（同链价差的主战场）：窗口越短、池越热 → 越高
P_MEV_DEFAULT = {"ethereum": 0.80, "arbitrum": 0.55, "base": 0.45}


# ── 子模型 1：滑点（恒定乘积 AMM）─────────────────────────────────────
class SlippageModel:
    """恒定乘积池 x*y=k，含手续费，计算价格冲击（滑点）。

    - 输入 dx，输出 dy = y * (1-f)*dx / (x + (1-f)*dx)
    - 滑点 = 1 - (dy/dx) / (y/x)：实际执行价相对当前价的偏离
    - 资金量 V 相对池深 x 的比值决定滑点：V/x 越大，滑点超线性增长
    """

    @staticmethod
    def output_amount(dx: float, x: float, y: float, fee: float = FEE_SWAP) -> float:
        """恒定乘积下输入 dx 能拿到的输出 dy（已扣手续费）。"""
        dx_in = dx * (1 - fee)
        return y * dx_in / (x + dx_in)

    @staticmethod
    def price_impact(dx: float, x: float, y: float, fee: float = FEE_SWAP) -> float:
        """价格冲击比例（0.01 = 1%）：实际成交均价相对现价的折损。"""
        if dx <= 0 or x <= 0:
            return 0.0
        dy = SlippageModel.output_amount(dx, x, y, fee)
        ideal_rate = y / x                      # 无滑点执行价
        exec_rate = dy / dx                     # 实际执行价
        return max(0.0, 1.0 - exec_rate / ideal_rate)

    @staticmethod
    def slippage_usd(dx: float, x: float, y: float, fee: float = FEE_SWAP) -> float:
        """滑点成本（USD）= 输入金额 × 价格冲击（近似，薄池时够用）。"""
        return dx * SlippageModel.price_impact(dx, x, y, fee)

    @staticmethod
    def breakeven_pool_depth(amount: float, fee: float = FEE_SWAP,
                             target_slippage: float = 0.002) -> float:
        """给定资金量，要达到目标滑点所需的池深（反向用，规划资金上限）。"""
        # 简化：滑点 ≈ (1-f)*dx / x → x ≈ (1-f)*dx / target
        return (1 - fee) * amount / target_slippage


# ── 子模型 2：gas 预测 ───────────────────────────────────────────────
class GasModel:
    """gas 成本预测：gas 用量 × gas price（gwei）→ USD。

    L1/L2 差异巨大：Arb/Base 的 gas price 低 ~2-3 个数量级，
    同链薄利策略几乎只可能在 L2 存活（这也是 08-11 选型没有排除 L2 的原因）。
    """

    def __init__(self, chain: str = "base", gas_price_gwei: float | None = None,
                 eth_usd: float = ETH_USD):
        self.chain = chain
        self.gas_usage = GAS_USAGE_SWAP.get(chain, GAS_USAGE_SWAP["base"])
        self.gas_price = gas_price_gwei or GAS_PRICE_GWEI.get(chain, 0.02)
        self.eth_usd = eth_usd

    def per_swap_usd(self) -> float:
        return self.gas_usage * self.gas_price * 1e-9 * self.eth_usd

    def total_usd(self, legs: int) -> float:
        return self.per_swap_usd() * legs

    def __repr__(self) -> str:  # noqa: D105
        return (f"GasModel({self.chain}): {self.per_swap_usd():.3f} USD/swap "
                f"(gas={self.gas_usage:,}, price={self.gas_price} gwei)")


# ── 子模型 3：MEV 折扣 ───────────────────────────────────────────────
class MevModel:
    """MEV 抢跑折扣：同链价差窗口可能 < 1 块，名义利润要乘成功率。

    p_mev 高 = 大概率被抢跑/被夹 → 期望收益打大折扣。
    冷门池/新池（检测腿的价值所在）p_mev 更低，但深度也浅——这正是模型的取舍。
    """

    def __init__(self, chain: str = "base", p_mev: float | None = None):
        self.p_mev = p_mev if p_mev is not None else P_MEV_DEFAULT.get(chain, 0.5)

    def expected(self, nominal_profit: float) -> float:
        return nominal_profit * (1 - self.p_mev)


# ── 主模型：净收益计算器 ─────────────────────────────────────────────
def net_profit(amount: float, pool_x: float, spread: float = DEFAULT_SPREAD,
               legs: int = 2, chain: str = "base",
               fee: float = FEE_SWAP, p_mev: float | None = None,
               fixed_fee: float = 0.0) -> dict:
    """单笔净收益拆解（同链场景默认 2 腿：便宜池买→贵池卖）。

    net = spread*V - legs*(fee*V + slippage_leg) - gas - mev折扣 - fixed_fee
    注意滑点按每腿输入金额近似（两腿池深不同可分别传入，这里演示用同一池深）。
    """
    gross = spread * amount                          # 毛价差收益
    swap_fee = legs * fee * amount                   # 各腿 swap fee
    # 滑点：两腿都吃（买腿在池 A 冲击，卖腿在池 B 冲击），近似同一池深
    slippage = legs * SlippageModel.slippage_usd(amount, pool_x, pool_x)
    gas = GasModel(chain).total_usd(legs)            # gas 预测
    nominal = gross - swap_fee - slippage - gas - fixed_fee   # 未计 MEV 的名义净收益
    mev_loss = nominal * (p_mev if p_mev is not None else P_MEV_DEFAULT.get(chain, 0.5))
    net = nominal - mev_loss

    return {
        "amount": amount, "pool_x": pool_x, "chain": chain, "legs": legs,
        "gross": gross, "swap_fee": swap_fee, "slippage": slippage,
        "slippage_pct": 100 * slippage / amount if amount else 0,
        "gas": gas, "fixed_fee": fixed_fee, "nominal": nominal,
        "mev_loss": mev_loss, "net": net,
        "net_pct": 100 * net / amount if amount else 0,
        "profitable": net > 0,
    }


def profit_curve(amounts: list, pool_x: float, **kw) -> list:
    """资金量 → 净收益曲线：直观看到「滑点随资金量超线性吞噬利润」。"""
    return [net_profit(a, pool_x, **kw) for a in amounts]


def fmt_row(r: dict) -> str:
    flag = "✅" if r["profitable"] else "❌"
    return (
        f"{flag} 资金 {r['amount']:>10,.0f} | 毛 {r['gross']:>8.2f} | "
        f"fee {r['swap_fee']:>7.2f} | 滑点 {r['slippage']:>8.2f} "
        f"({r['slippage_pct']:4.2f}%) | gas {r['gas']:>5.3f} | "
        f"MEV {r['mev_loss']:>8.2f} | 净 {r['net']:>8.2f} ({r['net_pct']:+.2f}%)"
    )


def main():
    ap = argparse.ArgumentParser(description="成本模型：净收益公式精确化（滑点/gas/MEV）")
    ap.add_argument("--pool", type=float, default=DEFAULT_POOL_X, help="池子 USDC 侧储备")
    ap.add_argument("--amounts", default="1000,5000,10000,25000,50000",
                    help="资金量序列（逗号分隔 USD）")
    ap.add_argument("--spread", type=float, default=DEFAULT_SPREAD, help="毛价差（0.006 = 0.6%）")
    ap.add_argument("--legs", type=int, default=2, help="swap 腿数（三角=3）")
    ap.add_argument("--chain", default="base", choices=["base", "arbitrum", "ethereum"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    amounts = [float(x) for x in args.amounts.split(",")]
    curve = profit_curve(amounts, args.pool, spread=args.spread,
                         legs=args.legs, chain=args.chain)

    if args.json:
        print(json.dumps(curve, ensure_ascii=False, indent=2))
        return

    g = GasModel(args.chain)
    print(f"▶ 成本模型 | 链={args.chain} | 池深(USDC侧)={args.pool:,.0f} | "
          f"毛价差={args.spread:.1%} | 腿数={args.legs}")
    print(f"  {g}")
    print(f"  每腿滑点模型：恒定乘积 x*y=k, fee={FEE_SWAP:.1%} | "
          f"MEV 抢跑概率={P_MEV_DEFAULT.get(args.chain):.0%}")
    print(f"  净收益公式: 毛价差 - {args.legs}×(fee+滑点) - gas - MEV折扣\n")
    print(f"{'':4} {'金额':>10} | {'毛收益':>8} | {'swap fee':>7} | {'滑点成本':>8} | "
          f"{'gas':>5} | {'MEV损失':>8} | {'净收益':>8}")
    print("-" * 88)
    for r in curve:
        print(fmt_row(r))

    # 关键读数：盈利区间 + 滑点爆炸点
    profitable = [r for r in curve if r["profitable"]]
    print("-" * 88)
    if profitable:
        best = max(profitable, key=lambda r: r["net"])
        print(f"✅ 盈利区间: ≤ ${profitable[-1]['amount']:,.0f}；"
              f"最佳单笔净收益 ${best['net']:.2f} @ ${best['amount']:,.0f}")
    else:
        print("❌ 该参数组合下无盈利点——滑点+费用+MEV 已吃光毛价差")
    # 滑点占比超过 50% 的资金量 = 滑点爆炸点
    for r in curve:
        if r["slippage_pct"] >= 0.5 * args.spread * 100:
            print(f"⚠ 滑点爆炸点: ${r['amount']:,.0f} 起，滑点成本已吃掉毛价差的一半以上")
            break
    # 规划读数：给定资金量需要多深的池
    need = SlippageModel.breakeven_pool_depth(amounts[-1], target_slippage=args.spread * 0.3)
    print(f"📐 规划读数: 若单笔做到 ${amounts[-1]:,.0f}，目标滑点 ≤ 毛价差 30%，"
          f"池深需 ≥ ${need:,.0f}")


if __name__ == "__main__":
    main()
