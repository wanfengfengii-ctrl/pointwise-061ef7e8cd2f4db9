from issue.ledger import IssuedItem, canonical_item_bytes, delivery_id
from receive.receipts import find_received_item
from source.regions import normalize_regions, regions_intersect, require_name
from source.revisions import NoticeImage, make_revision
from storage import require_sequence


class DeliveryBindingError(ValueError):
    pass


class DeliveryConflict(DeliveryBindingError):
    pass


def _require_fields(value, *fields):
    if type(value) is not dict or value.keys() != set(fields):
        raise ValueError(f'expected fields: {", ".join(fields)}')


def _regions(value):
    normalized = normalize_regions(value)
    if list(normalized) != value:
        raise ValueError('regions must be in canonical order')
    return normalized


def _image(value, through):
    _require_fields(value, 'notice_id', 'revision', 'body', 'regions')
    require_name(value['notice_id'])
    if not 0 < require_sequence(value['revision']) <= through or not isinstance(value['body'], str):
        raise ValueError('invalid notice image')
    _regions(value['regions'])
    return NoticeImage.from_dict(value)


def _validate_initial(payload, baseline):
    _require_fields(payload, 'baseline', 'regions', 'members')
    scope = _regions(payload['regions'])
    if type(payload['members']) is not list:
        raise ValueError('initial members must be a list')
    ids = []
    for member in payload['members']:
        image = _image(member, baseline)
        if not regions_intersect(scope, image.regions):
            raise ValueError('initial member outside subscription')
        ids.append(image.notice_id)
    if ids != sorted(set(ids)):
        raise ValueError('initial members must be unique and ordered')
    return 0


def _validate_change(payload, baseline):
    _require_fields(payload, 'baseline', 'seq', 'effect', 'revision', 'regions')
    seq = require_sequence(payload['seq'])
    effect = payload['effect']
    revision = payload['revision']
    _regions(payload['regions'])
    _require_fields(revision, 'seq', 'notice_id', 'action', 'before', 'after')
    if seq <= baseline or require_sequence(revision['seq']) != seq:
        raise ValueError('source sequence does not match change item')
    before = _image(revision['before'], seq - 1) if revision['before'] is not None else None
    after = _image(revision['after'], seq) if revision['after'] is not None else None
    action = revision['action']
    candidate = make_revision(
        seq, revision['notice_id'], action, before,
        body=after.body if after and action in ('create', 'edit') else None,
        regions=after.regions if after and action in ('create', 'move') else None,
    )
    if candidate.after != after:
        raise ValueError('invalid after image for source action')
    if effect not in ('enter', 'leave', 'update'):
        raise ValueError('unknown membership effect')
    if (effect in ('leave', 'update') and before is None) or (effect in ('enter', 'update') and after is None):
        raise ValueError('effect requires its corresponding notice image')
    return seq


def _validate_window(subscription, payload, baseline):
    _require_fields(payload, 'baseline', 'lo', 'hi', 'entries')
    lo = require_sequence(payload['lo'])
    hi = require_sequence(payload['hi'])
    if not baseline <= lo < hi or type(payload['entries']) is not list:
        raise ValueError('invalid coverage interval')
    previous = lo
    for entry in payload['entries']:
        if type(entry) is not list or len(entry) != 2:
            raise ValueError('invalid coverage entry')
        seq = require_sequence(entry[0])
        if not previous < seq <= hi or entry[1] != delivery_id(subscription, 'change', seq):
            raise ValueError('coverage entries must be ordered and bound to their changes')
        previous = seq
    return hi


def _validate_switch(payload):
    _require_fields(payload, 'baseline', 'cutover', 'switch_id', 'old_regions', 'new_regions',
                    'leaves', 'enters')
    baseline = require_sequence(payload['baseline'])
    cutover = require_sequence(payload['cutover'])
    require_name(payload['switch_id'])
    old_regions = _regions(payload['old_regions'])
    new_regions = _regions(payload['new_regions'])
    if cutover < baseline:
        raise ValueError('cutover precedes the subscription baseline')
    for name, required, excluded in (('leaves', old_regions, new_regions),
                                     ('enters', new_regions, old_regions)):
        images = payload[name]
        if type(images) is not list:
            raise ValueError(f'{name} must be a list')
        ids = []
        for value in images:
            image = _image(value, cutover)
            if not regions_intersect(required, image.regions) or regions_intersect(excluded, image.regions):
                raise ValueError(f'{name} image is outside its boundary side')
            ids.append(image.notice_id)
        if ids != sorted(set(ids)):
            raise ValueError(f'{name} images must be unique and ordered')
    return cutover


def validate_issued_item(subscription, item):
    if not isinstance(item, IssuedItem) or item.subscription != subscription:
        raise DeliveryBindingError('item is bound to a different subscription')
    try:
        require_name(subscription)
        require_name(item.item_id)
        baseline = require_sequence(item.payload['baseline'])
        if item.kind == 'initial':
            point = _validate_initial(item.payload, baseline)
        elif item.kind == 'change':
            point = _validate_change(item.payload, baseline)
        elif item.kind == 'coverage':
            point = _validate_window(subscription, item.payload, baseline)
        elif item.kind == 'switch':
            point = _validate_switch(item.payload)
        else:
            raise ValueError('unknown issue kind')
        if item.item_id != delivery_id(subscription, item.kind, point):
            raise ValueError('item identity does not match its payload')
        canonical_item_bytes(item)
    except (KeyError, LookupError, TypeError, ValueError) as exc:
        raise DeliveryBindingError(str(exc)) from exc


def check_replay(db, item):
    previous = find_received_item(db, item.item_id)
    if previous is None:
        return None
    original, receipt = previous
    try:
        same = canonical_item_bytes(original) == canonical_item_bytes(item)
    except (TypeError, ValueError) as exc:
        raise DeliveryConflict(item.item_id) from exc
    if not same:
        raise DeliveryConflict(item.item_id)
    return receipt
