# 新人链上数据观察入门：看价格、看池子、看协议（真实演示）

> 2026-08-10 | 目标：教会新人**自己动手看链上数据**，不靠别人的结论
> 全部数据来自免费 API 真实调用（CoinGecko / DefiLlama / GeckoTerminal / LI.FI，均无 key）
> 演示脚本：/tmp/papers/chain_watch.py（已跑通，可复制复用）

---

## 零、为什么先学"看数据"？

论文结论是别人的，只有自己会看数据，才能：
1. **验证假设**（08-11 策略假设池的每个假设都要用数据检验）
2. **发现机会**（机会长在数据里，不长在文章里）
3. **知道风险**（池子深浅、协议规模，一眼能看穿）

条件有限的新人不需要 node、不需要付费 API——**下面 4 个免费接口就够了**。

---

## 一、看价格：CoinGecko `/simple/price`（免费、无 key）

```bash
curl "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,usd-coin&vs_currencies=usd&include_24hr_change=true"
```

真实输出（2026-08-10 实测）：

| 资产 | 价格 | 24h |
|------|------|-----|
| BTC | $64,872 | -0.03% |
| ETH | $1,912 | -0.22% |
| USDC | $1.00 | +0.00% |

**新人看点**：
- 价格绝对值不重要，**24h 变化**才说明市场状态（risk-on/off）
- **稳定币必须钉住 $1**：USDC/USDT 脱锚（>0.5%）是系统性信号，脱锚本身就是套利机会（08-05 模块 1 讲过）
- 价格数据是套利净收益公式的第一输入：价差 = 两市场价格之差

---

## 二、看池子：GeckoTerminal `/networks/eth/trending_pools`（免费、无 key）

```bash
curl "https://api.geckoterminal.com/api/v2/networks/eth/trending_pools"
```

真实输出（2026-08-10 实测，截取 6 个池）：

| 池子 | 流动性(万$) | 24h量(万$) | 24h交易 | 换手率/天 |
|------|-----------|-----------|--------|----------|
| WETH/USDC 0.05% | 9,557.6 | 7,081.1 | 3,364 | 74% |
| FWA/ETH | 111.1 | 277.3 | 4,550 | 250% |
| ASTEROID/WETH | 177.6 | 61.6 | 678 | 35% |
| EPIC/ETH 0.3% | 9.4 | 28.6 | 2,178 | 304% |
| V4/ETH | 14.9 | 52.6 | 1,582 | 353% |
| HEX/WETH | 52.5 | 18.2 | 764 | 35% |

**新人看点（这是最值钱的一页）**：
1. **流动性深度**（储备 $）决定滑点：WETH/USDC 深池 $9557 万，FWA 浅池 $111 万——同一笔 $1 万交易，深池滑点可忽略，浅池直接砸出价差
2. **换手率 = 24h 量 / 流动性**：FWA 250%/天 vs WETH/USDC 74%/天——浅池高换手 = 投机盘、价格易被推动 = **价差机会多但进出危险**；深池低换手 = 稳定、适合大资金
3. **交易笔数 vs 金额**：FWA 4550 笔才 $277 万（平均 $610/笔），WETH/USDC 3364 笔 $7081 万（平均 $2.1 万/笔）——**一眼分辨散户池 vs 机构池**
4. 对套利的意义：**找机会去浅池（价差大），执行要算清滑点**——这正是 08-12 成本模型要落地的

**API 结构陷阱（已实测踩过）**：
- `reserve_in_usd` 是**字符串**不是数字
- `volume_usd` 是 dict：`{'m5':..,'h24':..}`，取 `['h24']`
- `transactions.h24` 是 dict：`{'buys':N,'sells':M}`，交易数 = buys+sells

---

## 三、看协议：DefiLlama `/protocols`（免费、无 key）

```bash
curl "https://api.llama.fi/protocols"
```

真实输出（2026-08-10 实测，TVL 前 8）：

| 协议 | 链数 | TVL(亿美元) | 24h |
|------|------|------------|-----|
| Binance CEX | 32 | 1,395.1 | -0.16% |
| OKX | 19 | 212.4 | +0.04% |
| Lido | 5 | 181.4 | +0.14% |
| Bitfinex | 20 | 169.6 | +0.02% |
| **Aave V3** | 22 | **142.6** | -0.04% |
| Bybit | 36 | 131.1 | +0.08% |
| Robinhood | 4 | 117.7 | +0.10% |
| SSV Network | 1 | 94.1 | +0.84% |

**新人看点**：
1. **TVL = 资金集中地**：借贷协议里 Aave V3 是龙头（$142.6 亿）——清算套利的机会密度和协议 TVL 成正比（池子大→借款人仓位多→清算机会多）
2. 论文 2（Hawkes）研究的 Aave V3 / Compound V3 / Morpho 都在这里可以查到规模——**用数据验证论文的选样合理性**
3. CEX（Binance/OKX）TVL 是 DEX 的 6-10 倍 → 解释了为什么 CEX-DEX 价差长期存在（流动性不对称）

---

## 四、看链：LI.FI `/chains`（免费、无 key，复用共学基建）

```bash
curl "https://li.quest/v1/chains"
```

真实输出（2026-08-10 实测，按 popularity 前 6）：

| 链 | id | native |
|----|----|--------|
| Ethereum | 1 | ETH |
| Arbitrum | 42161 | ETH |
| Robinhood Chain | 4663 | ETH |
| Base | 8453 | ETH |
| Hyperliquid | 1337 | USDC |
| HyperEVM | 999 | HYPE |

**新人看点**：
- **链 id 就是 API 参数**：我们 08-09 的 `lifi_routes.py` 用的 42161/1/8453 就来自这里
- 有趣细节：Hyperliquid 的 native 是 USDC（合约链的独特设计）——链数据本身就有信息量
- 跨链套利的前提是知道哪些链支持哪些资产 → `/tokens` 接口可以查（下一步练习）

---

## 五、新人练习路径（每天 10 分钟）

| 天 | 练习 | 自问 |
|----|------|------|
| Day 1 | 跑 chain_watch.py，看懂 4 类输出 | 哪个池子换手率最高？为什么？ |
| Day 2 | 用 GeckoTerminal 换网络：arbitrum / base / polygon | 同一种币在不同链的深度差多少？ |
| Day 3 | 用 DefiLlama 查 Compound V3 / Morpho 的 TVL | 论文 2 的三个协议规模排序？ |
| Day 4 | 用 LI.FI `/tokens` 查 USDC 在哪些链 | 稳定币在哪些链有流动性？ |
| Day 5 | 把某个池子的 24h 量 / 流动性做成换手率，和另一个池子比 | 深池 vs 浅池，哪个更适合 $1 万套利？ |

**原理性收获**（比背数据重要）：
- 数据源三原则：**免费、无 key、可脚本化**——新手不需要付费工具
- 指标三件套：**深度（滑点风险）、换手（机会密度）、规模（资金流向）**
- 任何论文结论，都能用这 4 个接口自己验证——**这就是"条件有限"的破局方式**

---

## 附：脚本位置与复用

- 演示脚本 `/tmp/papers/chain_watch.py`（代理 127.0.0.1:7897，与 lifi_routes.py 同款）
- 可扩展方向：定时跑 + 记录历史 → 就是 08-19 监控告警的前身
- 所有 API 无 key 免费；GeckoTerminal 免费版有 rate limit（~30 req/min），够学习用
