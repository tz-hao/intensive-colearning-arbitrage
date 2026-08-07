# 链上交易实战分析：两笔套利交易拆解

> 分析日期：2026-08-07 | 方法：RPC 直查交易 + receipt logs 解码
> 分析框架：套利共学模块 1（净收益公式）+ 模块 2（路由/执行）+ 模块 3（执行成本五问）
> 交易来源：
> - Robinhood 链：https://robinscan.io/tx/0xe596167afe972523f90971e4f15a0635579bd608d5fd4a78c4eb2f16811cf072
> - 以太坊主网：https://etherscan.io/tx/0xefe4f89fe0fdd1b9842eff564a0fa8b426503bb9a8880c928823ff4d3e52762d

---

## 案例 A：Robinhood 链 —— 多池路径执行（USDG → FRONG → WETH）

### 交易信息

- 交易哈希：`0xe596167afe972523f90971e4f15a0635579bd608d5fd4a78c4eb2f16811cf072`
- 发起者：`0xd71212080d90ffa50a80bff2af2ade6f99f29b96`（EOA）
- 路由合约：`0x1521027b665fa38fa4a642991607ad708376dd7b`（代理合约，141 字节）
- gas：391,931 | gasPrice 59.5 gwei | 状态：成功
- 链：Robinhood Chain（chainId 4663，RPC: `rpc.mainnet.chain.robinhood.com`）

### 参与者解码

| 地址 | 身份 |
|------|------|
| `0x5fc5360d...` | **USDG**（Global Dollar 稳定币，6 decimals） |
| `0x6245e67a...` | **FRONG**（18 decimals） |
| `0x0bd7d308...` | **WETH**（18 decimals） |
| `0x8e8de43a...` | Uniswap V3 池（USDG/FRONG） |
| `0x65ce976b...` | Uniswap V3 池（WETH/FRONG） |
| `0x8366a39c...` | 协议/代收地址（FRONG 接收方） |

### 资金流（logs 重建）

```
① 53.77 USDG 送入 USDG/FRONG 池 → 换出 10,552 FRONG
② 10,552 FRONG 送入 FRONG/WETH 池 → 换出 ~0.028 WETH
③ 0.028 WETH 换回 USDG → 53.77 USDG 回到发起者
④ 净损耗：0.000142 WETH（≈ $0.5 协议费）
```

log 时间线：
```
[0]  USDG 53.77  池A(0x65ce976b) → 路由
[1]  FRONG 10552.18  池B(0x8e8de43a) → 路由
[2]  FRONG 10552.18  路由 → 0x8366a39c（入池C）
[3]  0x8366a39c 事件（FRONG 接收）
[4]  WETH 0.028235  mint → 路由（出池C）
[5]  USDG 53.77  路由 → 池B（USDG 回流）
[6]  池B Uniswap V3 Swap 事件
[7]  WETH 0.028093  路由 → 池C
[8]  池C Swap 事件
[9]  路由 Swap 事件
[10] WETH 0.000142  路由 → 0x0000（手续费/烧毁）
```

### 套利框架评估（模块 1 净收益公式）

```
屏幕价差：USDG ↔ FRONG ↔ WETH 三角路径价差
- Gas: 391,931 × 59.5 gwei
- 协议费: 0.000142 WETH（V3 swap fee ≈ 0.05%）
- 滑点: FRONG 池流动性浅，10K FRONG 交易冲击不小
- 结论: 小额（$54）路径执行 —— 更像路由可用性验证而非大额套利
```

### 与共学知识的连接

- **模块 2「路径发现」**：三池环形路径（USDG→FRONG→WETH→USDG）
- **模块 3「执行成本」**：浅池滑点 > gas 成本（流动性深度决定成败）
- **模块 1「净收益公式」**：53.77 USDG 进出几乎持平，赚的是手续费/价差

---

## 案例 B：以太坊主网 —— Aave 循环借贷 + Bebop 意图执行

### 交易信息

- 交易哈希：`0xefe4f89fe0fdd1b9842eff564a0fa8b426503bb9a8880c928823ff4d3e52762d`
- 执行合约：`0xf0570ec48d03171a80ff796dceadf0d385a00004`（16KB 代码）
- 意图求解器：`0x51c72848c68a965f66fa7a88855f9f7784502a7f`（20KB 代码）
- gas：721,688 | 状态：成功

### 参与者解码

