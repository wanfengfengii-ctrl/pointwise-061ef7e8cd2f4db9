from issue.changes import project_revision
from issue.ledger import IssuedItem
from receive.coverage import read_coverage
from receive.declarations import (
    check_change_declaration, initialize_declarations, persist_declaration,
)
from receive.identity import DeliveryBindingError, DeliveryConflict, check_replay, validate_issued_item
from receive.receipts import initialize_receipts, read_received_items, received_baseline, store_receipt
from receive.switches import (
    initialize_received_switches, read_received_switch, read_received_switch_by_id,
    register_received_switch,
)
from source.revisions import NoticeRevision
from storage import write_transaction


def initialize_intake(db):
    initialize_receipts(db)
    initialize_declarations(db)
    initialize_received_switches(db)


def _check_projection(db, subscription, item):
    revision = NoticeRevision.from_dict(item.payload['revision'])
    switch = read_received_switch(db, subscription)
    if switch is not None:
        expected = switch.new_regions if item.payload['seq'] > switch.cutover else _initial_regions(db, subscription)
        if expected is not None and tuple(item.payload['regions']) != expected:
            raise DeliveryConflict('change regions contradict the subscription and its switch')
    if project_revision(revision, item.payload['regions']) != item.payload['effect']:
        raise DeliveryConflict('change effect contradicts the change regions')


def _initial_regions(db, subscription):
    initial = read_received_items(db, subscription, 'initial')
    return tuple(initial[0][0].payload['regions']) if initial else None


def _register_switch(db, item):
    payload = item.payload
    known = read_received_switch_by_id(db, payload['switch_id'])
    if known is not None and known.item_id != item.item_id:
        raise DeliveryConflict('switch number already binds a different item')
    if read_received_switch(db, item.subscription) is not None:
        raise DeliveryConflict('subscription already carries a switch')
    initial_regions = _initial_regions(db, item.subscription)
    if initial_regions is not None and initial_regions != tuple(payload['old_regions']):
        raise DeliveryConflict('switch old regions contradict the initial subscription')
    register_received_switch(db, payload['switch_id'], item.subscription, payload['cutover'],
                             item.item_id, tuple(payload['new_regions']))
    for change, _ in read_received_items(db, item.subscription, 'change'):
        _check_projection(db, item.subscription, change)


def _store_first_delivery(db, subscription, item):
    baseline = received_baseline(db, subscription)
    if baseline is not None and item.payload['baseline'] != baseline:
        raise DeliveryBindingError('delivery uses a different subscription baseline')

    if item.kind == 'initial':
        for change, _ in read_received_items(db, subscription, 'change'):
            _check_projection(db, subscription, change)
    elif item.kind == 'change':
        check_change_declaration(db, item)
        _check_projection(db, subscription, item)

    receipt = store_receipt(db, item)
    if item.kind == 'coverage':
        persist_declaration(db, item)
    elif item.kind == 'switch':
        _register_switch(db, item)
    return receipt


def accept_delivery(db, subscription, item):
    if not isinstance(item, IssuedItem) or item.subscription != subscription or not isinstance(item.item_id, str):
        raise DeliveryBindingError('item is bound to a different subscription or lacks an identity')
    with write_transaction(db):
        receipt = check_replay(db, item)
        if receipt is None:
            validate_issued_item(subscription, item)
            receipt = _store_first_delivery(db, subscription, item)
        status = read_coverage(db, subscription)
    return receipt, status
