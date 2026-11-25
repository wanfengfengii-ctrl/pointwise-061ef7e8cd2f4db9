from contextlib import contextmanager
import sqlite3


MAX_SEQUENCE = (1 << 63) - 1


def require_sequence(value):
    if type(value) is not int or not 0 <= value <= MAX_SEQUENCE:
        raise ValueError('expected a non-negative SQLite integer sequence')
    return value


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.execute('PRAGMA foreign_keys = ON')
    return db


@contextmanager
def _transaction(db, begin):
    db.execute(begin)
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


@contextmanager
def write_transaction(db):
    if db.in_transaction:
        raise RuntimeError('the service operation must own its write transaction')
    with _transaction(db, 'BEGIN IMMEDIATE'):
        yield


@contextmanager
def read_view(db):
    if db.in_transaction:
        yield
    else:
        with _transaction(db, 'BEGIN'):
            yield
