from archive.bootstrap import bootstrap_archive
from archive.evidence import store_evidence
from archive.membership import (
    MembershipChange, apply_boundary, apply_change, read_archive_switch,
    read_published_frontier, record_archive_switch, set_published_frontier,
)
from issue.switches import switch_images
from receive.coverage import eligible_prefix
from receive.declarations import read_declarations
from receive.receipts import find_received_item, read_received_items
from receive.switches import read_received_switch
from source.revisions import NoticeRevision
from storage import write_transaction


def _publish_boundary(db, subscription, switch):
    item, receipt = find_received_item(db, switch.item_id)
    leaves, enters = switch_images(item.payload)
    apply_boundary(db, subscription, switch.cutover, leaves, enters, item.item_id, receipt.receipt_id)
    record_archive_switch(db, subscription, switch.switch_id, switch.cutover,
                          switch.new_regions, item.item_id, receipt.receipt_id)
    store_evidence(db, item, receipt, through=switch.cutover)


def _foreign_change_barrier(db, subscription, previous, initial_regions):
    for change, _ in read_received_items(db, subscription, 'change', after=previous):
        if tuple(change.payload['regions']) != tuple(initial_regions):
            return change.payload['seq']
    return None


def advance_archive(db, subscription):
    if bootstrap_archive(db, subscription) is None:
        return None
    with write_transaction(db):
        previous = read_published_frontier(db, subscription)
        target = eligible_prefix(db, subscription)
        switch = read_received_switch(db, subscription)
        archived = read_archive_switch(db, subscription)
        boundary = None
        if switch is not None and archived is None and switch.cutover <= target:
            boundary = switch
        elif switch is None:
            initial = read_received_items(db, subscription, 'initial')
            if initial:
                barrier = _foreign_change_barrier(db, subscription, previous, initial[0][0].payload['regions'])
                if barrier is not None:
                    target = min(target, barrier - 1)
        if target <= previous and boundary is None:
            return previous
        target = max(target, previous)

        changes = []
        for declaration in read_declarations(db, subscription):
            if declaration.hi <= previous:
                continue
            if declaration.lo >= target:
                break
            item, receipt = find_received_item(db, declaration.item_id)
            store_evidence(db, item, receipt, through=min(declaration.hi, target))
            changes.extend((seq, item_id) for seq, item_id in declaration.entries
                           if previous < seq <= target)
        changes.sort()

        applied = False
        for seq, item_id in changes:
            if boundary is not None and not applied and seq > boundary.cutover:
                _publish_boundary(db, subscription, boundary)
                applied = True
            item, receipt = find_received_item(db, item_id)
            revision = NoticeRevision.from_dict(item.payload['revision'])
            apply_change(db, subscription, MembershipChange(seq, revision.notice_id,
                                                           item.payload['effect'], revision))
            store_evidence(db, item, receipt)
        if boundary is not None and not applied:
            _publish_boundary(db, subscription, boundary)
        set_published_frontier(db, subscription, target)
    return target
