import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.advance import advance_archive
from archive.evidence import initialize_evidence
from archive.history import query_archive
from archive.membership import initialize_membership
from issue.acknowledge import confirm_receipt, initialize_confirmations
from issue.changes import issue_through
from issue.ledger import get_issued_item, initialize_delivery_ledger
from issue.subscriptions import initialize_subscriptions, open_subscription
from receive.intake import accept_delivery, initialize_intake
from review.reopen import reopen_report
from review.reports import initialize_reports, request_report
from source.journal import initialize_source
from source.notices import submit_notice
from storage import connect, write_transaction


def open_source(path):
    db = connect(path)
    try:
        with write_transaction(db):
            initialize_source(db)
            initialize_delivery_ledger(db)
            initialize_subscriptions(db)
            initialize_confirmations(db)
    except BaseException:
        db.close()
        raise
    return db


def open_archive(path):
    db = connect(path)
    try:
        with write_transaction(db):
            initialize_intake(db)
            initialize_membership(db)
            initialize_evidence(db)
            initialize_reports(db)
    except BaseException:
        db.close()
        raise
    return db


def receive_and_publish(db, subscription, item):
    receipt, coverage = accept_delivery(db, subscription, item)
    advance_archive(db, subscription)
    return receipt, coverage


def read_recording(name):
    filename = {'registry': 'registry.json', 'shift': 'shift.json'}[name]
    return json.loads((Path(__file__).resolve().parent / 'data' / filename).read_text(encoding='utf-8'))


def enter_recording(db, actions):
    return tuple(submit_notice(db, **action) for action in actions)


def _summarize_view(view):
    references = {ref.revision: ref for ref in view.evidence if ref.kind == 'change'}
    return {
        'frontier': view.frontier,
        'members': [member.notice_id for member in view.members],
        'changes': [[change.seq, change.notice_id, change.effect, references[change.seq].prior_revision]
                    for change in view.changes],
        'evidence': [asdict(ref) for ref in view.evidence],
    }


def play_shift(directory, *, ordered=False):
    directory = Path(directory)
    source_path = directory / 'source.sqlite'
    archive_path = directory / 'archive.sqlite'
    recording = read_recording('shift')
    subscription = 'E'
    target = recording['target']
    with closing(open_source(source_path)) as source, closing(open_archive(archive_path)) as receiver:
        enter_recording(source, read_recording('registry'))
        initial = open_subscription(source, subscription, ['东区'])
        receipt, _ = receive_and_publish(receiver, subscription, initial)
        confirm_receipt(source, receiver, subscription, initial.item_id, receipt)

        enter_recording(source, recording['changes'])
        window = issue_through(source, subscription, target)
        bodies = {item.payload['seq']: item.item_id for item in window if item.kind == 'change'}
        coverage = next(item for item in window if item.kind == 'coverage')
        order = ['coverage', *sorted(bodies)] if ordered else recording['delivery_order']
        for index, key in enumerate(order):
            item_id = coverage.item_id if key == 'coverage' else bodies[key]
            item = get_issued_item(source, item_id)
            receipt, _ = receive_and_publish(receiver, subscription, item)
            progress = confirm_receipt(source, receiver, subscription, item_id, receipt)
            if index == 1:
                waiting = request_report(receiver, 'shift-1', subscription, target)
                before = query_archive(receiver, subscription)

        report = request_report(receiver, 'shift-1', subscription, target)
        published = query_archive(receiver, subscription)
        enter_recording(source, recording['later'])
        for item in issue_through(source, subscription):
            receive_and_publish(receiver, subscription, item)

    with closing(open_archive(archive_path)) as receiver:
        reopened = reopen_report(receiver, 'shift-1')
        latest = query_archive(receiver, subscription)
    return {
        'input': {'subscription': subscription, 'regions': initial.payload['regions'], 'target': target, 'delivery_order': order},
        'initial': {'baseline': initial.payload['baseline'], 'members': [row['notice_id'] for row in initial.payload['members']]},
        'waiting': {'status': waiting.status, 'frontier': before.frontier,
                    'missing': [seq for seq, _ in waiting.readiness.coverage.missing]},
        'published': _summarize_view(published),
        'confirmed': progress.frontier,
        'report': asdict(report),
        'reopened': asdict(reopened),
        'latest': _summarize_view(latest),
    }


def main():
    parser = argparse.ArgumentParser(description='市政工程公告采集站')
    parser.add_argument('command', choices=('demo',), help='运行一班固定辖区采集及交班录制')
    parser.parse_args()
    with TemporaryDirectory(prefix='notice-station-') as directory:
        result = play_shift(directory)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
