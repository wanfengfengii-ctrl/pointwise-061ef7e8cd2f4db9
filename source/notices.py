from source.journal import append_revision, notice_registered, read_notice, source_frontier
from source.regions import require_name
from source.revisions import InvalidNoticeAction, make_revision
from storage import write_transaction


def submit_notice(db, notice_id, action, *, body=None, regions=None):
    try:
        require_name(notice_id)
    except ValueError as exc:
        raise InvalidNoticeAction(str(exc)) from exc
    with write_transaction(db):
        if action == 'create' and notice_registered(db, notice_id):
            raise InvalidNoticeAction('notice already registered')
        revision = make_revision(
            source_frontier(db) + 1, notice_id, action, read_notice(db, notice_id),
            body=body, regions=regions,
        )
        append_revision(db, revision)
    return revision
