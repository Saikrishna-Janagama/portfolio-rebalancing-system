"""Generate data and run additional validation with one command."""
import argparse
import json
from pathlib import Path
from main import build, SEED
from complete import run


def main():
    parser = argparse.ArgumentParser(description='Run the complete portfolio simulation')
    parser.add_argument('--out', default=str(Path(__file__).resolve().parent / 'output'))
    parser.add_argument('--portfolios', type=int, default=1000)
    parser.add_argument('--instruments', type=int, default=5000)
    parser.add_argument('--days', type=int, default=250)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--verify-repeat', action='store_true')
    args = parser.parse_args()
    result = build(args.out, args.portfolios, args.instruments, args.days, args.seed)
    if args.verify_repeat:
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            repeated = build(temp, args.portfolios, args.instruments, args.days, args.seed)
            result['reproducible'] = (result['row_counts'] == repeated['row_counts'] and
                                      result['csv_sha256'] == repeated['csv_sha256'])
        (Path(args.out) / 'validation_summary.json').write_text(json.dumps(result, indent=2))
    extra = run(args.out)
    print(json.dumps({'baseline_pass': result['pass'],
                      'reproducible': result.get('reproducible', 'not checked'),
                      'exception_tests_pass': extra['all_cases_pass'],
                      'exception_cases': extra['candidate_cases'],
                      'output_directory': str(Path(args.out).resolve())}, indent=2))
    if not result['pass'] or not extra['all_cases_pass'] or result.get('reproducible') is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
