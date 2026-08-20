# 安全执行框架（strategies/security.md）

> 模块 5 · Day 2（08-17）｜复用 SafePay 经验：权限最小化、session key、白名单合约、限额
> 配套代码：`experiments/executor.py`（08-16 落地）
> 核心思想：**不是"信得过才执行"，而是"执行不了才安全"——把每一层攻击面都用最小权限封死，让最坏情况（私钥泄露/合约被黑/交易被篡改）也拿不走本金。**

---

## 一、威胁模型：执行链路里的攻击面

上链执行 = 私钥 + 签名 + 广播，攻击面按环节拆：

| 环节 | 风险 | 后果 |
|------|------|------|
| 私钥存储 | 明文/复用/冷热不分 | 资金全丢（最致命） |
| 授权（approve） | 无限额度 / 授权给错合约 | 被抽走全部余额 |
| 交易构造 | 恶意 `to`/`data`（钓鱼报价） | 签名了不该签的交易 |
| 广播 | 无审查即发（交易被抢跑/被替换） | 滑点扩大 / 交易失败 |
| 合约信任 | 依赖未审计桥合约 | 合约漏洞直接吞钱 |

**一句话：签名是一次性授权，approve 是持续性授权，私钥是终极授权——三者都要最小化。**

---

## 二、五道防线（SafePay 经验落地）

### 防线 1：权限最小化（approve 粒度）

- **只授权所需金额**，不设无限额度（executor.py `build_approve_tx` 已实现：`approve(spender, amount_raw)`，amount = 本次报价金额）。
- **approve 目标是 `quote.estimate.approvalAddress`**（LI.FI Diamond 合约），不是随便一个桥合约——昨天实测确认。
- native 币（ETH）无需 approve，天然少一层授权面。
- 进阶：用完可 `approve(0)` 回收授权；长期跑可考虑一次授权后 `allowance` 检查复用（见 executor `check_allowance`）。

### 防线 2：session key（Safe 系授权隔离）

- 理念：不给"主钱包/主私钥"永久权限，而是签发**临时、限定范围**的会话密钥。
- 落地到套利 Agent：
  - 单独派生一个**执行专用地址**（热钱包），只持有"够跑当次策略"的资金，主钱包/金库与其分离。
  - 类比 Safe Session Key：会话授权绑定 ①目标合约白名单 ②单笔/累计限额 ③有效期。到期自动失效。
  - 即使执行密钥泄露，攻击者能动的也只是执行钱包里的限额资金，主资产不动。

### 防线 3：白名单合约（只信审计过的）

- 可交互合约固定白名单：LI.FI Diamond（0x1231DEB6...）、USDC 等代币合约、路由中出现的桥合约。
- 广播前校验 `tx["to"] ∈ 白名单`，否则拒绝签名——防"报价里的 data 被钓鱼替换"。
- 校验 `tx["value"]` 和 `tx["data"]` 的 method selector 在允许集合内（approve / swap 类）。

### 防线 4：限额（单笔 + 累计 + 频率）

- 单笔上限：`max_amount_per_tx`（如 $1,000），超了拒签。
- 累计上限：`max_amount_per_day`（如 $3,000），用日账本记账，超了停止。
- 频率上限：同一策略间隔 `min_interval`（如 60s），防止异常循环/重复广播。
- 滑点保护：`slippage`（默认 0.5%）+ `minAmountOut` 校验，成交恶化直接失败不广播。

### 防线 5：签名隔离 + 可审计性

- **离线签名优先**：executor 的 dry-run 模式（真实报价 + 真实签名、不广播）用于验证链路，线上模式才广播——先验后发。
- 私钥环境变量注入（`PRIVATE_KEY`），不写进代码/日志/仓库；`.gitignore` 保护。
- 每次广播记录：txHash、金额、to、data 前 10 字节、时间——事后可审计。

---

## 三、落地检查清单（广播前逐项过）

```bash
[ ] tx.to ∈ 合约白名单
[ ] approve 金额 == 本次报价金额（非无限）
[ ] allowance(token, me, Diamond) >= 所需金额
[ ] 单笔金额 <= max_per_tx 且 今日累计 <= max_per_day
[ ] slippage / minAmountOut 已设置
[ ] 签名恢复地址 == 我的地址（eth_keys 验证）
[ ] 余额充足（native gas + token 本金）
[ ] 距离上次广播 >= min_interval
[ ] 日志落盘（txHash + 参数快照）
```

## 四、与已有产出的连接

- 08-12 `cost_model.py`：限额里的资金上限来自成本模型的净收益阈值。
- 08-15 `signal-rules.md`：触发阈值决定"值不值得执行"，安全框架决定"能不能安全执行"。
- 08-16 `executor.py`：本框架的执行载体（approve 最小化已内建）。
- SafePay 经验（Week 2 PoW）：session key / 白名单 / 限额的原始出处。

## 五、下一步（08-18 Paper Trading 前置）

- 给 executor 加 `--max-amount` / `--whitelist` / `--dry-run` 参数落地防线 3/4。
- 写一个 `check_security()` 函数：广播前跑检查清单，任一不通过即拒绝签名。
