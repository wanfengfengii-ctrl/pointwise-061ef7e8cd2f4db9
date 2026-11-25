from archive.bootstrap import bootstrap_archive
from archive.evidence import store_evidence
from archive.membership import MembershipChange, apply_change, read_published_frontier, set_published_frontier
from receive.coverage import eligible_prefix
from receive.declarations import read_declarations
from receive.receipts import find_received_item
from source.revisions import NoticeRevision
from storage import write_transaction


def advance_archive(db, subscription):
    if bootstrap_archive(db, subscription) is None:
        return None
    with write_transaction(db):
        previous = read_published_frontier(db, subscription)
        target = eligible_prefix(db, subscription)
        if target <= previous:
            return previous

        for declaration in read_declarations(db, subscription):
            if declaration.hi <= previous:
                continue
            if declaration.lo >= target:
                break
            item, receipt = find_received_item(db, declaration.item_id)
            store_evidence(db, item, receipt, through=min(declaration.hi, target))
            for seq, item_id in declaration.entries:
                if not previous < seq <= target:
                    continue
                item, receipt = find_received_item(db, item_id)
                revision = NoticeRevision.from_dict(item.payload['revision'])
                change = MembershipChange(seq, revision.notice_id, item.payload['effect'], revision)
                apply_change(db, subscription, change)
                store_evidence(db, item, receipt)
        set_published_frontier(db, subscription, target)
    return target
