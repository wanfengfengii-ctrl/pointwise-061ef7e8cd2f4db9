import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.advance import advance_archive
from archive.evidence import initialize_evidence
from archive.history import query_archive
from archive.membership import initialize_membership, read_archive_switch
from issue.acknowledge import confirm_receipt, initialize_confirmations
from issue.changes import issue_through
from issue.ledger import get_issued_item, initialize_delivery_ledger, item_position
from issue.subscriptions import initialize_subscriptions, open_subscription
from issue.switches import initialize_switches, request_switch
from receive.intake import accept_delivery, initialize_intake
from receive.receipts import find_received_item
from receive.switches import read_received_switch_by_id
from review.reopen import reopen_report
from review.reports import initialize_reports, request_report
from source.journal import initialize_source
from source.notices import submit_notice
from storage import connect, read_view, write_transaction


def open_source(path):
    db = connect(path)
    try:
        with write_transaction(db):
            initialize_source(db)
            initialize_delivery_ledger(db)
            initialize_subscriptions(db)
            initialize_switches(db)
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


def query_switch(receiver, switch_id):
    switch = read_received_switch_by_id(receiver, switch_id)
    if switch is None:
        return None
    with read_view(receiver):
        archived = read_archive_switch(receiver, switch.subscription)
    status = 'active' if archived is not None and archived[0] == switch_id else 'waiting'
    return {'cutover': switch.cutover, 'status': status}


def read_recording(name):
    filename = {'registry': 'registry.json', 'shift': 'shift.json', 'cutover': 'cutover.json'}[name]
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


def _summarize_cutover_view(view):
    return {
        'frontier': view.frontier,
        'members': [member.notice_id for member in view.members],
        'changes': [[change.seq, change.notice_id, change.effect, change.phase] for change in view.changes],
        'provenance': [{'seq': change.seq, 'notice_id': change.notice_id, 'phase': change.phase,
                        'item_id': change.item_id, 'receipt_id': change.receipt_id}
                       for change in view.changes if change.phase == 'switch'],
        'evidence': [asdict(ref) for ref in view.evidence],
    }


def _partition_cutover_items(window, cutover):
    coverage = [item for item in window if item.kind == 'coverage']
    bodies = [item for item in window if item.kind == 'change']
    new_cover = [item for item in coverage if item.payload['hi'] > cutover]
    old_cover = [item for item in coverage if item.payload['hi'] <= cutover]
    new_bodies = sorted((item for item in bodies if item.payload['seq'] > cutover),
                        key=lambda item: item.payload['seq'])
    old_bodies = sorted((item for item in bodies if item.payload['seq'] <= cutover),
                        key=lambda item: item.payload['seq'])
    return new_cover, new_bodies, old_cover, old_bodies


def play_cutover(directory):
    directory = Path(directory)
    recording = read_recording('cutover')
    subscription = recording['subscription']
    cases = {}
    for case in recording['cases']:
        case_dir = directory / case['switch_id']
        delayed = _play_cutover_case(case_dir / 'delayed', subscription, recording, case, ordered=False)
        ordered = _play_cutover_case(case_dir / 'ordered', subscription, recording, case, ordered=True)
        cases[case['switch_id']] = {
            'switch_id': case['switch_id'],
            'cutover': case['cutover'],
            'expected': case['expected'],
            'delayed': delayed,
            'ordered': ordered,
            'order_matches': (delayed['published'] == ordered['published']
                              and delayed['active'] == ordered['active']),
        }
    return cases


def _cutover_delivery_order(case, window, cutover, boundary_items, ordered):
    new_cover, new_bodies, old_cover, old_bodies = _partition_cutover_items(window, cutover)
    if ordered:
        return sorted((*boundary_items, *new_cover, *new_bodies, *old_cover, *old_bodies),
                      key=item_position)
    return [*boundary_items, *new_cover, *new_bodies, *old_cover, *old_bodies]


