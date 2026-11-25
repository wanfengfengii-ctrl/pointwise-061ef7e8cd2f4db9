from dataclasses import asdict, dataclass
import json

from archive.evidence import EvidenceReference
from review.completeness import ReviewReadiness, assess_review
from review.totals import ReviewTotals, calculate_totals
from source.regions import require_name
from storage import require_sequence, write_transaction


class ReportConflict(ValueError):
    pass


@dataclass(frozen=True)
class CoverageReport:
    report_id: str
    subscription: str
    target: int
    status: str
    readiness: ReviewReadiness | None
    totals: ReviewTotals | None


@dataclass(frozen=True)
class ReportRecord:
    report_id: str
    subscription: str
    target: int
    frozen_result: str | None


def initialize_reports(db):
    db.execute('''CREATE TABLE IF NOT EXISTS review_reports (
        report_id TEXT PRIMARY KEY, subscription TEXT NOT NULL, target INTEGER NOT NULL,
        frozen_result TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS report_pickups (
        pickup_id INTEGER PRIMARY KEY, report_id TEXT NOT NULL REFERENCES review_reports(report_id)
    )''')


def read_report_record(db, report_id):
    row = db.execute('SELECT * FROM review_reports WHERE report_id = ?', (report_id,)).fetchone()
    return ReportRecord(*row) if row is not None else None


def evaluate_report(db, record):
    report_id, subscription, target = record.report_id, record.subscription, record.target
    if record.frozen_result is not None:
        result = json.loads(record.frozen_result)
        result['evidence'] = tuple(EvidenceReference(**ref) for ref in result['evidence'])
        return CoverageReport(report_id, subscription, target, 'complete', None, ReviewTotals(**result))

    readiness = assess_review(db, subscription, target)
    if readiness.status == 'waiting':
        return CoverageReport(report_id, subscription, target, 'waiting', readiness, None)
    totals = calculate_totals(db, subscription, target)
    db.execute('UPDATE review_reports SET frozen_result = ? WHERE report_id = ?', (
        json.dumps(asdict(totals), sort_keys=True), report_id,
    ))
    return CoverageReport(report_id, subscription, target, 'complete', None, totals)


def request_report(db, report_id, subscription, target):
    require_name(report_id)
    require_name(subscription)
    require_sequence(target)
    with write_transaction(db):
        record = read_report_record(db, report_id)
        if record is None:
            record = ReportRecord(report_id, subscription, target, None)
            db.execute('INSERT INTO review_reports VALUES (?, ?, ?, NULL)', (report_id, subscription, target))
        elif (record.subscription, record.target) != (subscription, target):
            raise ReportConflict(report_id)
        result = evaluate_report(db, record)
    return result
