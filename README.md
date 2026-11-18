# 光伏电站限电事件与损失电量复算

给新能源场站运营分析员使用。仅用有效对照逆变器和相同辐照区间拟合基线；重叠限电事件先求并集再积分。

七个业务模块：遥测采信、设备映射、事件切片、基线拟合、损失核算、能量对账、运行归档。归档前新增基线适用性证书，按设备列出可用与受阻区间。

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

访问 http://localhost:18244 。启动直接载入现场脏批。切换干净样例和旧编码样例可对照核验。完整对照批INV-A重叠事件取并集后30分钟，基线100kW、实际60kW，损失20kWh；无合格对照或遥测冲突必须保持未定，不能填0。

各模块页面对应 `/<模块英文名>`，各模块执行入口为 `POST /api/<模块英文名>`，不传参即可独立演示。完整运行 `POST /api/runs` 支持 `dataset`（dirty/clean/legacy）、`csv_text` 与 `overrides` 参数。批次历史 `GET /api/runs`，详情 `GET /api/runs/{run_id}`。

同输入与规则版本生成相同批号，只保留一条批次。复核入口 `POST /api/runs/{run_id}/review` 提交当前 `revision`，陈旧提交409。归档包只有一个下载入口 `GET /api/export/{run_id}`，含证据和运行摘要。原始隔离在页面同屏显示。

SQLite使用命名卷，容器重启后保留历史。要重建演示可在Compose中改用新的卷名称，保留旧卷。

源码在 `app/`，样例在 `sample_data/`，测试在 `tests/`；定义见 `docs/DATA-DICTIONARY.md`，模块和边界测试对应关系见 `docs/DEPTH.md`。输入上限2MB，属于离线复核工具。

采信页支持上传或粘贴CSV。完整源字节与参数随批次持久化，跨页通过run_id读取同一结果。`POST /api/runs/{run_id}/rerun` 读取该批不可变输入快照重新计算，规则未变时批号不变。切换另一示例则创建或复用另一批次。

## 基线适用性证书

现场要尽快签发，审计只认有同区、同时刻健康对照的区间。两者的折中是“限定签发”：只覆盖显式可用区间，受阻区间逐条列出并附缺少的对照条件，绝不用相邻区间补齐，因此损失不会因补齐而失真。

- `GET /certificate?run_id=...` 查看每台设备的可用/受阻区间、证据指纹和证书版本历史。
- `POST /api/certificate/issue`，参数 `run_id`、`mode`（`bounded` 限定 / `formal` 正式）、`note`。正式模式要求证据闭合。
- `GET /api/certificate/runs/{run_id}` 当前有效证书与历史版本。
- `POST /api/runs/{run_id}/seal` 正式归档：需当前有效且闭合的正式证书，且证据指纹一致；同批只写一次。
- `GET /api/archive/{run_id}` 取正式归档内容；`GET /api/export/{run_id}` 已正式归档时导出含 `certificate.json` 的完整包。

证据指纹覆盖规则版本、采信与隔离记录、设备映射、规则参数。`POST /api/runs` 导入新采信结果时推进作用域指纹，旧证书立即失效并记录失效原因；重跑同一快照不推进。签发为单事务，写入失败回滚并保留旧证书；同一证据同模式重签幂等复用，不覆盖已确认内容。
