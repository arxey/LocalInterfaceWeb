"""Integration test of a fresh offline archive using its own embedded Python."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def wait_ready(bundle, process):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Portable launcher exited; inspect integration-launch.log and data/logs')
        state = bundle / 'data/running.json'
        try:
            value = json.loads(state.read_text(encoding='utf-8'))
            if value.get('status') == 'ready':
                return value['url']
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError('Portable application did not become ready')


def start(bundle):
    env = os.environ | {
        'PATH': str(Path(os.environ['SystemRoot']) / 'System32'),
        'CI': 'true', 'PIP_NO_INDEX': '1', 'PYTHONHOME': 'Z:/not-installed',
        'PYTHONPATH': 'Z:/not-installed', 'USE_SQLITE_FOR_TESTS': '1',
        'POSTGRES_HOST': '192.0.2.1', 'POSTGRES_PORT': '1', 'DJANGO_DEBUG': '1',
        'HTTP_PROXY': 'http://127.0.0.1:9', 'HTTPS_PROXY': 'http://127.0.0.1:9',
        'NO_PROXY': 'localhost,127.0.0.1',
    }
    log = (bundle / 'integration-launch.log').open('ab', buffering=0)
    process = subprocess.Popen([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
                                str(bundle / 'start-portable.cmd'), '--headless'],
                               cwd=bundle, env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    log.close()
    try:
        return process, wait_ready(bundle, process), env
    except BaseException:
        if process.poll() is None:
            stop(bundle, process, env)
        raise


def verify_local_crt(bundle, env):
    script = r'''
    $ErrorActionPreference = 'Stop'
    $prefix = [IO.Path]::GetFullPath($env:CHECK_BUNDLE)
    $program = Join-Path $prefix 'runtime\postgresql\bin\postgres.exe'
    $servers = @(Get-Process postgres -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $program })
    if (-not $servers.Count) { throw 'No owned PostgreSQL process found' }
    $modules = @($servers[0].Modules | Where-Object { $_.ModuleName -match '^(msvcp140|vcruntime140)' })
    if (-not $modules.Count) { throw 'No Microsoft CRT modules found' }
    foreach ($module in $modules) {
      if (-not $module.FileName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw ('CRT was loaded outside the bundle: ' + $module.FileName)
      }
    }
    Write-Output ('App-local CRT verified: ' + (($modules | ForEach-Object ModuleName) -join ', '))
    '''
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.run([str(shell), '-NoProfile', '-Command', script],
                   env=env | {'CHECK_BUNDLE': str(bundle)}, check=True, timeout=15)


def stop(bundle, process, env):
    result = subprocess.run([str(bundle / 'runtime/python/python.exe'), '-I', '-B',
                             str(bundle / 'portable/launcher.py'), '--stop', '--headless'],
                            cwd=bundle, env=env, timeout=55)
    if result.returncode:
        raise RuntimeError('Portable shutdown failed')
    if process.wait(timeout=15):
        raise RuntimeError('Portable launcher exited with an error')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    if not (bundle / 'runtime/python/python.exe').is_file() or (bundle / 'data').exists():
        raise RuntimeError('Use a fresh built bundle, never a user installation')
    # This test owns only this new build directory; relocation stays in its parent.
    moved = bundle.parent / (bundle.name + ' перенос с пробелами')
    if moved.exists() or moved.resolve().parent != bundle.parent:
        raise RuntimeError('Relocation target must be fresh and stay inside the build directory')
    sys.path.insert(0, str(bundle / 'scripts'))
    from smoke import Client, exercise, verify
    process, url, env = start(bundle)
    state = bundle / 'data/integration-state.json'
    try:
        verify_local_crt(bundle, env)
        config = json.loads((bundle / 'data/config.json').read_text(encoding='utf-8'))
        credentials = {'ADMIN_USERNAME': config['admin_username'], 'ADMIN_PASSWORD': config['admin_password']}
        exercise(Client(url), credentials, state)
        # A second start exits safely, without owning/stopping the first instance.
        second = subprocess.run([str(bundle / 'runtime/python/python.exe'), '-I', '-B',
                                 str(bundle / 'portable/launcher.py'), '--headless'], env=env, timeout=15)
        if second.returncode:
            raise RuntimeError('Second start did not recognize the active instance')
        before = (bundle / 'data/config.json').read_bytes()
        print('Native fresh startup/HTTP/duplicate-start PASS', flush=True)
    finally:
        stop(bundle, process, env)
    bundle.rename(moved)
    try:
        process, url, env = start(moved)
        try:
            if (moved / 'data/config.json').read_bytes() != before:
                # Port fallback is allowed, secrets must remain unchanged.
                after = json.loads((moved / 'data/config.json').read_text(encoding='utf-8'))
                for key in ('django_secret', 'database_password', 'superuser_password', 'admin_password'):
                    if after[key] != config[key]:
                        raise RuntimeError('A persisted secret changed after relocation')
            verify(Client(url), credentials, moved / 'data/integration-state.json')
            print('Offline relocation/restart/persistence PASS', flush=True)
        finally:
            stop(moved, process, env)
    finally:
        moved.rename(bundle)


if __name__ == '__main__':
    main()
