from contextlib import closing
import sqlite3

from archive.history import UnknownArchiveSubscription, query_archive
from issue.subscriptions import SubscriptionConflict, open_subscription
from source.journal import read_notice, read_revisions, source_frontier
from source.notices import submit_notice
from station import open_archive, open_source, receive_and_publish
from tests.support import StationTest


class TestInitial(StationTest):
    def test_reopen_keeps_seeded_and_user_rows(self):
        user = submit_notice(self.source, 'U', 'create', body='留在资料组的用户登记', regions=['西区'])
        updated = submit_notice(self.source, 'K', 'edit', body='用户保留的订正版')
        with closing(open_source(self.source_path)) as reopened:
            self.assertEqual(source_frontier(reopened), 5)
            self.assertEqual(read_notice(reopened, 'U'), user.after)
            self.assertEqual(read_notice(reopened, 'K'), updated.after)
            self.assertEqual([entry.seq for entry in read_revisions(reopened, 0, 5)], [1, 2, 3, 4, 5])
            initial = open_subscription(reopened, 'E', ['东区'])
        receipt, _ = self.deliver(initial)
        with closing(open_archive(self.archive_path)) as reopened:
            copy, _ = receive_and_publish(reopened, 'E', initial)
            view = query_archive(reopened, 'E')
        self.assertEqual(copy, receipt)
        self.assertEqual(view.frontier, 5)
        self.assertEqual([(row.notice_id, row.revision) for row in view.members], [('B', 1), ('K', 5)])
        self.assertEqual(view.changes, ())
        self.assertEqual([ref.receipt_id for ref in view.evidence], [receipt.receipt_id])

    def test_empty_scope(self):
        with self.assertRaises(UnknownArchiveSubscription):
            query_archive(self.receiver, 'EMPTY')
        item = open_subscription(self.source, 'EMPTY', [])
        receipt, coverage = self.deliver(item)
        archive = query_archive(self.receiver, 'EMPTY')
        self.assertEqual(item.payload['members'], [])
        self.assertEqual((archive.baseline, archive.frontier, coverage.eligible), (3, 3, 3))
        self.assertEqual(archive.members, ())
        self.assertEqual(archive.evidence[0].receipt_id, receipt.receipt_id)

    def test_initial_replay(self):
        item = open_subscription(self.source, 'EW', ['东区', '西区', '东区'])
        submit_notice(self.source, 'K', 'edit', body='首批后的修订')
        with closing(open_source(self.source_path)) as reopened:
            self.assertEqual(open_subscription(reopened, 'EW', {'西区', '东区'}), item)
            with self.assertRaises(SubscriptionConflict):
                open_subscription(reopened, 'EW', ['西区'])
        self.assertEqual(item.payload['baseline'], 3)
        self.assertEqual([row['notice_id'] for row in item.payload['members']], ['A', 'B', 'K'])

    def test_snapshot_consistency(self):
        attempts = []
        with closing(open_source(self.source_path)) as writer:
            writer.execute('PRAGMA busy_timeout = 0')

            def during_snapshot(sql):
                if 'FROM source_notices' in sql and 'WHERE image IS NOT NULL' in sql and not attempts:
                    try:
                        attempts.append(submit_notice(writer, 'B', 'move', regions=['西区']))
                    except sqlite3.OperationalError as exc:
                        attempts.append(exc.sqlite_errorcode)

            self.source.set_trace_callback(during_snapshot)
            try:
                initial = open_subscription(self.source, 'E', ['东区'])
            finally:
                self.source.set_trace_callback(None)
            self.assertEqual(attempts, [sqlite3.SQLITE_BUSY])
            self.assertEqual((initial.payload['baseline'], [m['notice_id'] for m in initial.payload['members']]), (3, ['B', 'K']))
            moved = submit_notice(writer, 'B', 'move', regions=['西区'])
            self.assertEqual(moved.seq, 4)
            later = open_subscription(writer, 'NEXT', ['东区'])
        self.assertEqual((later.payload['baseline'], [m['notice_id'] for m in later.payload['members']]), (4, ['K']))
        self.deliver(initial)
        self.assertEqual([row.notice_id for row in query_archive(self.receiver, 'E').members], ['B', 'K'])
        self.assertEqual(open_subscription(self.source, 'E', ['东区']), initial)
