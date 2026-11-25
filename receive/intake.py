from issue.changes import project_revision
from issue.ledger import IssuedItem
from receive.coverage import read_coverage
from receive.declarations import (
    check_change_declaration, initialize_declarations, persist_declaration,
)
from receive.identity import DeliveryBindingError, DeliveryConflict, check_replay, validate_issued_item
from receive.receipts import initialize_receipts, read_received_items, received_baseline, store_receipt
from source.revisions import NoticeRevision
from storage import write_transaction


def initialize_intake(db):
    initialize_receipts(db)
    initialize_declarations(db)


def _check_projection(item, regions):
    revision = NoticeRevision.from_dict(item.payload['revision'])
    if project_revision(revision, regions) != item.payload['effect']:
        raise DeliveryConflict('change effect contradicts the fixed subscription')


def _store_first_delivery(db, subscription, item):
    baseline = received_baseline(db, subscription)
    if baseline is not None and item.payload['baseline'] != baseline:
        raise DeliveryBindingError('delivery uses a different subscription baseline')

    if item.kind == 'initial':
        for change, _ in read_received_items(db, subscription, 'change'):
            _check_projection(change, item.payload['regions'])
    elif item.kind == 'change':
        check_change_declaration(db, item)
        initial = read_received_items(db, subscription, 'initial')
        if initial:
            _check_projection(item, initial[0][0].payload['regions'])

    receipt = store_receipt(db, item)
    if item.kind == 'coverage':
        persist_declaration(db, item)
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
