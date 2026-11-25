from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.history import query_archive
from issue.changes import issue_through
from issue.ledger import get_issued_item
from issue.subscriptions import open_subscription
from issue.switches import request_switch
from receive.identity import DeliveryConflict
from receive.receipts import find_received_item
from station import (
    open_archive, open_source, query_switch, receive_and_publish, read_recording, enter_recording,
)
from tests.support import StationTest


def _change_ids(window):
    return [item.item_id for item in window if item.kind == 'change']


def _coverage(window):
    return next(item for item in window if item.kind == 'coverage')


class TestCutover(StationTest):
    def _open_switch(self, publish_before=False):
        recording = read_recording('cutover')
        case = next(row for row in recording['cases'] if row['switch_id'] == 'EW-5')
        initial = open_subscription(self.source, 'E', recording['old_regions'])
        self.deliver(initial)
        enter_recording(self.source, case['before'])
        if publish_before:
            for item in issue_through(self.source, 'E'):
                self.deliver(item)
        cutover, boundary = request_switch(self.source, 'E', 'EW-5', recording['new_regions'])
        enter_recording(self.source, case['after'])
        return cutover, boundary, issue_through(self.source, 'E')

    def test_waiting_then_boundary(self):
        cutover, boundary, window = self._open_switch()
        self.assertEqual(cutover, 5)
        self.assertEqual([item.kind for item in boundary], ['switch'])
        self.assertIsNone(query_switch(self.receiver, 'EW-5'))
        self.assertEqual(_change_ids(window), ['change:4:E', 'change:6:E'])
        self.assertEqual([item.payload['regions'] for item in window if item.kind == 'change'],
                         [['东区'], ['西区']])
        # Deliver the switch basis and the new-segment items before the old segment.
        for item in (*boundary, *(item for item in window if item.payload.get('hi', 0) > cutover),
                     *(item for item in window if item.payload.get('hi', 0) <= cutover)):
            self.deliver(item)
        self.assertEqual(query_switch(self.receiver, 'EW-5'), {'cutover': 5, 'status': 'active'})
        view = query_archive(self.receiver, 'E')
        self.assertEqual(view.frontier, 7)
        self.assertEqual([(c.seq, c.notice_id, c.effect, c.phase) for c in view.changes], [
            (4, 'B', 'update', 'revision'),
            (5, 'B', 'leave', 'switch'),
            (5, 'A', 'enter', 'switch'),
            (6, 'A', 'leave', 'revision'),
        ])
        self.assertEqual([m.notice_id for m in view.members], ['K'])

    def test_switch_provenance_and_reopen(self):
        recording = read_recording('cutover')
        initial = open_subscription(self.source, 'E', recording['old_regions'])
        self.deliver(initial)
        case = recording['cases'][0]
        enter_recording(self.source, case['before'])
        cutover, boundary = request_switch(self.source, 'E', 'EW-5', recording['new_regions'])
        enter_recording(self.source, case['after'])
        window = issue_through(self.source, 'E')
        for item in (*boundary, *window):
            self.deliver(item)
        view = query_archive(self.receiver, 'E')
        switch_rows = [change for change in view.changes if change.phase == 'switch']
        self.assertEqual([row.item_id for row in switch_rows], ['switch:5:E', 'switch:5:E'])
        self.assertEqual([row.receipt_id for row in switch_rows],
                         [find_received_item(self.receiver, 'switch:5:E')[1].receipt_id] * 2)
        switch_evidence = next(ref for ref in view.evidence if ref.kind == 'switch')
        self.assertEqual((switch_evidence.item_id, switch_evidence.position), ('switch:5:E', 5))
        self.source.close()
        self.receiver.close()
        with closing(open_archive(self.archive_path)) as reopened:
            restored = query_archive(reopened, 'E')
            self.assertEqual(restored.changes, view.changes)
            for row in switch_rows:
                item, receipt = find_received_item(reopened, row.item_id)
                self.assertEqual(receipt.receipt_id, row.receipt_id)

    def test_switch_request_replay_and_conflict(self):
        recording = read_recording('cutover')
        initial = open_subscription(self.source, 'E', recording['old_regions'])
        self.deliver(initial)
        cutover, boundary = request_switch(self.source, 'E', 'EW-5', recording['new_regions'])
        repeat = request_switch(self.source, 'E', 'EW-5', recording['new_regions'])
        self.assertEqual(repeat, (cutover, boundary))
        with self.assertRaises(ValueError):
            request_switch(self.source, 'E', 'EW-5', recording['old_regions'])
        with self.assertRaises(ValueError):
            request_switch(self.source, 'E', 'EW-6', recording['new_regions'])
        with self.assertRaises(LookupError):
            request_switch(self.source, 'OTHER', 'EW-5', recording['new_regions'])

    def test_boundary_without_revision_and_shared_notice(self):
        recording = read_recording('cutover')
        initial = open_subscription(self.source, 'E', recording['old_regions'])
        self.deliver(initial)
        cutover, boundary = request_switch(self.source, 'E', 'EW-5', recording['new_regions'])
        self.deliver(boundary[0])
        view = query_archive(self.receiver, 'E')
        self.assertEqual(view.frontier, 3)
        self.assertEqual([(c.seq, c.notice_id, c.effect, c.phase) for c in view.changes], [
            (3, 'B', 'leave', 'switch'), (3, 'A', 'enter', 'switch'),
        ])
        self.assertEqual([m.notice_id for m in view.members], ['A', 'K'])
        self.assertEqual(query_switch(self.receiver, 'EW-5'), {'cutover': 3, 'status': 'active'})

    def test_old_segment_completes_before_boundary(self):
        cutover, boundary, window = self._open_switch()
        old = [item for item in window if item.kind == 'change' and item.payload['seq'] <= cutover]
        old_cover = [item for item in window if item.kind == 'coverage' and item.payload['hi'] <= cutover]
        for item in (*old, *old_cover):
            self.deliver(item)
        self.assertEqual(query_archive(self.receiver, 'E').frontier, cutover)
        # Boundary not yet delivered: archive still shows only the old segment.
        self.assertIsNone(query_switch(self.receiver, 'EW-5'))
        self.deliver(boundary[0])
        switch_rows = [c for c in query_archive(self.receiver, 'E').changes if c.phase == 'switch']
        self.assertEqual([(c.seq, c.notice_id, c.effect) for c in switch_rows],
                         [(cutover, 'B', 'leave'), (cutover, 'A', 'enter')])

    def test_item_replay_and_bad_switch(self):
        recording = read_recording('cutover')
        initial = open_subscription(self.source, 'E', recording['old_regions'])
        self.deliver(initial)
        cutover, boundary = request_switch(self.source, 'E', 'EW-5', recording['new_regions'])
        receipt, _ = accept = self.deliver(boundary[0])
        self.assertEqual(self.deliver(get_issued_item(self.source, boundary[0].item_id))[0], receipt)
        altered = replace(boundary[0], payload={**boundary[0].payload, 'new_regions': ['东区']})
        with self.assertRaises(DeliveryConflict):
            self.deliver(altered)

    def test_new_side_before_switch_basis(self):
        cutover, boundary, window = self._open_switch()
        new_changes = [item for item in window if item.kind == 'change' and item.payload['seq'] > cutover]
        new_cover = [item for item in window if item.kind == 'coverage' and item.payload['hi'] > cutover]
        old_changes = [item for item in window if item.kind == 'change' and item.payload['seq'] <= cutover]
        old_cover = [item for item in window if item.kind == 'coverage' and item.payload['hi'] <= cutover]
        # New segment and its coverage arrive, but the switch basis does not.
        for item in (*new_changes, *new_cover):
            self.deliver(item)
        self.assertIsNone(query_switch(self.receiver, 'EW-5'))
        early = query_archive(self.receiver, 'E')
        self.assertEqual(early.frontier, 3)
        self.assertEqual(early.changes, ())
        for item in (*old_changes, *old_cover, boundary[0]):
            self.deliver(item)
        self.assertEqual(query_switch(self.receiver, 'EW-5'), {'cutover': 5, 'status': 'active'})
        view = query_archive(self.receiver, 'E')
        self.assertEqual(view.frontier, 7)
        self.assertEqual([(c.seq, c.notice_id, c.effect, c.phase) for c in view.changes], [
            (4, 'B', 'update', 'revision'),
            (5, 'B', 'leave', 'switch'),
            (5, 'A', 'enter', 'switch'),
            (6, 'A', 'leave', 'revision'),
        ])
        self.assertEqual([m.notice_id for m in view.members], ['K'])

    def test_ordered_delivery_matches_recorded(self):
        from station import play_cutover
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            self.assertEqual(play_cutover(first), play_cutover(second))
