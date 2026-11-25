from contextlib import closing
from dataclasses import replace
import sqlite3

from archive.advance import advance_archive
from archive.history import query_archive
from issue.acknowledge import DeliveryConfirmationError, confirm_receipt, read_confirmation_progress
from issue.changes import issue_through
from issue.ledger import get_issued_item, item_digest
from issue.subscriptions import open_subscription
from receive.coverage import read_coverage
from receive.identity import DeliveryBindingError, DeliveryConflict
from receive.intake import accept_delivery
from receive.receipts import Receipt, read_committed_receipt
from source.notices import submit_notice
from station import open_archive
from tests.support import StationTest


class TestIntake(StationTest):
    def test_cover_before_body(self):
        self.open_east()
        first, second, last, cover = self.issue_shift()
        self.deliver(cover)
        _, status = self.deliver(last)
        self.assertEqual(status.eligible, 3)
        self.assertEqual(status.missing, ((4, first.item_id), (5, second.item_id)))
        self.assertEqual(status.uncovered, ())
        self.assertEqual(query_archive(self.receiver, 'E').changes, ())
        self.deliver(get_issued_item(self.source, second.item_id))
        self.assertEqual(query_archive(self.receiver, 'E').frontier, 3)
        self.deliver(get_issued_item(self.source, first.item_id))
        archive = query_archive(self.receiver, 'E')
        self.assertEqual([change.seq for change in archive.changes], [4, 5, 6])
        self.assertEqual(archive.frontier, 7)

    def test_irrelevant_tail(self):
        self.open_east()
        submit_notice(self.source, 'B', 'edit', body='东区稿件')
        submit_notice(self.source, 'A', 'edit', body='西区稿件')
        change, cover = issue_through(self.source, 'E')
        self.assertEqual((cover.payload['lo'], cover.payload['hi']), (3, 5))
        self.deliver(cover)
        self.assertEqual(query_archive(self.receiver, 'E').frontier, 3)
        self.deliver(change)
        view = query_archive(self.receiver, 'E')
        self.assertEqual(view.frontier, 5)
        self.assertEqual([entry.seq for entry in view.changes], [4])
        submit_notice(self.source, 'A', 'edit', body='仍在西区')
        only_cover, = issue_through(self.source, 'E')
        self.assertEqual(only_cover.payload['entries'], [])
        self.deliver(only_cover)
        advanced = query_archive(self.receiver, 'E')
        self.assertEqual((advanced.frontier, advanced.changes), (6, view.changes))
        self.assertEqual([(ref.position, ref.through) for ref in advanced.evidence if ref.kind == 'coverage'], [(4, 5), (6, 6)])

    def test_conflicting_replay(self):
        self.open_east()
        _, _, item, cover = self.issue_shift()
        receipt, _ = self.deliver(item)
        self.deliver(cover)
        before = query_archive(self.receiver, 'E')
        self.assertEqual(self.deliver(item)[0], receipt)
        altered = replace(item, payload={**item.payload, 'revision': {
            **item.payload['revision'], 'after': {**item.payload['revision']['after'], 'body': '异文'},
        }})
        with self.assertRaises(DeliveryConflict):
            self.deliver(altered)
        with self.assertRaises(DeliveryBindingError):
            accept_delivery(self.receiver, 'W', item)
        self.assertEqual(read_committed_receipt(self.receiver, receipt.receipt_id), receipt)
        self.assertEqual(query_archive(self.receiver, 'E'), before)

    def test_failed_commit(self):
        initial, initial_receipt = self.open_east()
        confirm_receipt(self.source, self.receiver, 'E', initial.item_id, initial_receipt)
        item, _, _, _ = self.issue_shift()
        self.receiver.execute('CREATE TABLE commit_parent (id INTEGER PRIMARY KEY)')
        self.receiver.execute('''CREATE TABLE commit_child (
            id INTEGER REFERENCES commit_parent(id) DEFERRABLE INITIALLY DEFERRED
        )''')
        self.receiver.execute('''CREATE TRIGGER reject_intake_at_commit AFTER INSERT ON received_items
            WHEN NEW.kind = 'change' BEGIN INSERT INTO commit_child VALUES (1); END''')
        digest = item_digest(item)
        candidate = Receipt(f'receipt:{digest}', 'E', item.item_id, digest)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            accept_delivery(self.receiver, 'E', item)
        self.assertFalse(self.receiver.in_transaction)
        self.assertIsNone(read_committed_receipt(self.receiver, candidate.receipt_id))
        with closing(open_archive(self.archive_path)) as reopened:
            self.assertIsNone(read_committed_receipt(reopened, candidate.receipt_id))
            with self.assertRaises(DeliveryConfirmationError):
                confirm_receipt(self.source, reopened, 'E', item.item_id, candidate)
        self.assertEqual(read_confirmation_progress(self.source, 'E').confirmed, (initial.item_id,))
        self.receiver.execute('DROP TRIGGER reject_intake_at_commit')
        receipt, _ = self.deliver(get_issued_item(self.source, item.item_id))
        self.assertEqual(receipt, candidate)
        confirmed = confirm_receipt(self.source, self.receiver, 'E', item.item_id, receipt)
        self.assertEqual(confirmed, confirm_receipt(self.source, self.receiver, 'E', item.item_id, receipt))
        self.assertEqual(set(confirmed.confirmed), {initial.item_id, item.item_id})
        self.assertEqual(confirmed.frontier, 3)

    def test_confirmation_binding(self):
        self.open_east()
        open_subscription(self.source, 'PAIR', ['东区'])
        first, second, _, _ = self.issue_shift()
        other, *_ = issue_through(self.source, 'PAIR')
        receipt, _ = self.deliver(first)
        for subscription, item_id in [('E', second.item_id), ('PAIR', other.item_id)]:
            with self.assertRaises(DeliveryConfirmationError):
                confirm_receipt(self.source, self.receiver, subscription, item_id, receipt)
            self.assertEqual(read_confirmation_progress(self.source, subscription).confirmed, ())
        with self.assertRaises(DeliveryConfirmationError):
            confirm_receipt(self.source, self.receiver, 'E', first.item_id, replace(receipt, digest='other'))
        progress = confirm_receipt(self.source, self.receiver, 'E', first.item_id, receipt)
        self.assertEqual(progress.confirmed, (first.item_id,))
        self.assertIsNone(progress.frontier)

    def test_confirmed_prefix(self):
        initial, initial_receipt = self.open_east()
        first, second, last, cover = self.issue_shift()
        for item in (cover, last, second):
            receipt, _ = self.deliver(item)
            progress = confirm_receipt(self.source, self.receiver, 'E', item.item_id, receipt)
        self.assertFalse(progress.initial_confirmed)
        self.assertIsNone(progress.frontier)
        progress = confirm_receipt(self.source, self.receiver, 'E', initial.item_id, initial_receipt)
        self.assertEqual(progress.frontier, 3)
        receipt, _ = self.deliver(first)
        progress = confirm_receipt(self.source, self.receiver, 'E', first.item_id, receipt)
        self.assertEqual(progress.frontier, 7)
        self.assertEqual(len(progress.confirmed), 5)

    def test_body_before_initial_and_unknown_coverage(self):
        initial = open_subscription(self.source, 'E', ['东区'])
        first, second, last, cover = self.issue_shift()
        self.deliver(last)
        self.assertEqual(query_archive(self.receiver, 'E').status, 'awaiting_initial')
        status = read_coverage(self.receiver, 'E', 7)
        self.assertEqual((status.eligible, status.missing, status.uncovered), (3, (), ((4, 7),)))
        self.deliver(cover)
        self.deliver(second)
        self.deliver(first)
        self.assertIsNone(query_archive(self.receiver, 'E').frontier)
        self.deliver(initial)
        self.assertEqual(query_archive(self.receiver, 'E').frontier, 7)

    def test_publication_failure_keeps_committed_receipts(self):
        self.open_east()
        first, second, last, cover = self.issue_shift()
        for item in (last, cover, second, first):
            receipt, _ = accept_delivery(self.receiver, 'E', item)
        self.receiver.execute('''CREATE TRIGGER reject_publication BEFORE INSERT ON archive_evidence
            WHEN NEW.revision = 5 BEGIN SELECT RAISE(ABORT, 'evidence unavailable'); END''')
        before = query_archive(self.receiver, 'E')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'evidence unavailable'):
            advance_archive(self.receiver, 'E')
        self.assertEqual(query_archive(self.receiver, 'E'), before)
        self.assertEqual(read_committed_receipt(self.receiver, receipt.receipt_id), receipt)
        self.receiver.execute('DROP TRIGGER reject_publication')
        self.assertEqual(advance_archive(self.receiver, 'E'), 7)
        self.assertEqual([change.seq for change in query_archive(self.receiver, 'E').changes], [4, 5, 6])
