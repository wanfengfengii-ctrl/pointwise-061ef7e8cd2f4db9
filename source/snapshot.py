from dataclasses import dataclass

from source.journal import read_active_notices, source_frontier
from source.revisions import NoticeImage
from storage import read_view


@dataclass(frozen=True)
class SourceSnapshot:
    sequence: int
    notices: tuple[NoticeImage, ...]


def read_current_snapshot(db):
    with read_view(db):
        sequence = source_frontier(db)
        return SourceSnapshot(sequence, read_active_notices(db))
