\# Learning Cards（学习卡）

记录每次告警：发生了什么 → 可能原因 → 我该查什么 → 概念解释




---
## [DATA_FLAT] AAPL

- time_utc: `2025-12-15T05:14:15+00:00`
- time_local(Eastern Standard Time): `2025-12-15T00:14:15-05:00`

**Facts**
- AAPL 价格连续 10 次更新未变化
- price=278.279999
- last_ts=2025-12-15T05:13:56+00:00

**Hypotheses**
- 周末/盘后正常冻结
- 数据源只给昨收/最后成交
- 你拿到的是缓存价

**Checks**
- 看 SPY 是否也冻结
- 检查是否周末/盘后
- 后续可在 quotes.py 增加 source 字段区分数据来源

**Concepts**
- DATA_FLAT：文件在更新，但数值不变（可能市场没动，也可能数据源不刷新）。

---
## [DATA_FLAT] MSFT

- time_utc: `2025-12-15T05:14:15+00:00`
- time_local(Eastern Standard Time): `2025-12-15T00:14:15-05:00`

**Facts**
- MSFT 价格连续 10 次更新未变化
- price=478.529999
- last_ts=2025-12-15T05:13:56+00:00

**Hypotheses**
- 周末/盘后正常冻结
- 数据源只给昨收/最后成交
- 你拿到的是缓存价

**Checks**
- 看 SPY 是否也冻结
- 检查是否周末/盘后
- 后续可在 quotes.py 增加 source 字段区分数据来源

**Concepts**
- DATA_FLAT：文件在更新，但数值不变（可能市场没动，也可能数据源不刷新）。

---
## [DATA_FLAT] NVDA

- time_utc: `2025-12-15T05:14:15+00:00`
- time_local(Eastern Standard Time): `2025-12-15T00:14:15-05:00`

**Facts**
- NVDA 价格连续 10 次更新未变化
- price=175.020004
- last_ts=2025-12-15T05:13:56+00:00

**Hypotheses**
- 周末/盘后正常冻结
- 数据源只给昨收/最后成交
- 你拿到的是缓存价

**Checks**
- 看 SPY 是否也冻结
- 检查是否周末/盘后
- 后续可在 quotes.py 增加 source 字段区分数据来源

**Concepts**
- DATA_FLAT：文件在更新，但数值不变（可能市场没动，也可能数据源不刷新）。

---
## [DATA_FLAT] SPY

- time_utc: `2025-12-15T05:14:15+00:00`
- time_local(Eastern Standard Time): `2025-12-15T00:14:15-05:00`

**Facts**
- SPY 价格连续 10 次更新未变化
- price=681.760010
- last_ts=2025-12-15T05:13:56+00:00

**Hypotheses**
- 周末/盘后正常冻结
- 数据源只给昨收/最后成交
- 你拿到的是缓存价

**Checks**
- 看 SPY 是否也冻结
- 检查是否周末/盘后
- 后续可在 quotes.py 增加 source 字段区分数据来源

**Concepts**
- DATA_FLAT：文件在更新，但数值不变（可能市场没动，也可能数据源不刷新）。
