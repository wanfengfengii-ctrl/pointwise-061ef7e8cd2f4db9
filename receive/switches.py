from dataclasses import dataclass
import json

from storage import read_view


@dataclass(frozen=True)
class ReceivedSwitch:
    switch_id: str
    subscription: str
    cutover: int
    item_id: str
    new_regions: tuple[str, ...]


def initialize_received_switches(db):
    db.execute('''CREATE TABLE IF NOT EXISTS receive_switches (
        switch_id TEXT PRIMARY KEY, subscription TEXT NOT NULL UNIQUE,
        cutover INTEGER NOT NULL, item_id TEXT NOT NULL UNIQUE
            REFERENCES received_items(item_id), new_regions TEXT NOT NULL
    )''')


def register_received_switch(db, switch_id, subscription, cutover, item_id, new_regions):
    db.execute('INSERT INTO receive_switches VALUES (?, ?, ?, ?, ?)', (
        switch_id, subscription, cutover, item_id, json.dumps(list(new_regions)),
    ))


def _from_row(row):
    if row is None:
        return None
    switch_id, subscription, cutover, item_id, new_regions = row
    return ReceivedSwitch(switch_id, subscription, cutover, item_id, tuple(json.loads(new_regions)))


def read_received_switch(db, subscription):
    with read_view(db):
        return _from_row(db.execute('''SELECT switch_id, subscription, cutover, item_id, new_regions
                                       FROM receive_switches WHERE subscription = ?''', (subscription,)).fetchone())


def read_received_switch_by_id(db, switch_id):
    with read_view(db):
        return _from_row(db.execute('''SELECT switch_id, subscription, cutover, item_id, new_regions
                                       FROM receive_switches WHERE switch_id = ?''', (switch_id,)).fetchone())
