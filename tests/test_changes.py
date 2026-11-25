import sqlite3

from archive.history import query_archive
from issue.changes import issue_through
from issue.ledger import UnknownIssuedItem, get_issued_item
from source.journal import read_notice, read_revisions, source_frontier
from source.notices import submit_notice
from source.revisions import InvalidNoticeAction, UnknownNotice
from tests.support import StationTest


class TestChanges(StationTest):
    def test_unknown_withdraw(self):
        with self.assertRaises(UnknownNotice):
            submit_notice(self.source, 'missing', 'withdraw')
        self.assertEqual(source_frontier(self.source), 3)
        with self.assertRaises(InvalidNoticeAction):
            submit_notice(self.source, 'B', 'edit', body='合法正文', regions=[])
        revision = submit_notice(self.source, 'B', 'edit', body='招标澄清')
        self.assertEqual(revision.seq, 4)
        self.assertEqual(read_revisions(self.source, 3, 4), (revision,))
        self.assertEqual(revision.before.revision, 1)
        self.assertEqual(revision.after.regions, ('东区',))

    def test_region_projection(self):
        self.open_east()
        window = self.issue_shift()
        self.assertEqual(window[-1].payload['entries'], [[4, 'change:4:E'], [5, 'change:5:E'], [6, 'change:6:E']])
        for item in window:
            self.deliver(item)
        view = query_archive(self.receiver, 'E')
        self.assertEqual([(c.seq, c.notice_id, c.effect) for c in view.changes], [
            (4, 'B', 'leave'), (5, 'A', 'enter'), (6, 'K', 'update'),
        ])
        self.assertEqual([row.notice_id for row in view.members], ['A', 'K'])
        references = [ref for ref in view.evidence if ref.kind == 'change']
        self.assertEqual([(ref.revision, ref.prior_revision, ref.item_id) for ref in references], [
            (4, 1, 'change:4:E'), (5, 3, 'change:5:E'), (6, 2, 'change:6:E'),
        ])
        self.assertTrue(all(ref.receipt_id.startswith('receipt:') for ref in references))
        self.assertEqual(view.changes[0].revision.before.body, view.changes[0].revision.after.body)

    def test_withdrawal(self):
        self.open_east()
        revision = submit_notice(self.source, 'B', 'withdraw')
        self.assertIsNone(revision.after)
        self.assertIsNone(read_notice(self.source, 'B'))
        for action in ('withdraw', 'edit', 'move'):
            with self.assertRaises(UnknownNotice):
                submit_notice(self.source, 'B', action)
        with self.assertRaises(InvalidNoticeAction):
            submit_notice(self.source, 'B', 'create', body='重复公告', regions=['东区'])
        self.assertEqual(source_frontier(self.source), 4)
        self.assertEqual(read_revisions(self.source, 3, 4), (revision,))
        for item in issue_through(self.source, 'E'):
            self.deliver(item)
        view = query_archive(self.receiver, 'E')
        self.assertEqual([row.notice_id for row in view.members], ['K'])
        self.assertEqual((view.changes[0].effect, view.changes[0].revision), ('leave', revision))
        ref = next(ref for ref in view.evidence if ref.kind == 'change')
        self.assertEqual((ref.notice_id, ref.revision, ref.prior_revision, ref.item_id), ('B', 4, 1, 'change:4:E'))

    def test_source_and_issue_write_failures_leave_retryable_state(self):
        self.open_east()
        self.source.execute('''CREATE TRIGGER reject_revision BEFORE INSERT ON source_notices
            WHEN NEW.latest_seq = 4 BEGIN SELECT RAISE(ABORT, 'registry unavailable'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'registry unavailable'):
            submit_notice(self.source, 'A', 'move', regions=['东区'])
        self.assertEqual(source_frontier(self.source), 3)
        self.assertEqual(read_notice(self.source, 'A').regions, ('西区',))
        self.source.execute('DROP TRIGGER reject_revision')
        self.assertEqual(submit_notice(self.source, 'A', 'move', regions=['东区']).seq, 4)
        self.source.execute('''CREATE TRIGGER reject_window BEFORE INSERT ON issued_items
            WHEN NEW.kind = 'coverage' BEGIN SELECT RAISE(ABORT, 'window unavailable'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'window unavailable'):
            issue_through(self.source, 'E', 4)
        with self.assertRaises(UnknownIssuedItem):
            get_issued_item(self.source, 'change:4:E')
        self.source.execute('DROP TRIGGER reject_window')
        with self.assertRaises(ValueError):
            issue_through(self.source, 'E', 5)
        self.assertEqual([item.kind for item in issue_through(self.source, 'E', 4)], ['change', 'coverage'])
        self.assertEqual(issue_through(self.source, 'E', 4), ())
