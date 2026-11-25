from archive.evidence import store_evidence
from archive.membership import install_initial_members, read_baseline
from receive.receipts import read_received_items
from source.revisions import NoticeImage
from storage import write_transaction


def bootstrap_archive(db, subscription):
    with write_transaction(db):
        baseline = read_baseline(db, subscription)
        if baseline is not None:
            return baseline.seq
        received = read_received_items(db, subscription, 'initial')
        if not received:
            return None
        item, receipt = received[0]
        sequence = item.payload['baseline']
        members = tuple(NoticeImage.from_dict(member) for member in item.payload['members'])
        install_initial_members(db, subscription, sequence, members)
        store_evidence(db, item, receipt)
    return sequence
