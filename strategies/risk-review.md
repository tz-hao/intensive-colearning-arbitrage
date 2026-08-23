# 风险审查：四类失败场景

> 模块 5 · Day 8（08-23）｜与 `signal-rules.md`、`security.md`、`experiments/executor.py` 对照检查

## 结论

在没有广播前复核和可观测状态的情况下，策略不应从 ACTION 进入真实执行。今天的审查把“价差消失、交易失败、被夹、桥故障”拆成触发条件、可见信号和处置动作；默认动作是拒绝签名或停止后续操作，而不是赌下一步会恢复。

## 失败场景矩阵

| 场景 | 典型原因/表现 | 广播前或执行中检查 | 处置 |
|---|---|---|---|
| 价差消失 | 重新 `/quote` 后净收益跌破 $10，或窗口低于规则要求 | 执行前重新报价；复算 `toAmount - fromAmount`、滑点和 gas；检查时间闸门 | 不签名、不广播；原信号记为 EXPIRED，避免用旧报价成交 |
| 交易失败 | allowance 不足、余额不足、gas 不足、nonce 冲突，或链上 revert | allowance、原生币 gas、token 余额、nonce 和 `minAmountOut`；签名后保留原始参数 | 不自动重试同一交易；记录失败原因，确认 nonce/余额后才生成新 quote |
| 被夹/MEV | 热门池中交易前后价格异常，实际成交恶化，p_mev 超阈值 | 白名单 `to`、method selector、value/data；滑点上限；p_mev > 0.6 直接 IGNORE | 拒绝广播或等待窗口结束；不通过扩大滑点来“救单” |
| 桥故障 | `/status` 长时间 PENDING/NOT_FOUND，目标链未收到资产，或路由工具异常 | 保存源链 txHash；按 from/to chain 查询 `/status`；区分 NOT_FOUND、PENDING、FAILED | 停止重复发起；告警并人工核对源链交易和 LI.FI 状态，确认失败前不补发 |

## 与现有实现的差距

`executor.py` 已有真实 `/quote`、本地签名、广播和 `/status` 轮询，并默认 dry-run；`security.md` 已定义最小 approve、白名单、限额、滑点和日志清单。但当前仓库没有找到 `strategies/risk-review.md` 之外的专门风险脚本，也没有发现 `backtest_v2.py`，所以这些规则目前是审查清单，不是已经自动阻断的生产护栏。下一步应把 `check_security()` 和状态超时告警接入执行入口，并为每次失败保留可回放记录。
