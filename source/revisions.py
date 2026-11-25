from dataclasses import dataclass

from source.regions import normalize_regions, require_name
from storage import require_sequence


class UnknownNotice(LookupError):
    pass


class InvalidNoticeAction(ValueError):
    pass


@dataclass(frozen=True)
class NoticeImage:
    notice_id: str
    revision: int
    body: str
    regions: tuple[str, ...]

    def to_dict(self):
        return {
            'notice_id': self.notice_id,
            'revision': self.revision,
            'body': self.body,
            'regions': list(self.regions),
        }

    @classmethod
    def from_dict(cls, value):
        return cls(value['notice_id'], value['revision'], value['body'], tuple(value['regions']))


@dataclass(frozen=True)
class NoticeRevision:
    seq: int
    notice_id: str
    action: str
    before: NoticeImage | None
    after: NoticeImage | None

    def to_dict(self):
        return {
            'seq': self.seq,
            'notice_id': self.notice_id,
            'action': self.action,
            'before': self.before.to_dict() if self.before else None,
            'after': self.after.to_dict() if self.after else None,
        }

    @classmethod
    def from_dict(cls, value):
        before = value['before']
        after = value['after']
        return cls(
            value['seq'], value['notice_id'], value['action'],
            NoticeImage.from_dict(before) if before is not None else None,
            NoticeImage.from_dict(after) if after is not None else None,
        )


def make_revision(seq, notice_id, action, previous, *, body=None, regions=None):
    require_name(notice_id)
    if require_sequence(seq) == 0:
        raise InvalidNoticeAction('a revision requires a positive source sequence')
    if action not in ('create', 'edit', 'move', 'withdraw'):
        raise InvalidNoticeAction(f'unknown notice action: {action!r}')
    if action == 'create':
        if previous is not None:
            raise InvalidNoticeAction('notice already registered')
    elif previous is None:
        raise UnknownNotice(notice_id)
    elif previous.notice_id != notice_id or previous.revision >= seq:
        raise InvalidNoticeAction('previous image belongs to another revision chain')

    if action in ('create', 'edit'):
        if not isinstance(body, str):
            raise InvalidNoticeAction('action requires a text body')
    elif body is not None:
        raise InvalidNoticeAction('action does not accept a body')

    if action in ('create', 'move'):
        try:
            current_regions = normalize_regions(regions)
        except ValueError as exc:
            raise InvalidNoticeAction(str(exc)) from exc
    elif regions is not None:
        raise InvalidNoticeAction('action does not accept regions')
    else:
        current_regions = previous.regions

    if action == 'withdraw':
        after = None
    else:
        current_body = previous.body if action == 'move' else body
        after = NoticeImage(notice_id, seq, current_body, current_regions)
    return NoticeRevision(seq, notice_id, action, previous, after)
