from dataclasses import dataclass

from archive.history import UnknownArchiveSubscription
from archive.membership import read_published_frontier
from receive.coverage import CoverageStatus, read_coverage
from receive.receipts import received_baseline
from storage import read_view, require_sequence


@dataclass(frozen=True)
class ReviewReadiness:
    status: str
    coverage: CoverageStatus
    awaiting_initial: bool
    unpublished: tuple[int, int] | None


def assess_review(db, subscription, target):
    require_sequence(target)
    with read_view(db):
        if received_baseline(db, subscription) is None:
            raise UnknownArchiveSubscription(subscription)
        coverage = read_coverage(db, subscription, target)
        frontier = read_published_frontier(db, subscription)
        if frontier is not None and frontier >= target:
            return ReviewReadiness('ready', coverage, False, None)
        unpublished = None
        if frontier is not None and frontier < coverage.eligible:
            unpublished = (frontier + 1, coverage.eligible)
        return ReviewReadiness('waiting', coverage, frontier is None, unpublished)
