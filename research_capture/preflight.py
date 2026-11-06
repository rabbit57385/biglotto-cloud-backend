"""Manual private-code preflight. Never reads DB credentials or runs capture/sync."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from bridge import SOURCE_REPO, UPDATER_REPO, source_config, verify_checkout, emit

WORKFLOW = '.github/workflows/capture-code-preflight.yml'
IMPORT_CHECK = """globalThis.fetch = () => { throw Error('PREFLIGHT_NETWORK_FORBIDDEN'); };
const c = await import('./src/lib/capture/contract.ts');
const a = await import('./src/lib/capture/github-artifact.ts');
const h = await import('./src/lib/capture/handoff.ts');
if (![c.collectCapture,c.validateCapture,a.receiveArtifact,h.receiveHandoffs].every(f=>typeof f==='function')) throw Error('IMPORT_REJECTED');
"""


def context(env):
    if (env.get('GITHUB_REPOSITORY') != UPDATER_REPO or env.get('GITHUB_ACTIONS') != 'true'
            or env.get('GITHUB_REF_NAME') != 'master' or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_WORKFLOW_REF') != f'{UPDATER_REPO}/{WORKFLOW}@refs/heads/master'):
        raise ValueError('CHECKOUT_FAILED')


def clean_environment(env):
    return {k: v for k, v in env.items() if k.upper() in ('PATH', 'SYSTEMROOT', 'COMSPEC', 'HOME', 'USERPROFILE', 'TEMP', 'TMP', 'TMPDIR')}


def check_node(run=subprocess.run, env=None):
    result = run(['node', '--version'], capture_output=True, text=True, timeout=10,
                 env=clean_environment(env or os.environ), check=True)
    if not re.fullmatch(r'v24\.\d+\.\d+\s*', result.stdout):
        raise ValueError('DEPENDENCY_FAILED')


def check_imports(code, run=subprocess.run, env=None):
    result = run(['node', '--import', 'tsx', '--input-type=module', '-e', IMPORT_CHECK],
                 cwd=Path(code).resolve(), env=clean_environment(env or os.environ),
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
    if result.returncode != 0:
        raise ValueError('DEPENDENCY_FAILED')


def report(env):
    checkout = all(env.get(k) == 'success' for k in ('PLAN_OUTCOME', 'CHECKOUT_OUTCOME', 'HEAD_OUTCOME'))
    deps = all(env.get(k) == 'success' for k in ('NODE_SETUP_OUTCOME', 'NODE_CHECK_OUTCOME', 'DEPENDENCY_OUTCOME', 'IMPORT_OUTCOME'))
    return {'schema_version': 'biglotto-capture-code-preflight-v1',
            'status': 'PREFLIGHT_SUCCESS' if checkout and deps else 'CHECKOUT_FAILED' if not checkout else 'DEPENDENCY_FAILED',
            'capture_repository': SOURCE_REPO, 'capture_commit': source_config()['commit'],
            'official_sync_executed': False, 'capture_executed': False, 'auto_publish': False}


def main():
    operation = None
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('operation', choices=('plan', 'head', 'node', 'imports', 'report'))
        parser.add_argument('--code', default='_research-code')
        args = parser.parse_args()
        operation = args.operation
        context(os.environ)  # Separate legitimate workflow identity; no producer() impersonation.
        if operation == 'report':
            value = report(os.environ)
            print(json.dumps(value, sort_keys=True))
            if os.environ.get('GITHUB_STEP_SUMMARY'):
                with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as file:
                    file.write('### Capture code preflight\n\n```json\n' + json.dumps(value, sort_keys=True) + '\n```\n')
            return 0 if value['status'] == 'PREFLIGHT_SUCCESS' else 1
        if operation == 'plan':
            config = source_config()
            if not config['commit'] or os.environ.get('CAPTURE_CODE_TOKEN_AVAILABLE') != 'true':
                raise ValueError('CHECKOUT_FAILED')
            emit({'code_commit': config['commit'], 'status': 'PREFLIGHT_PLAN_READY'})
        elif operation == 'head':
            verify_checkout(Path(args.code).resolve(), source_config()['commit'])
            emit({'status': 'PREFLIGHT_HEAD_VERIFIED'})
        elif operation == 'node':
            check_node()
            emit({'status': 'PREFLIGHT_NODE_VERIFIED'})
        else:
            check_imports(args.code)
            emit({'status': 'PREFLIGHT_IMPORTS_VERIFIED'})
        return 0
    except BaseException:
        print(json.dumps({'status': 'DEPENDENCY_FAILED' if operation in ('node', 'imports') else 'CHECKOUT_FAILED',
                          'official_sync_executed': False, 'capture_executed': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
