from contextlib import closing

from archive.advance import advance_archive
from archive.history import UnknownArchiveSubscription, query_archive, read_published_history
from issue.changes import issue_through
from receive.intake import accept_delivery
from receive.receipts import find_received_item
from review.reopen import UnknownReport, reopen_report
from review.reports import ReportConflict, read_report_record, request_report
from source.notices import submit_notice
from station import open_archive
from tests.support import StationTest


class TestReview(StationTest):
    def test_wait_and_freeze(self):
        self.open_east()
        first, second, last, cover = self.issue_shift()
        for item in (cover, second, last):
            self.deliver(item)
        waiting = request_report(self.receiver, 'shift', 'E', 7)
        self.assertEqual(waiting.status, 'waiting')
        self.assertIsNone(waiting.totals)
        self.assertEqual(waiting.readiness.coverage.missing, ((4, first.item_id),))
        accept_delivery(self.receiver, 'E', first)
        unpublished = request_report(self.receiver, 'shift', 'E', 7)
        self.assertEqual(unpublished.readiness.unpublished, (4, 7))
        advance_archive(self.receiver, 'E')
        signed = request_report(self.receiver, 'shift', 'E', 7)
        totals = signed.totals
        self.assertEqual(signed.status, 'complete')
        self.assertEqual((totals.current, totals.entered, totals.left, totals.updated), (2, 1, 1, 1))
        self.assertEqual({ref.item_id for ref in totals.evidence}, {
            'initial:0:E', 'coverage:7:E', 'change:4:E', 'change:5:E', 'change:6:E',
        })
        submit_notice(self.source, 'K', 'edit', body='下一班澄清')
        for item in issue_through(self.source, 'E'):
            self.deliver(item)
        self.assertEqual(query_archive(self.receiver, 'E').frontier, 8)
        self.assertEqual(request_report(self.receiver, 'shift', 'E', 7), signed)

    def test_historical_request(self):
        self.open_east()
        window = self.issue_shift()
        submit_notice(self.source, 'A', 'withdraw')
        for item in (*window, *issue_through(self.source, 'E')):
            self.deliver(item)
        self.assertEqual(len(query_archive(self.receiver, 'E').members), 1)
        report = request_report(self.receiver, 'past', 'E', 5)
        self.assertEqual((report.totals.current, report.totals.entered, report.totals.left, report.totals.updated), (2, 1, 1, 0))
        self.assertEqual({ref.item_id for ref in report.totals.evidence}, {
            'initial:0:E', 'coverage:7:E', 'change:4:E', 'change:5:E',
        })
        coverage = next(ref for ref in report.totals.evidence if ref.kind == 'coverage')
        self.assertEqual((coverage.position, coverage.through), (4, 5))
        baseline = request_report(self.receiver, 'base', 'E', 3)
        self.assertEqual((baseline.totals.current, baseline.totals.entered), (2, 0))
        self.assertEqual([ref.item_id for ref in baseline.totals.evidence], ['initial:0:E'])
        with self.assertRaises(ValueError):
            request_report(self.receiver, 'too-early', 'E', 2)
        with self.assertRaises(UnknownArchiveSubscription):
            request_report(self.receiver, 'unknown-sub', 'X', 3)
        self.assertIsNone(read_report_record(self.receiver, 'too-early'))
        self.assertIsNone(read_report_record(self.receiver, 'unknown-sub'))

    def test_same_number(self):
        self.open_east()
        waiting = request_report(self.receiver, 'same', 'E', 7)
        self.assertEqual(waiting.readiness.coverage.uncovered, ((4, 7),))
        self.assertEqual(reopen_report(self.receiver, 'same'), waiting)
        with self.assertRaises(ReportConflict):
            request_report(self.receiver, 'same', 'E', 6)
        for item in self.issue_shift():
            self.deliver(item)
        signed = reopen_report(self.receiver, 'same')
        self.assertEqual(signed.status, 'complete')
        self.assertEqual(request_report(self.receiver, 'same', 'E', 7), signed)
        with self.assertRaises(ReportConflict):
            request_report(self.receiver, 'same', 'X', 7)
        with self.assertRaises(UnknownReport):
            reopen_report(self.receiver, 'missing')

    def test_read_without_source(self):
        self.open_east()
        for item in self.issue_shift():
            self.deliver(item)
        expected = request_report(self.receiver, 'archive', 'E', 7)
        history = read_published_history(self.receiver, 'E', 7)
        self.source.close()
        self.receiver.close()
        with closing(open_archive(self.archive_path)) as restarted:
            self.assertEqual(reopen_report(restarted, 'archive'), expected)
            self.assertEqual(read_published_history(restarted, 'E', 7), history)
            for ref in history.evidence:
                item, receipt = find_received_item(restarted, ref.item_id)
                self.assertEqual(receipt.receipt_id, ref.receipt_id)
                if ref.kind == 'change':
                    self.assertEqual(item.payload['revision']['seq'], ref.revision)
                    self.assertEqual(item.payload['revision']['before']['revision'], ref.prior_revision)
            stored = restarted.execute('SELECT report_id FROM report_pickups').fetchall()
            self.assertEqual(stored, [('archive',)])
