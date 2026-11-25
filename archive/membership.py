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
    revision: NoticeRevision


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
        seq INTEGER NOT NULL, notice_id TEXT NOT NULL, effect TEXT NOT NULL,
        revision TEXT NOT NULL, PRIMARY KEY(subscription, seq)
    )''')


def install_initial_members(db, subscription, seq, members):
    db.execute('INSERT INTO archive_subscriptions VALUES (?, ?, ?, ?)', (
        subscription, seq, len(members), seq,
    ))
    db.executemany('INSERT INTO archive_members VALUES (?, ?, ?)', (
        (subscription, image.notice_id, json.dumps(image.to_dict(), sort_keys=True)) for image in members
    ))


def apply_change(db, subscription, change):
    revision = change.revision
    key = (subscription, change.notice_id)
    row = db.execute('SELECT image FROM archive_members WHERE subscription = ? AND notice_id = ?', key).fetchone()
    current = NoticeImage.from_dict(json.loads(row[0])) if row else None
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
    db.execute('INSERT INTO archive_changes VALUES (?, ?, ?, ?, ?)', (
        subscription, change.seq, change.notice_id, change.effect,
        json.dumps(revision.to_dict(), sort_keys=True),
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


def read_change_rows(db, subscription, through):
    rows = db.execute('''SELECT seq, notice_id, effect, revision FROM archive_changes
                         WHERE subscription = ? AND seq <= ? ORDER BY seq''', (subscription, through))
    return tuple(MembershipChange(seq, notice_id, effect, NoticeRevision.from_dict(json.loads(revision)))
                 for seq, notice_id, effect, revision in rows)
