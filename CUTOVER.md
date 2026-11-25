# 在途换订与变更账

固定辖区的首批、投递、实收和确认约定见 README.md。本次仍使用原订阅号查账。来源受理时的最新源序是 C，换订本身不占用新的源序。首次受理不等待接收库。辖区名和集合沿用现有规范。

## 值班调用

| 调用 | 可观察结果 |
| --- | --- |
| `station.request_switch(source, subscription, switch_id, regions)` | 返回 `(C, boundary_items)`，其中 `boundary_items` 是承载这次换订依据的一项或多项正式发行件，按返回顺序即可投递。同号同参返回原 C 和原件。同号异参，或已办理换订的订阅再用另一号办理，均报 `ValueError`。 |
| `issue.changes.issue_through(source, subscription, through=None)` | 按原订阅续发。待发行区间跨过 C 时，可在一次调用中领取两侧的正式声明及变更。C 及以前按原范围，C 之后按新范围。已发原件仍可由 `issue.ledger.get_issued_item` 按发行号补领。 |
| `station.receive_and_publish(receiver, subscription, item)` | 用原订阅号实收并尝试推进公布。返回值的第一项仍是正式首执。每件仍可用 `issue.acknowledge.confirm_receipt(source, receiver, item.subscription, item.item_id, receipt)` 按原件核对。换订依据和新段件可在旧段欠件时先实收。 |
| `station.query_switch(receiver, switch_id)` | 本地识别换订后返回 `{'cutover': C, 'status': 'waiting'}` 或 `{'cutover': C, 'status': 'active'}`。尚未知该换订号时返回 `None`。换订边界已经公布即为 `active`，新段可按实收继续推进。 |
| `archive.history.query_archive(receiver, subscription)` | 按原订阅返回公布前沿 `frontier`、当前 `members`、有序 `changes` 及 `evidence`。换订前只公开齐备的旧段。边界公布时，退出与进入一起生效。之后继续按源序公布新段。 |

有序变更的普通行保留 `seq`、`notice_id`、`effect` 和原修订。边界行也有这三个字段，另以 `phase='switch'` 区分，并提供其正式依据的 `item_id` 和 `receipt_id`。同一 C 先列普通行，再列退出、进入，各组内按公告号排序。普通行仍可从 `evidence` 按 `kind='change'` 和源序找到原件及首执。两类出处都能通过 `receive.receipts.find_received_item` 在本地读回。共有公告在边界上没有变更行。未换订订阅继续使用原回归所覆盖的接口和结果。本站原有冻结核查单仍可复领，本次交付关注原订阅的变更账。

## 录制和运行

`data/cutover.json` 的每条录制从独立来源和档案开始。用 `data/registry.json` 登记 B、K、A，开通并实收 E 的东区首批。提交 `before` 中的公告动作。`publish_before_request` 为真时，先发行并公布这些变更。然后受理换订，再提交 `after` 并领取剩余正式件。`expected.changes` 每行依次为源序、公告号、变更效果、阶段，普通行为 `revision`。

EW-5 先投换订依据、新段声明和撤下件，再交旧段声明，把第 4 位 B 改文最后补到。等待期间应能按换订号查到 C=5 和 `waiting`，原订阅仍公布到 3。补齐后核对录制的全序和出处。用同一组发行件做顺序投递对照，并复投原件和换订请求。EW-4 已公布第 4 位 B 改文才受理，验明同一源序仍能补记边界的两行。两例最终名册都只剩 K，第 7 位或第 6 位的东区改文只跨过覆盖序位。

沿用项目的 `.pointwise-runtime.json`。该清单为 version 1、kind `cli`，`setup` 是准备命令列表，`verify` 和 `demo` 各是一条 argv。保持 `python3 -B check_project.py` 执行原回归，接入换订联合核验，核验不符须以失败退出。演示用录制展示待齐、补到后的变更账，以及关闭来源连接后重开档案、按原号读到的结果和实收出处。
