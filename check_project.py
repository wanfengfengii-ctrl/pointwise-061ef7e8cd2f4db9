"""Run this project's full unittest suite and the recorded cutover check."""
import sys
from tempfile import TemporaryDirectory
import unittest

from station import verify_cutover


def main():
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')

    with TemporaryDirectory(prefix='cutover-verify-') as directory:
        problems = verify_cutover(directory)
    for problem in problems:
        print(f'CUTOVER {problem}')
    print(f'CUTOVER SUMMARY problems={len(problems)} result={"PASS" if not problems else "FAIL"}')

    successful = (
        result.testsRun > 0 and result.wasSuccessful() and not problems
    )
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
