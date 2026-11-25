from dataclasses import dataclass

from receive.declarations import read_declarations
from receive.receipts import read_received_items, received_baseline, received_frontier
from storage import read_view, require_sequence


@dataclass(frozen=True)
class CoverageStatus:
    baseline: int
    target: int
    eligible: int
    missing: tuple[tuple[int, str], ...]
    uncovered: tuple[tuple[int, int], ...]


def read_coverage(db, subscription, through=None):
    with read_view(db):
        baseline = received_baseline(db, subscription)
        if baseline is None:
            raise LookupError(subscription)
        target = received_frontier(db, subscription) if through is None else require_sequence(through)
        if target < baseline:
            raise ValueError('target precedes subscription baseline')

        received = {item.item_id for item, _ in read_received_items(
            db, subscription, 'change', after=baseline, through=target,
        )}
        missing = []
        uncovered = []
        cursor = baseline + 1
        for window in read_declarations(db, subscription):
            if window.lo >= target:
                break
            if cursor <= window.lo:
                uncovered.append((cursor, window.lo))
            missing.extend((seq, item_id) for seq, item_id in window.entries
                           if seq <= target and item_id not in received)
            cursor = min(target, window.hi) + 1
        if cursor <= target:
            uncovered.append((cursor, target))

        first_unavailable = min(
            missing[0][0] if missing else target + 1,
            uncovered[0][0] if uncovered else target + 1,
        )
        return CoverageStatus(baseline, target, first_unavailable - 1, tuple(missing), tuple(uncovered))


def eligible_prefix(db, subscription):
    return read_coverage(db, subscription).eligible
