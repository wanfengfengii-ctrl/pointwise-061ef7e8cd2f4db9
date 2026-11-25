from dataclasses import dataclass
import json

from source.revisions import NoticeImage, NoticeRevision


class MembershipConflict(ValueError):
    pass


@dataclass(frozen=True)
class MembershipChange:
    seq: int
    notice_id: str
    effect: str
    revision: NoticeRevision | None = None
    phase: str = 'revision'
    item_id: str | None = None
    receipt_id: str | None = None
    image: NoticeImage | None = None


@dataclass(frozen=True)
class ArchiveBaseline:
    seq: int
    member_count: int


def initialize_membership(db):
    db.execute('''CREATE TABLE IF NOT EXISTS archive_subscriptions (
        subscription TEXT PRIMARY KEY, baseline INTEGER NOT NULL,
        member_count INTEGER NOT NULL, frontier INTEGER NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS archive_members (
        subscription TEXT NOT NULL REFERENCES archive_subscriptions(subscription),
        notice_id TEXT NOT NULL, image TEXT NOT NULL, PRIMARY KEY(subscription, notice_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS archive_changes (
        subscription TEXT NOT NULL REFERENCES archive_subscriptions(subscription),
        seq INTEGER NOT NULL, phase TEXT NOT NULL, notice_id TEXT NOT NULL,
        effect TEXT NOT NULL, revision TEXT, image TEXT, item_id TEXT, receipt_id TEXT,
        PRIMARY KEY(subscription, seq, phase, notice_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS archive_switches (
        subscription TEXT PRIMARY KEY REFERENCES archive_subscriptions(subscription),
        switch_id TEXT NOT NULL, cutover INTEGER NOT NULL, new_regions TEXT NOT NULL,
        item_id TEXT NOT NULL, receipt_id TEXT NOT NULL
    )''')


def install_initial_members(db, subscription, seq, members):
    db.execute('INSERT INTO archive_subscriptions VALUES (?, ?, ?, ?)', (
        subscription, seq, len(members), seq,
    ))
    db.executemany('INSERT INTO archive_members VALUES (?, ?, ?)', (
        (subscription, image.notice_id, json.dumps(image.to_dict(), sort_keys=True)) for image in members
    ))


def _current_image(db, subscription, notice_id):
    row = db.execute('SELECT image FROM archive_members WHERE subscription = ? AND notice_id = ?',
                     (subscription, notice_id)).fetchone()
    return NoticeImage.from_dict(json.loads(row[0])) if row else None


def _insert_change_row(db, subscription, change):
    revision = json.dumps(change.revision.to_dict(), sort_keys=True) if change.revision else None
    image = json.dumps(change.image.to_dict(), sort_keys=True) if change.image else None
    db.execute('INSERT INTO archive_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (
        subscription, change.seq, change.phase, change.notice_id, change.effect,
        revision, image, change.item_id, change.receipt_id,
    ))


def apply_change(db, subscription, change):
    revision = change.revision
    key = (subscription, change.notice_id)
    current = _current_image(db, subscription, change.notice_id)
    expected = None if change.effect == 'enter' else revision.before
    if current != expected or (change.effect != 'enter' and expected is None):
        raise MembershipConflict(f'membership at {change.seq} conflicts with the previous image')

    if change.effect == 'leave':
        db.execute('DELETE FROM archive_members WHERE subscription = ? AND notice_id = ?', key)
    elif change.effect in ('enter', 'update') and revision.after is not None:
        db.execute('''INSERT INTO archive_members VALUES (?, ?, ?)
                      ON CONFLICT(subscription, notice_id) DO UPDATE SET image = excluded.image''', (
            *key, json.dumps(revision.after.to_dict(), sort_keys=True),
        ))
    else:
        raise MembershipConflict('invalid change effect or missing image')
    _insert_change_row(db, subscription, change)


def apply_boundary(db, subscription, cutover, leaves, enters, item_id, receipt_id):
    for effect, images in (('leave', leaves), ('enter', enters)):
        for image in images:
            key = (subscription, image.notice_id)
            current = _current_image(db, subscription, image.notice_id)
            if effect == 'leave':
                if current != image:
                    raise MembershipConflict(f'boundary leave at {cutover} conflicts with the current image')
                db.execute('DELETE FROM archive_members WHERE subscription = ? AND notice_id = ?', key)
            else:
                if current is not None:
                    raise MembershipConflict(f'boundary enter at {cutover} conflicts with an existing image')
                db.execute('INSERT INTO archive_members VALUES (?, ?, ?)', (
                    *key, json.dumps(image.to_dict(), sort_keys=True),
                ))
            _insert_change_row(db, subscription, MembershipChange(
                cutover, image.notice_id, effect, None, 'switch', item_id, receipt_id, image,
            ))


def read_members(db, subscription):
    rows = db.execute('SELECT image FROM archive_members WHERE subscription = ? ORDER BY notice_id', (subscription,))
    return tuple(NoticeImage.from_dict(json.loads(row[0])) for row in rows)


def read_baseline(db, subscription):
    row = db.execute('SELECT baseline, member_count FROM archive_subscriptions WHERE subscription = ?', (subscription,)).fetchone()
    return ArchiveBaseline(*row) if row is not None else None


def read_published_frontier(db, subscription):
    row = db.execute('SELECT frontier FROM archive_subscriptions WHERE subscription = ?', (subscription,)).fetchone()
    return row[0] if row is not None else None


def set_published_frontier(db, subscription, frontier):
    db.execute('UPDATE archive_subscriptions SET frontier = ? WHERE subscription = ?', (frontier, subscription))


def read_archive_switch(db, subscription):
    row = db.execute('''SELECT switch_id, cutover, new_regions, item_id, receipt_id FROM archive_switches
                        WHERE subscription = ?''', (subscription,)).fetchone()
    if row is None:
        return None
    switch_id, cutover, new_regions, item_id, receipt_id = row
    return switch_id, cutover, tuple(json.loads(new_regions)), item_id, receipt_id


def record_archive_switch(db, subscription, switch_id, cutover, new_regions, item_id, receipt_id):
    db.execute('INSERT INTO archive_switches VALUES (?, ?, ?, ?, ?, ?)', (
        subscription, switch_id, cutover, json.dumps(list(new_regions)), item_id, receipt_id,
    ))


def _change_from_row(row):
    seq, phase, notice_id, effect, revision, image, item_id, receipt_id = row
    return MembershipChange(
        seq, notice_id, effect,
        NoticeRevision.from_dict(json.loads(revision)) if revision else None,
        phase, item_id, receipt_id,
        NoticeImage.from_dict(json.loads(image)) if image else None,
    )


def read_change_rows(db, subscription, through):
    rows = db.execute('''SELECT seq, phase, notice_id, effect, revision, image, item_id, receipt_id
                         FROM archive_changes WHERE subscription = ? AND seq <= ?
                         ORDER BY seq,
                           CASE phase WHEN 'revision' THEN 0 ELSE 1 END,
                           CASE effect WHEN 'leave' THEN 0 WHEN 'enter' THEN 1 ELSE 2 END,
                           notice_id''', (subscription, through))
    return tuple(_change_from_row(row) for row in rows)
