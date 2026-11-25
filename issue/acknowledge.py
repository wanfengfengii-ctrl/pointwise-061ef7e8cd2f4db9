from dataclasses import dataclass

from issue.ledger import UnknownIssuedItem, get_issued_item, item_digest, read_issued_windows
from issue.subscriptions import read_subscription
from receive.receipts import Receipt, read_committed_receipt
from storage import read_view, write_transaction


class DeliveryConfirmationError(ValueError):
    pass


@dataclass(frozen=True)
class ConfirmationProgress:
    initial_confirmed: bool
    frontier: int | None
    confirmed: tuple[str, ...]


def initialize_confirmations(db):
    db.execute('''CREATE TABLE IF NOT EXISTS issue_confirmations (
        item_id TEXT PRIMARY KEY REFERENCES issued_items(item_id),
        subscription TEXT NOT NULL, receipt_id TEXT NOT NULL UNIQUE
    )''')


def read_confirmation_progress(db, subscription):
    with read_view(db):
        fixed = read_subscription(db, subscription)
        confirmed = tuple(row[0] for row in db.execute('''SELECT item_id FROM issue_confirmations
                                  WHERE subscription = ? ORDER BY item_id''', (subscription,)))
        known = set(confirmed)
        if fixed.initial_id not in known:
            return ConfirmationProgress(False, None, confirmed)
        frontier = fixed.baseline
        for window in read_issued_windows(db, subscription):
            if window.item_id not in known:
                break
            missing = next((seq for seq, item_id in window.payload['entries'] if item_id not in known), None)
            if missing is not None:
                frontier = missing - 1
                break
            frontier = window.payload['hi']
        return ConfirmationProgress(True, frontier, confirmed)


def confirm_receipt(db, receiver, subscription, item_id, receipt):
    if not isinstance(receipt, Receipt) or receipt.subscription != subscription or receipt.item_id != item_id:
        raise DeliveryConfirmationError('receipt binding does not match confirmation request')
    if not isinstance(receipt.receipt_id, str) or receiver.in_transaction:
        raise DeliveryConfirmationError('receipt cannot be checked as committed')
    if read_committed_receipt(receiver, receipt.receipt_id) != receipt:
        raise DeliveryConfirmationError('receipt is not committed at the receiver')
    with write_transaction(db):
        try:
            item = get_issued_item(db, item_id)
        except UnknownIssuedItem as exc:
            raise DeliveryConfirmationError('unknown item for confirmation') from exc
        if item.subscription != subscription or item_digest(item) != receipt.digest:
            raise DeliveryConfirmationError('receipt does not match the original issued item')
        db.execute('''INSERT INTO issue_confirmations VALUES (?, ?, ?)
                      ON CONFLICT(item_id) DO NOTHING''', (item_id, subscription, receipt.receipt_id))
        progress = read_confirmation_progress(db, subscription)
    return progress
