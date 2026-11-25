from issue.ledger import IssuedItem, delivery_id, read_issued_windows, store_issued_item
from issue.subscriptions import read_subscription
from source.journal import read_revisions, source_frontier
from source.regions import regions_intersect
from storage import require_sequence, write_transaction


def project_revision(revision, regions):
    was_member = revision.before is not None and regions_intersect(revision.before.regions, regions)
    is_member = revision.after is not None and regions_intersect(revision.after.regions, regions)
    if was_member:
        return 'update' if is_member else 'leave'
    return 'enter' if is_member else None


def issue_through(db, subscription, through=None):
    with write_transaction(db):
        fixed = read_subscription(db, subscription)
        windows = read_issued_windows(db, subscription)
        lower = windows[-1].payload['hi'] if windows else fixed.baseline
        frontier = source_frontier(db)
        upper = frontier if through is None else require_sequence(through)
        if not lower <= upper <= frontier:
            raise ValueError('issue target is outside the available continuation')
        if upper == lower:
            return ()

        issued = []
        expected_seq = lower + 1
        for revision in read_revisions(db, lower, upper):
            if revision.seq != expected_seq:
                raise ValueError(f'missing source revision {expected_seq}')
            expected_seq += 1
            effect = project_revision(revision, fixed.regions)
            if effect is None:
                continue
            item = IssuedItem(delivery_id(subscription, 'change', revision.seq), subscription, 'change', {
                'baseline': fixed.baseline,
                'seq': revision.seq,
                'effect': effect,
                'revision': revision.to_dict(),
            })
            store_issued_item(db, item)
            issued.append(item)
        if expected_seq != upper + 1:
            raise ValueError(f'missing source revision {expected_seq}')

        coverage = IssuedItem(delivery_id(subscription, 'coverage', upper), subscription, 'coverage', {
            'baseline': fixed.baseline,
            'lo': lower,
            'hi': upper,
            'entries': [[item.payload['seq'], item.item_id] for item in issued],
        })
        store_issued_item(db, coverage)
        issued.append(coverage)
    return tuple(issued)