def _play_cutover_case(directory, subscription, recording, case, *, ordered):
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / 'source.sqlite'
    archive_path = directory / 'archive.sqlite'
    switch_id = case['switch_id']
    result = {}
    with closing(open_source(source_path)) as source, closing(open_archive(archive_path)) as receiver:
        enter_recording(source, read_recording('registry'))
        initial = open_subscription(source, subscription, recording['old_regions'])
        receive_and_publish(receiver, subscription, initial)

        enter_recording(source, case['before'])
        if case['publish_before_request']:
            for item in issue_through(source, subscription):
                receive_and_publish(receiver, subscription, item)

        cutover, boundary_items = request_switch(source, subscription, switch_id, recording['new_regions'])
        enter_recording(source, case['after'])
        window = issue_through(source, subscription)
        replay = request_switch(source, subscription, switch_id, recording['new_regions'])
        result['replayed_switch'] = (
            replay[0] == cutover
            and [item.item_id for item in replay[1]] == [item.item_id for item in boundary_items]
        )

        order = _cutover_delivery_order(case, window, cutover, boundary_items, ordered)
        new_cover, new_bodies, old_cover, old_bodies = _partition_cutover_items(window, cutover)
        if case['publish_before_request'] or ordered:
            first_side = list(order)
            second_side = []
        else:
            first_side = [*boundary_items, *new_cover, *new_bodies]
            second_side = [*old_cover, *old_bodies]
        receipts = {}
        for item in first_side:
            receipt, _ = receive_and_publish(receiver, subscription, item)
            receipts[item.item_id] = receipt.receipt_id
        new_side = {'switch': query_switch(receiver, switch_id),
                    'frontier': query_archive(receiver, subscription).frontier}
        for item in second_side:
            receipt, _ = receive_and_publish(receiver, subscription, item)
            receipts[item.item_id] = receipt.receipt_id
        result['new_segment_before_old'] = None if (ordered or case['publish_before_request']) else new_side

        replays = {}
        for item in order:
            original = get_issued_item(source, item.item_id)
            receipt, _ = receive_and_publish(receiver, subscription, original)
            replays[item.item_id] = receipt.receipt_id == receipts[item.item_id]
        result['replayed_items'] = replays

        published = query_archive(receiver, subscription)
        result['published'] = _summarize_cutover_view(published)
        result['active'] = query_switch(receiver, switch_id)
        result['first_receipts'] = receipts

    with closing(open_archive(archive_path)) as receiver:
        reopened = query_archive(receiver, subscription)
        provenance = []
        for ref in reopened.evidence:
            item, receipt = find_received_item(receiver, ref.item_id)
            provenance.append({
                'item_id': ref.item_id, 'kind': ref.kind,
                'receipt_matches': receipt.receipt_id == ref.receipt_id,
                'recovered': item.item_id == ref.item_id,
            })
        boundary_receipts = [find_received_item(receiver, change.item_id)[1].receipt_id
                             for change in reopened.changes if change.phase == 'switch']
    result['reopened'] = _summarize_cutover_view(reopened)
    result['reopened_provenance'] = provenance
    result['boundary_receipts'] = boundary_receipts
    return result


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


def verify_cutover(directory):
    """Replay every recorded switch and compare against its expected ledger."""
    results = play_cutover(directory)
    problems = []
    for switch_id, outcome in sorted(results.items()):
        expected = outcome['expected']
        for side_name in ('delayed', 'ordered'):
            side = outcome[side_name]
            published = side['published']
            actual = {'frontier': published['frontier'], 'members': published['members'],
                      'changes': published['changes']}
            wanted = {'frontier': expected['frontier'], 'members': expected['members'],
                      'changes': expected['changes']}
            if actual != wanted:
                problems.append(f'{switch_id}/{side_name}: published ledger {actual} != {wanted}')
            if not side['replayed_switch']:
                problems.append(f'{switch_id}/{side_name}: switch replay changed the record')
            if not all(side['replayed_items'].values()):
                problems.append(f'{switch_id}/{side_name}: replayed item produced a new receipt')
            active = side['active']
            if active != {'cutover': outcome['cutover'], 'status': 'active'}:
                problems.append(f'{switch_id}/{side_name}: switch status {active} is not active')
            reopened = side['reopened']
            if reopened['changes'] != published['changes'] or reopened['frontier'] != published['frontier']:
                problems.append(f'{switch_id}/{side_name}: reopened archive differs from the live one')
            if not all(row['receipt_matches'] and row['recovered']
                       for row in side['reopened_provenance']):
                problems.append(f'{switch_id}/{side_name}: evidence cannot be read back after reopen')
            boundary = published['provenance']
            if [row['receipt_id'] for row in boundary] != side['boundary_receipts']:
                problems.append(f'{switch_id}/{side_name}: boundary receipts do not match evidence')
            if not boundary:
                problems.append(f'{switch_id}/{side_name}: boundary rows lack their switch provenance')
        if not outcome['order_matches']:
            problems.append(f'{switch_id}: ordered delivery differs from the recorded order')
        boundary_check = outcome['delayed']['new_segment_before_old']
        if boundary_check is not None:
            if boundary_check['switch'] != {'cutover': outcome['cutover'], 'status': 'waiting'}:
                problems.append(f'{switch_id}: new segment did not wait for the switch boundary')
            if boundary_check['frontier'] >= outcome['cutover']:
                problems.append(f'{switch_id}: new segment entered the ledger before the old segment was complete')
    return problems


def main():
    parser = argparse.ArgumentParser(description='市政工程公告采集站')
    parser.add_argument('command', choices=('demo',), help='运行固定辖区采集、交班及换订录制')
    parser.parse_args()
    with TemporaryDirectory(prefix='notice-station-') as directory:
        root = Path(directory)
        (root / 'shift').mkdir()
        result = play_shift(root / 'shift')
        result['cutover'] = play_cutover(root / 'cutover')
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