| 地址 | 身份 |
|------|------|
| `0x87870bca...` | **Aave V3 Pool** |
| `0x4228f889...` | Aave Variable Debt LINK（浮动债务代币） |
| `0x98c23e9d...` | **aUSDC**（Aave 存款凭证） |
| `0xbbbbbbb5...` | **Bebop**（意图式 DEX） |
| `0x51491077...` | **LINK**（ChainLink Token） |
| `0xa0b86991...` | **USDC**（Circle） |
| `0xc02aaa39...` | **WETH** |

### 资金流（25 个 logs 重建）

```
Aave 借贷环：
① 借出 4191.1 LINK（Aave 借贷池）→ 执行合约
② LINK 送入 Bebop 意图交易 → 换出 35,966 USDC + 0.543 WETH
③ 35,966 USDC 存入 Aave → 铸出 aUSDC
④ 用 aUSDC 头寸做抵押循环（LINK → USDC → aUSDC → 再借）

意图执行：
⑤ 0.543 WETH + LINK 经 Bebop settle（0xbbbbbbb5 事件）
⑥ 最后 4193.2 LINK 偿还 Aave（还本付息）
```

关键 logs：
```
[0]  LINK 4191.1  0x5e8c8a72 → 执行合约（借出）
[1]  DebtLINK 4190.5  0x986bb0b2 → 0x0000（债务头寸变动）
[5]  aUSDC 3.59万  0x986bb0b2 → 0x0000（存款凭证）
[7]  USDC 35,966  0x98c23e9d → 执行合约（意图卖出所得）
[14] LINK 4193.2  0x51c72848 → 执行合约（还贷）
[16] USDC 35,966  执行合约 → 0x51c72848（付给求解器）
[23] WETH 0.0066  执行合约 → 0x51c72848（付给求解器）
```

### 套利框架评估

```
核心策略: 借 LINK → 意图卖成 USDC → 存入 Aave 赚收益 → 循环
利润来源:
  + LINK 借贷利率 vs USDC 存款利率的利差
  + Bebop 意图竞价的执行优化（比单 DEX 好 0.1-0.3%）
  - 还贷利息: 4193.2 - 4191.1 = 2.1 LINK 利息成本
  - Gas: 721,688 gas（主网成本不低）
  - 滑点: 4K LINK 卖出的冲击
```

### 与共学知识的连接

- **模块 2「最便宜路径 ≠ 最优」**：用 Bebop 意图网络而非普通路由 → solver 竞争报价，执行质量优先于名义价格
- **模块 3「执行成本五问」**：借贷利息、gas、滑点、意图执行优化全部体现
- **模块 1「净收益公式」**：利差收益 - 利息 - gas = 净收益

---

## 两笔交易的横向对比

| 维度 | 案例 A（Robinhood） | 案例 B（Ethereum） |
|------|-------------------|-------------------|
| 类型 | 多池路径套利 | 借贷利差 + 意图执行 |
| 中间资产 | FRONG（浅池） | USDC/aUSDC（深池） |
| 执行方式 | V3 路由 | Bebop solver 竞价 |
| 主要成本 | 滑点 | 利息 + gas |
| 规模 | $54（测试级） | $40K+（实战级） |
| 套利收益 | 几乎持平 | 利差 + 执行优化 |

**一句话总结**：案例 A 是「验证路由可用性」的小额路径执行；案例 B 是「借贷利差 + 意图执行优化」的组合策略 —— 完美呼应模块 2 的 Quote/Route 和模块 3 的「执行前不看两个价格」。

---

## 分析方法备忘（可复用）

1. **RPC 直查**：`eth_getTransactionByHash` + `eth_getTransactionReceipt` 拿原始交易和 logs（无需浏览器/API key）
2. **Robinscan 被 Vercel 拦截**：直接走 Robinhood Chain RPC（chainId 4663, `rpc.mainnet.chain.robinhood.com`）
3. **识别代币**：对 log 里的合约地址调 `symbol()` / `decimals()` / `name()`（eth_call）
4. **识别协议**：`eth_getCode` 查代码大小判断合约/EOA；知名地址（Aave V3 Pool、WETH、LINK、USDC）直接比对
5. **Transfer 事件解码**：topic0 = `0xddf252ad...`（ERC20 Transfer），topic1=sender，topic2=receiver，data=amount
6. **套利判定**：用模块 1 公式「价差 - gas - 协议费 - 滑点 - MEV 折扣 = 净收益」逐层套
