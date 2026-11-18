# 深度登记 光伏电站限电事件与损失电量复算

规模：中型。使用者：新能源场站运营分析员。核心规则：仅用有效对照逆变器和相同辐照区间拟合基线；重叠限电事件先求并集再积分。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 遥测采信 | 执行并复核遥测采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 设备映射 | 执行并复核设备映射 | `POST /api/devices` | `app/modules/devices/rules.py` | `tests/test_devices.py` |
| M3 事件切片 | 执行并复核事件切片 | `POST /api/slicing` | `app/modules/slicing/rules.py` | `tests/test_slicing.py` |
| M4 基线拟合 | 执行并复核基线拟合 | `POST /api/baseline` | `app/modules/baseline/rules.py` | `tests/test_baseline.py` |
| M5 损失核算 | 执行并复核损失核算 | `POST /api/loss` | `app/modules/loss/rules.py` | `tests/test_loss.py` |
| M6 能量对账 | 执行并复核能量对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 基线证书 | 签发/失效/追证基线适用性 | `POST /api/certificate/issue` | `app/certificate.py`、`app/modules/certificate/rules.py` | `tests/test_certificate.py`、`tests/test_certificate_store.py` |
| M8 运行归档 | 执行并复核运行归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码中文状态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_source` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_source` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_segment_header` |
| TRAP-05 | 缺列记录 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bad_column` |
| TRAP-18 | 空态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_three_empty_types` |
| TRAP-07 | 遥测哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_sentinel_real` |
| TRAP-14 | 设备时刻冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_conflict_both_isolated` |
| TRAP-15 | 功率单位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_megawatt_conversion` |
| TRAP-06 | 全角千分位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_fullwidth_number` |
| TRAP-11 | UTC归一 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_time_equivalence` |
| TRAP-23 | 限电区间并集 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/slicing/rules.py` | `tests/test_slicing.py::test_overlapping_real` |
| TRAP-31 | 受阻区间被相邻区间补齐 | 损失失真、审计不可追 | 只签显式可用片段，受阻逐条列出并排除 | `app/certificate.py` | `tests/test_certificate.py::test_dirty_blocks_are_explicit_and_excluded` |
| TRAP-32 | 上游采信变化后旧证书仍被使用 | 用过期对照基线出证 | 证据指纹变化即失效，须重判 | `app/store.py` | `tests/test_certificate_store.py::test_upstream_admission_change_invalidates_then_reissues` |
| TRAP-33 | 同批重跑覆盖已确认内容 | 已签发证书/归档被改写 | 同证据幂等复用，不递增版本、不覆盖 | `app/store.py` | `tests/test_certificate_store.py::test_rerun_same_evidence_reuses_confirmed_content` |
| TRAP-34 | 签发半途失败留下残缺版本 | 旧证书丢失或出现半版 | 单事务写入，失败整体回滚 | `app/store.py` | `tests/test_certificate_store.py::test_failed_issue_rolls_back_previous_version` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
