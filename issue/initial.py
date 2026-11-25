from issue.ledger import IssuedItem, delivery_id
from source.regions import regions_intersect
from source.snapshot import read_current_snapshot


def build_initial_list(db, subscription, regions):
    snapshot = read_current_snapshot(db)
    members = [image.to_dict() for image in snapshot.notices if regions_intersect(image.regions, regions)]
    return IssuedItem(delivery_id(subscription, 'initial', 0), subscription, 'initial', {
        'baseline': snapshot.sequence,
        'regions': list(regions),
        'members': members,
    })
