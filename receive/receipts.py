from dataclasses import dataclass

from issue.ledger import canonical_item_bytes, decode_item, item_digest, item_position
from storage import MAX_SEQUENCE


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    subscription: str
    item_id: str
    digest: str


def initialize_receipts(db):
    db.execute('''CREATE TABLE IF NOT EXISTS received_items (
        item_id TEXT PRIMARY KEY, subscription TEXT NOT NULL, kind TEXT NOT NULL,
        point INTEGER NOT NULL, baseline INTEGER NOT NULL, wire BLOB NOT NULL,
        receipt_id TEXT NOT NULL UNIQUE, digest TEXT NOT NULL,
        UNIQUE(subscription, kind, point)
    )''')


def store_receipt(db, item):
    digest = item_digest(item)
    receipt = Receipt(f'receipt:{digest}', item.subscription, item.item_id, digest)
    db.execute('INSERT INTO received_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
        item.item_id, item.subscription, item.kind, item_position(item), item.payload['baseline'],
        canonical_item_bytes(item), receipt.receipt_id, digest,
    ))
    return receipt


def read_committed_receipt(db, receipt_id):
    if db.in_transaction:
        raise ValueError('receipt confirmation requires a connection outside a transaction')
    row = db.execute('''SELECT receipt_id, subscription, item_id, digest FROM received_items
                        WHERE receipt_id = ?''', (receipt_id,)).fetchone()
    return Receipt(*row) if row is not None else None


def find_received_item(db, item_id):
    row = db.execute('''SELECT wire, receipt_id, subscription, item_id, digest FROM received_items
                        WHERE item_id = ?''', (item_id,)).fetchone()
    if row is None:
        return None
    return decode_item(row[0]), Receipt(*row[1:])


def read_received_items(db, subscription, kind=None, *, after=-1, through=MAX_SEQUENCE):
    rows = db.execute('''SELECT wire, receipt_id, subscription, item_id, digest FROM received_items
                         WHERE subscription = ? AND point > ? AND point <= ?
                           AND (? IS NULL OR kind = ?) ORDER BY point, kind''', (
        subscription, after, through, kind, kind,
    ))
    return tuple((decode_item(row[0]), Receipt(*row[1:])) for row in rows)


def received_baseline(db, subscription):
    row = db.execute('SELECT baseline FROM received_items WHERE subscription = ? LIMIT 1', (subscription,)).fetchone()
    return row[0] if row is not None else None


def received_frontier(db, subscription):
    return db.execute('SELECT MAX(point) FROM received_items WHERE subscription = ?', (subscription,)).fetchone()[0]
