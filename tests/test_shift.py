import json
from tempfile import TemporaryDirectory
import unittest

from station import play_shift


class TestShift(unittest.TestCase):
    def test_recorded_shift(self):
        with TemporaryDirectory() as delayed_dir, TemporaryDirectory() as ordered_dir:
            delayed = play_shift(delayed_dir)
            ordered = play_shift(ordered_dir, ordered=True)
        expected = {'frontier': 7, 'members': ['A', 'K'], 'changes': [
            [4, 'B', 'leave', 1], [5, 'A', 'enter', 3], [6, 'K', 'update', 2],
        ]}
        actual = {key: delayed['published'][key] for key in expected}
        expected_counts = {'current': 2, 'entered': 1, 'left': 1, 'updated': 1}
        actual_counts = {key: delayed['reopened']['totals'][key] for key in expected_counts}
        self.assertEqual(delayed['initial'], {'baseline': 3, 'members': ['B', 'K']})
        self.assertEqual(delayed['waiting'], {'status': 'waiting', 'frontier': 3, 'missing': [4, 5]})
        self.assertEqual(actual, expected)
        self.assertEqual(actual_counts, expected_counts)
        self.assertEqual(delayed['report'], delayed['reopened'])
        self.assertEqual(delayed['published'], ordered['published'])
        self.assertEqual(delayed['report'], ordered['report'])
        self.assertEqual(delayed['confirmed'], 7)
        self.assertEqual(delayed['latest']['frontier'], 9)
        self.assertEqual(delayed['latest']['members'], ['K'])
        self.assertEqual(delayed['latest']['changes'][-2:], [[8, 'K', 'update', 6], [9, 'A', 'leave', 5]])
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'V7',
            'input': delayed['input'],
            'expected': expected,
            'actual': actual,
            'downstream_expected': expected_counts,
            'downstream_actual': actual_counts,
            'code_paths': ['issue/changes.py', 'receive/coverage.py', 'archive/advance.py', 'review/totals.py', 'review/reopen.py'],
            'oracle': '手算：首批 B、K；B 退出、A 进入、K 更新，D 无关；2 + 1 - 1 = 2。',
        }, ensure_ascii=False, sort_keys=True))
