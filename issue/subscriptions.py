from dataclasses import dataclass
import json

from issue.initial import build_initial_list
from issue.ledger import get_issued_item, store_issued_item
from source.regions import normalize_regions, require_name
from storage import write_transaction


class SubscriptionConflict(ValueError):
    pass


class UnknownSubscription(LookupError):
    pass


@dataclass(frozen=True)
class Subscription:
    subscription: str
    regions: tuple[str, ...]
    baseline: int
    initial_id: str


def initialize_subscriptions(db):
    db.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
        subscription TEXT PRIMARY KEY, regions TEXT NOT NULL, baseline INTEGER NOT NULL,
        initial_id TEXT NOT NULL REFERENCES issued_items(item_id)
    )''')


def read_subscription(db, subscription):
    row = db.execute('''SELECT regions, baseline, initial_id FROM subscriptions
                        WHERE subscription = ?''', (subscription,)).fetchone()
    if row is None:
        raise UnknownSubscription(subscription)
    return Subscription(subscription, tuple(json.loads(row[0])), row[1], row[2])


def open_subscription(db, subscription, regions):
    require_name(subscription)
    regions = normalize_regions(regions)
    with write_transaction(db):
        try:
            existing = read_subscription(db, subscription)
        except UnknownSubscription:
            initial = build_initial_list(db, subscription, regions)
            store_issued_item(db, initial)
            db.execute('INSERT INTO subscriptions VALUES (?, ?, ?, ?)', (
                subscription, json.dumps(regions), initial.payload['baseline'], initial.item_id,
            ))
        else:
            if existing.regions != regions:
                raise SubscriptionConflict(subscription)
            initial = get_issued_item(db, existing.initial_id)
    return initial
