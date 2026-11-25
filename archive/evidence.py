from dataclasses import dataclass

from source.revisions import NoticeRevision


@dataclass(frozen=True)
class EvidenceReference:
    kind: str
    item_id: str
    receipt_id: str
    position: int
    through: int
    notice_id: str | None = None
    revision: int | None = None
    prior_revision: int | None = None


def initialize_evidence(db):
    db.execute('''CREATE TABLE IF NOT EXISTS archive_evidence (
        subscription TEXT NOT NULL REFERENCES archive_subscriptions(subscription),
        kind TEXT NOT NULL, item_id TEXT NOT NULL REFERENCES received_items(item_id),
        receipt_id TEXT NOT NULL REFERENCES received_items(receipt_id),
        position INTEGER NOT NULL, through INTEGER NOT NULL, notice_id TEXT,
        revision INTEGER, prior_revision INTEGER, PRIMARY KEY(subscription, item_id)
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS archive_evidence_order
                  ON archive_evidence(subscription, position, kind)''')


def store_evidence(db, item, receipt, *, through=None):
    if (receipt.item_id, receipt.subscription) != (item.item_id, item.subscription):
        raise ValueError('evidence does not match the received item')
    notice_id = revision_id = prior = None
    if item.kind == 'coverage':
        position = item.payload['lo'] + 1
        if through is None or not position <= through <= item.payload['hi']:
            raise ValueError('published evidence must be inside the declared interval')
    elif item.kind == 'initial':
        position = through = item.payload['baseline']
    elif item.kind == 'change':
        revision = NoticeRevision.from_dict(item.payload['revision'])
        position = through = revision_id = revision.seq
        notice_id = revision.notice_id
        prior = revision.before.revision if revision.before else None
    else:
        raise ValueError('unknown evidence kind')

    db.execute('''INSERT INTO archive_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(subscription, item_id) DO UPDATE SET through = MAX(through, excluded.through)''', (
        item.subscription, item.kind, item.item_id, receipt.receipt_id, position, through,
        notice_id, revision_id, prior,
    ))


def read_evidence(db, subscription, through):
    rows = db.execute('''SELECT kind, item_id, receipt_id, position, MIN(through, ?),
                                notice_id, revision, prior_revision FROM archive_evidence
                         WHERE subscription = ? AND position <= ? ORDER BY position, kind, item_id''', (
        through, subscription, through,
    ))
    return tuple(EvidenceReference(*row) for row in rows)
