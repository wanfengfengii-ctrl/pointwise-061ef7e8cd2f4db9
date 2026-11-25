from dataclasses import dataclass
import hashlib
import json


class UnknownIssuedItem(LookupError):
    pass


@dataclass(frozen=True)
class IssuedItem:
    item_id: str
    subscription: str
    kind: str
    payload: dict


def delivery_id(subscription, kind, point):
    return f'{kind}:{point}:{subscription}'


def item_position(item):
    key = {'initial': 'baseline', 'change': 'seq', 'coverage': 'hi'}[item.kind]
    return item.payload[key]


def canonical_item_bytes(item):
    envelope = {
        'item_id': item.item_id,
        'subscription': item.subscription,
        'kind': item.kind,
        'payload': item.payload,
    }
    return json.dumps(envelope, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def decode_item(wire):
    return IssuedItem(**json.loads(wire))


def item_digest(item):
    return hashlib.sha256(canonical_item_bytes(item)).hexdigest()


def initialize_delivery_ledger(db):
    db.execute('''CREATE TABLE IF NOT EXISTS issued_items (
        item_id TEXT PRIMARY KEY, subscription TEXT NOT NULL, kind TEXT NOT NULL,
        point INTEGER NOT NULL, wire BLOB NOT NULL, UNIQUE(subscription, kind, point)
    )''')


def store_issued_item(db, item):
    db.execute('INSERT INTO issued_items VALUES (?, ?, ?, ?, ?)', (
        item.item_id, item.subscription, item.kind, item_position(item), canonical_item_bytes(item),
    ))


def get_issued_item(db, item_id):
    row = db.execute('SELECT wire FROM issued_items WHERE item_id = ?', (item_id,)).fetchone()
    if row is None:
        raise UnknownIssuedItem(item_id)
    return decode_item(row[0])


def read_issued_windows(db, subscription):
    rows = db.execute('''SELECT wire FROM issued_items
                         WHERE subscription = ? AND kind = 'coverage' ORDER BY point''', (subscription,))
    return tuple(decode_item(row[0]) for row in rows)
