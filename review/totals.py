from collections import Counter
from dataclasses import dataclass

from archive.evidence import EvidenceReference
from archive.history import read_published_history


@dataclass(frozen=True)
class ReviewTotals:
    current: int
    entered: int
    left: int
    updated: int
    evidence: tuple[EvidenceReference, ...]


def calculate_totals(db, subscription, target):
    history = read_published_history(db, subscription, target)
    effects = Counter(change.effect for change in history.changes)
    entered, left, updated = (effects[effect] for effect in ('enter', 'leave', 'update'))
    return ReviewTotals(history.baseline.member_count + entered - left, entered, left, updated, history.evidence)
