import json

from source.revisions import NoticeImage, NoticeRevision


def initialize_source(db):
    db.execute('''CREATE TABLE IF NOT EXISTS source_notices (
        notice_id TEXT PRIMARY KEY, latest_seq INTEGER NOT NULL, image TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS source_revisions (
        seq INTEGER PRIMARY KEY CHECK (seq > 0), notice_id TEXT NOT NULL,
        revision TEXT NOT NULL
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS source_notice_chain
                  ON source_revisions (notice_id, seq)''')


def notice_registered(db, notice_id):
    return db.execute('SELECT 1 FROM source_notices WHERE notice_id = ?', (notice_id,)).fetchone() is not None


def read_notice(db, notice_id):
    row = db.execute('SELECT image FROM source_notices WHERE notice_id = ?', (notice_id,)).fetchone()
    if row is None or row[0] is None:
        return None
    return NoticeImage.from_dict(json.loads(row[0]))


def source_frontier(db):
    return db.execute('SELECT COALESCE(MAX(seq), 0) FROM source_revisions').fetchone()[0]


def append_revision(db, revision):
    db.execute('INSERT INTO source_revisions VALUES (?, ?, ?)', (
        revision.seq, revision.notice_id, json.dumps(revision.to_dict(), sort_keys=True),
    ))
    image = json.dumps(revision.after.to_dict(), sort_keys=True) if revision.after else None
    db.execute('''INSERT INTO source_notices VALUES (?, ?, ?)
                  ON CONFLICT(notice_id) DO UPDATE SET
                  latest_seq = excluded.latest_seq, image = excluded.image''', (
        revision.notice_id, revision.seq, image,
    ))


def read_revisions(db, after, through):
    rows = db.execute('''SELECT revision FROM source_revisions
                         WHERE seq > ? AND seq <= ? ORDER BY seq''', (after, through))
    return tuple(NoticeRevision.from_dict(json.loads(row[0])) for row in rows)


def read_active_notices(db):
    rows = db.execute('''SELECT image FROM source_notices
                         WHERE image IS NOT NULL ORDER BY notice_id''')
    return tuple(NoticeImage.from_dict(json.loads(row[0])) for row in rows)
