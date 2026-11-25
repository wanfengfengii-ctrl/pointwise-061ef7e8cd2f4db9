from dataclasses import dataclass
import json

from issue.ledger import IssuedItem, delivery_id, get_issued_item, store_issued_item
from issue.subscriptions import read_subscription
from source.journal import read_active_notices, source_frontier
from source.regions import normalize_regions, regions_intersect, require_name
from source.revisions import NoticeImage
from storage import write_transaction


@dataclass(frozen=True)
class SwitchRecord:
    switch_id: str
    subscription: str
    cutover: int
    old_regions: tuple[str, ...]
    new_regions: tuple[str, ...]
    item_id: str


def initialize_switches(db):
    db.execute('''CREATE TABLE IF NOT EXISTS source_switches (
        switch_id TEXT PRIMARY KEY, subscription TEXT NOT NULL UNIQUE,
        cutover INTEGER NOT NULL, old_regions TEXT NOT NULL, new_regions TEXT NOT NULL,
        item_id TEXT NOT NULL REFERENCES issued_items(item_id)
    )''')


def _from_row(row):
    switch_id, subscription, cutover, old_regions, new_regions, item_id = row
    return SwitchRecord(switch_id, subscription, cutover, tuple(json.loads(old_regions)),
                        tuple(json.loads(new_regions)), item_id)


def read_switch(db, subscription):
    row = db.execute('''SELECT switch_id, subscription, cutover, old_regions, new_regions, item_id
                        FROM source_switches WHERE subscription = ?''', (subscription,)).fetchone()
    return _from_row(row) if row is not None else None


def read_switch_by_id(db, switch_id):
    row = db.execute('''SELECT switch_id, subscription, cutover, old_regions, new_regions, item_id
                        FROM source_switches WHERE switch_id = ?''', (switch_id,)).fetchone()
    return _from_row(row) if row is not None else None


def _boundary_images(db, old_regions, new_regions):
    images = read_active_notices(db)
    old_members = [image for image in images if regions_intersect(image.regions, old_regions)]
    new_members = [image for image in images if regions_intersect(image.regions, new_regions)]
    new_ids = {image.notice_id for image in new_members}
    old_ids = {image.notice_id for image in old_members}
    leaves = [image for image in old_members if image.notice_id not in new_ids]
    enters = [image for image in new_members if image.notice_id not in old_ids]
    return tuple(leaves), tuple(enters)


def request_switch(db, subscription, switch_id, regions):
    require_name(switch_id)
    new_regions = normalize_regions(regions)
    with write_transaction(db):
        fixed = read_subscription(db, subscription)
        known = read_switch_by_id(db, switch_id)
        if known is not None:
            if known.subscription == subscription and known.new_regions == new_regions:
                return known.cutover, (get_issued_item(db, known.item_id),)
            raise ValueError('switch number already registered with different parameters')
        if read_switch(db, subscription) is not None:
            raise ValueError('subscription has already been switched')

        cutover = source_frontier(db)
        leaves, enters = _boundary_images(db, fixed.regions, new_regions)
        item = IssuedItem(delivery_id(subscription, 'switch', cutover), subscription, 'switch', {
            'baseline': fixed.baseline,
            'cutover': cutover,
            'switch_id': switch_id,
            'old_regions': list(fixed.regions),
            'new_regions': list(new_regions),
            'leaves': [image.to_dict() for image in leaves],
            'enters': [image.to_dict() for image in enters],
        })
        store_issued_item(db, item)
        db.execute('INSERT INTO source_switches VALUES (?, ?, ?, ?, ?, ?)', (
            switch_id, subscription, cutover,
            json.dumps(list(fixed.regions)), json.dumps(list(new_regions)), item.item_id,
        ))
    return cutover, (item,)


def switch_images(payload):
    return (
        tuple(NoticeImage.from_dict(value) for value in payload['leaves']),
        tuple(NoticeImage.from_dict(value) for value in payload['enters']),
    )
