def require_name(value):
    if not isinstance(value, str) or not value:
        raise ValueError('expected a non-empty name')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError('name must be valid Unicode') from exc
    return value


def normalize_regions(regions):
    if not isinstance(regions, (list, tuple, set, frozenset)):
        raise ValueError('regions must be a list, tuple, set or frozenset of names')
    return tuple(sorted({require_name(region) for region in regions}))


def regions_intersect(left, right):
    return not set(left).isdisjoint(right)
