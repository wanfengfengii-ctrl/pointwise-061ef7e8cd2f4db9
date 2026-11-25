from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from issue.changes import issue_through
from issue.subscriptions import open_subscription
from station import enter_recording, open_archive, open_source, read_recording, receive_and_publish


class StationTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.source_path = root / 'source.sqlite'
        self.archive_path = root / 'archive.sqlite'
        self.source = self.enterContext(closing(open_source(self.source_path)))
        self.receiver = self.enterContext(closing(open_archive(self.archive_path)))
        enter_recording(self.source, read_recording('registry'))

    def open_east(self):
        item = open_subscription(self.source, 'E', ['东区'])
        receipt, _ = receive_and_publish(self.receiver, 'E', item)
        return item, receipt

    def issue_shift(self):
        recording = read_recording('shift')
        enter_recording(self.source, recording['changes'])
        return issue_through(self.source, 'E', recording['target'])

    def deliver(self, item):
        return receive_and_publish(self.receiver, item.subscription, item)
