from dataclasses import dataclass
import json

from receive.identity import DeliveryConflict
from receive.receipts import read_received_items


@dataclass(frozen=True)
class CoverageDeclaration:
    item_id: str
    lo: int
    hi: int
    entries: tuple[tuple[int, str], ...]


def initialize_declarations(db):
    db.execute('''CREATE TABLE IF NOT EXISTS received_declarations (
        item_id TEXT PRIMARY KEY REFERENCES received_items(item_id), subscription TEXT NOT NULL,
        lo INTEGER NOT NULL, hi INTEGER NOT NULL, entries TEXT NOT NULL,
        CHECK (lo < hi), UNIQUE(subscription, lo)
    )''')


def persist_declaration(db, item):
    payload = item.payload
    lo, hi = payload['lo'], payload['hi']
    overlapping = db.execute('''SELECT 1 FROM received_declarations
        WHERE subscription = ? AND lo < ? AND hi > ?''', (item.subscription, hi, lo)).fetchone()
    if overlapping:
        raise DeliveryConflict('coverage interval overlaps a received declaration')

    expected = dict(payload['entries'])
    for change, _ in read_received_items(db, item.subscription, 'change', after=lo, through=hi):
        if expected.get(change.payload['seq']) != change.item_id:
            raise DeliveryConflict('declaration excludes an already received change')
    db.execute('INSERT INTO received_declarations VALUES (?, ?, ?, ?, ?)', (
        item.item_id, item.subscription, lo, hi, json.dumps(payload['entries']),
    ))


def read_declarations(db, subscription):
    rows = db.execute('''SELECT item_id, lo, hi, entries FROM received_declarations
                         WHERE subscription = ? ORDER BY lo''', (subscription,))
    return tuple(CoverageDeclaration(item_id, lo, hi, tuple(map(tuple, json.loads(entries))))
                 for item_id, lo, hi, entries in rows)


def check_change_declaration(db, item):
    seq = item.payload['seq']
    row = db.execute('''SELECT entries FROM received_declarations
                        WHERE subscription = ? AND lo < ? AND hi >= ?''', (item.subscription, seq, seq)).fetchone()
    if row is not None and dict(json.loads(row[0])).get(seq) != item.item_id:
        raise DeliveryConflict('change contradicts the received declaration')
