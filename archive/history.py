from dataclasses import dataclass

from archive.evidence import EvidenceReference, read_evidence
from archive.membership import (
    ArchiveBaseline, MembershipChange, read_baseline, read_change_rows, read_members,
    read_published_frontier,
)
from receive.receipts import received_baseline
from source.revisions import NoticeImage
from storage import read_view, require_sequence


class UnknownArchiveSubscription(LookupError):
    pass


@dataclass(frozen=True)
class ArchiveView:
    subscription: str
    status: str
    baseline: int
    frontier: int | None
    members: tuple[NoticeImage, ...]
    changes: tuple[MembershipChange, ...]
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class PublishedHistory:
    baseline: ArchiveBaseline
    target: int
    changes: tuple[MembershipChange, ...]
    evidence: tuple[EvidenceReference, ...]


def query_archive(db, subscription):
    with read_view(db):
        sequence = received_baseline(db, subscription)
        if sequence is None:
            raise UnknownArchiveSubscription(subscription)
        frontier = read_published_frontier(db, subscription)
        if frontier is None:
            return ArchiveView(subscription, 'awaiting_initial', sequence, None, (), (), ())
        return ArchiveView(
            subscription, 'published', sequence, frontier, read_members(db, subscription),
            read_change_rows(db, subscription, frontier), read_evidence(db, subscription, frontier),
        )


def read_published_history(db, subscription, through):
    require_sequence(through)
    with read_view(db):
        if received_baseline(db, subscription) is None:
            raise UnknownArchiveSubscription(subscription)
        baseline = read_baseline(db, subscription)
        frontier = read_published_frontier(db, subscription)
        if baseline is None or not baseline.seq <= through <= frontier:
            raise ValueError('target is outside published history')
        return PublishedHistory(
            baseline, through, read_change_rows(db, subscription, through),
            read_evidence(db, subscription, through),
        )
