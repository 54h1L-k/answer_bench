"""AnswerBench CLI. Live requests happen only in explicit run/resume commands."""
import argparse
import json
import sys
from pathlib import Path
import yaml
from . import __version__
from .core import Invalid, Incompatible, load_config, write_json
from .defaults import demo_config
from .extraction import extract_run, import_review
from .journeys import generate, verify_bundle
from .metrics import compare
from .reporting import render_report
from .runner import create_run, execute, plan
from .storage import Store


def init(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'answerbench.yaml'
    if path.exists():
        raise Invalid(f'Configuration already exists: {path}')
    path.write_text(yaml.safe_dump(demo_config(), sort_keys=False, allow_unicode=True))
    return path


def parser():
    p = argparse.ArgumentParser(description='Auditable measurement of AI recommendations')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--config', default='answerbench.yaml')
    p.add_argument('--database', help='Override SQLite path for existing-run commands')
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('init', 'demo'):
        sub.add_parser(command).add_argument('directory', nargs='?', default='.' if command == 'init' else 'answerbench-demo')
    sub.add_parser('validate')
    q = sub.add_parser('generate'); q.add_argument('--out')
    q = sub.add_parser('journeys'); q.add_argument('--bundle')
    q = sub.add_parser('run'); q.add_argument('--bundle'); q.add_argument('--dry-run', action='store_true')
    q = sub.add_parser('resume'); q.add_argument('run_id'); q.add_argument('--retry-failed', action='store_true')
    sub.add_parser('runs')
    for command in ('extract', 'reproduce'):
        sub.add_parser(command).add_argument('run_id')
    q = sub.add_parser('report'); q.add_argument('run_id'); q.add_argument('--out', default='report')
    q = sub.add_parser('inspect'); q.add_argument('execution_id')
    q = sub.add_parser('review'); q.add_argument('run_id'); q.add_argument('--file', required=True)
    q = sub.add_parser('compare'); q.add_argument('baseline'); q.add_argument('comparison'); q.add_argument('--engine-a'); q.add_argument('--engine-b'); q.add_argument('--out')
    return p


def read_bundle(path):
    bundle = json.loads(Path(path).read_text())
    verify_bundle(bundle)
    return bundle


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == 'init':
            result = {'config': str(init(args.directory)), 'next': 'answerbench generate; answerbench run --dry-run'}
        elif args.command == 'demo':
            path = init(args.directory)
            c = load_config(path)
            bundle = generate(c)
            write_json(path.parent / '.answerbench/journeys.json', bundle)
            store = Store(path.parent / c['project']['database'])
            rid = create_run(store, c, bundle)
            execute(store, rid)
            extract_run(store, rid)
            result = {'run_id': rid, 'synthetic': True, 'config': str(path), 'report': render_report(store, rid, path.parent / 'report')}
        else:
            path = Path(args.config).resolve()
            needs_config = args.command in ('validate', 'generate', 'journeys', 'run') or not args.database
            c = load_config(path) if needs_config else None
            bundle_path = Path(getattr(args, 'bundle', None) or path.parent / '.answerbench/journeys.json')
            if args.command == 'validate':
                result = {'valid': True, 'plan': plan(c, generate(c))}
            elif args.command == 'generate':
                bundle = generate(c)
                out = Path(args.out) if args.out else bundle_path
                write_json(out, bundle)
                result = {'bundle': str(out.resolve()), 'hash': bundle['hash'], 'journeys': len(bundle['journeys'])}
            elif args.command == 'journeys':
                result = read_bundle(bundle_path)
            elif args.command == 'run' and args.dry_run:
                bundle = read_bundle(bundle_path)
                if bundle['hash'] != generate(c)['hash']:
                    raise Invalid('Frozen bundle differs from config; explicitly regenerate it')
                result = plan(c, bundle)
            else:
                database = args.database or path.parent / c['project']['database']
                store = Store(database)
                if args.command == 'run':
                    bundle = read_bundle(bundle_path)
                    if bundle['hash'] != generate(c)['hash']:
                        raise Invalid('Frozen bundle differs from config; explicitly regenerate it')
                    rid = create_run(store, c, bundle)
                    execute(store, rid)
                    result = {'run_id': rid, 'status': store.run(rid)['status'], 'next': f'answerbench extract {rid}'}
                elif args.command == 'resume':
                    execute(store, args.run_id, retry_failed=args.retry_failed)
                    result = {'run_id': args.run_id, 'status': store.run(args.run_id)['status']}
                elif args.command == 'runs':
                    result = store.rows('SELECT id,created_at,status FROM runs ORDER BY created_at DESC')
                elif args.command == 'extract':
                    result = {'batch_id': extract_run(store, args.run_id)}
                elif args.command == 'report':
                    result = {'report': render_report(store, args.run_id, args.out)}
                elif args.command == 'inspect':
                    result = store.trace(args.execution_id)
                elif args.command == 'reproduce':
                    run = store.run(args.run_id)
                    result = {'manifest': run['manifest'], 'bundle': run['bundle'], 'note': 'Frozen inputs reproduce the procedure; live outputs are not guaranteed identical.'}
                elif args.command == 'review':
                    result = {'batch_id': import_review(store, args.run_id, json.loads(Path(args.file).read_text()))}
                elif args.command == 'compare':
                    result = compare(store, args.baseline, args.comparison, args.engine_a, args.engine_b)
                    if args.out:
                        write_json(args.out, result)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 3 if isinstance(result, dict) and result.get('status') == 'incomplete' else 0
    except Incompatible as exc:
        print(f'Incompatible comparison: {exc}', file=sys.stderr)
        return 4
    except (Invalid, OSError, json.JSONDecodeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Interrupted. Saved evidence can be resumed using its run ID.', file=sys.stderr)
        return 130
    finally:
        if store:
            store.close()
