from review.reports import evaluate_report, read_report_record
from storage import write_transaction


class UnknownReport(LookupError):
    pass


def reopen_report(db, report_id):
    with write_transaction(db):
        record = read_report_record(db, report_id)
        if record is None:
            raise UnknownReport(report_id)
        report = evaluate_report(db, record)
        if report.status == 'complete':
            db.execute('INSERT INTO report_pickups (report_id) VALUES (?)', (report_id,))
    return report
