# 市政工程公告采集站

发布员登记公告、改文、改辖区或撤下。采集员领取固定辖区的正式资料，按发行号补交并交回首执。值班编辑以已公布名册核账，保存指定源序的交班结论。来源发行库与接收档案库各自持久化。接收库保存已公布历史与所引用的来件，来源停用后仍可复核。

## 运行

需要 Python 3.11 标准库及其内置 SQLite 模块。在项目目录执行正常回归，标准入口由运行平台提供。

```sh
python3 -B check_project.py
```

直接运行同一套 unittest 和演示的命令如下。演示使用临时目录内的两份真实数据库，输出首批、欠件、归档历史、确认进度和断源复领结果。

```sh
python3 -B -m unittest discover -s tests -t .
python3 -B -m station demo
```

`data/registry.json` 来自模拟登记簿。`data/shift.json` 给出值班修订和投递顺序。东区首批在源序 3 包含 B 和 K。4 移出 B、5 移入 A、6 改文 K、7 登记未归区的 D。声明与第 6 件先到时仍缺 4、5。补齐后应公布退出、进入和更新各一次，跨过 7 时名册有 A、K 两件。随后 K 再改文、A 撤下，接班仍可读回目标 7 的两件核查单。

## 公开 Python 接口

通过 `station.open_source(path)` 和 `station.open_archive(path)` 打开各自数据库。调用者关闭连接，可使用 `contextlib.closing`。初始化可重复执行。下列写入口自行持有事务，调用前连接应处于事务外。

| 角色 | 正常入口 | 结果 |
| --- | --- | --- |
| 发布员 | `source.notices.submit_notice(db, notice_id, action, *, body=None, regions=None)` | 返回含源序和前后像的修订 |
| 发行员 | `issue.subscriptions.open_subscription(db, subscription, regions)` | 冻结或复领固定范围首批 |
| 发行员 | `issue.changes.issue_through(db, subscription, through=None)` | 返回新发行的变更件和覆盖声明；默认查至当前源序；相同前沿返回空元组 |
| 采集员 | `issue.ledger.get_issued_item(db, item_id)` | 补领原发行件 |
| 采集员 | `receive.intake.accept_delivery(db, subscription, item)` | 返回正式首执和接收覆盖状态 |
| 采集员 | `issue.acknowledge.confirm_receipt(source, receiver, subscription, item_id, receipt)` | 核对提交的首执并返回逐件与连续确认进度 |
| 档案员 | `archive.advance.advance_archive(db, subscription)` | 按实收推进档案，返回公布前沿或待首批时的 `None` |
| 值班编辑 | `archive.history.query_archive(db, subscription)` | 一致视图中的基线、前沿、名册、历史和来源 |
| 值班编辑 | `archive.history.read_published_history(db, subscription, through)` | 已公布前缀内的指定目标历史和裁剪后的来源 |
| 值班编辑 | `review.reports.request_report(db, report_id, subscription, target)` 与 `review.reopen.reopen_report(db, report_id)` | 返回 `waiting` 待齐或 `complete` 冻结核查单 |

`station.receive_and_publish` 将实收和归档依次执行。若第二步失败，已提交的来件仍可用，重试归档或重投原件即可。正式确认使用事务外的接收连接。`receive.coverage.read_coverage` 可查询收齐前缀、欠件和未覆盖区间。`receive.receipts.find_received_item` 可按归档引用中的发行号找到本地原件与首执。低层持久化函数由上述业务入口在各自事务中使用。

## 数据与错误语义

- 公告动作 `create` 需要正文及辖区，`edit` 只接收正文，`move` 只接收辖区，`withdraw` 保留前像与原修订号。正文保存原字符串。公告号、订阅号和辖区名为精确的非空 Unicode 名称。辖区集合去重排序，空集合有效。未知或已撤下的原件拒绝后续变更，重复登记以及非法动作报业务错误，失败不占源序。
- 固定订阅的首批成员与基线来自同一视图。范围同号同参可复领，异参报 `SubscriptionConflict`。发行通过前后像的区交集计算进入、退出、更新与无关。源序为连续整数。正式覆盖 `(L,H]` 中明确列出应交的源序和发行号，其余是无关序位。欠件与未覆盖区间分别列出，未覆盖区间的两端均包含。
- 来件来自本站固定录制或受信任的发行进程。接收检查规范格式、订阅绑定、已知覆盖与原文重投。内容摘要绑定一次实收与原发行件。错绑或异文分别报 `DeliveryBindingError` 或 `DeliveryConflict`。接收提交失败时无可核对的正式首执。确认以接收库已提交记录和原发行记录为准。
- 档案只公布连续齐备前缀，范围内变更、名册、证据和前沿同时提交。空范围与无关区间也可推进。未知订阅报 `UnknownArchiveSubscription`，已收其他件但未建首批则返回 `awaiting_initial`。当前名册可在第一份首批之前为空。
- 核查按基线件数加累计进入、减累计退出，更新单计。只计目标内历史，目标等于基线时所有变更数为零。早于基线的目标拒绝。核查号在待齐阶段即绑定订阅与目标，异参报 `ReportConflict`，未知号报 `UnknownReport`。签出冻结的计数与依据在后续发布及重开后保持原值。
