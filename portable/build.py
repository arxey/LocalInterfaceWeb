"""Build a fresh offline Windows x64 distribution. Never runs vendor installers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(source, cache):
    path = cache / source['file']
    if path.is_file() and digest(path) == source['sha256']:
        return path
    temporary = path.with_suffix(path.suffix + '.part')
    for attempt in range(3):
        try:
            request = urllib.request.Request(source['url'], headers={'User-Agent': 'LocalInterfaceWeb-build/1'})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
            if digest(temporary) != source['sha256']:
                raise RuntimeError(f"SHA256 mismatch for {source['file']}; nothing will be executed")
            temporary.replace(path)
            return path
        except (OSError, RuntimeError):
            if temporary.exists():
                temporary.unlink()
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def extract_zip(source, target, select=lambda name: True, strip=0):
    with ZipFile(source) as archive:
        for member in archive.infolist():
            if member.is_dir() or not select(member.filename):
                continue
            parts = Path(member.filename).parts[strip:]
            if not parts:
                continue
            output = target.joinpath(*parts)
            if not output.resolve().is_relative_to(target.resolve()):
                raise RuntimeError('Archive path escapes destination')
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as incoming, output.open('wb') as stream:
                shutil.copyfileobj(incoming, stream)


def copy_sources(destination):
    ignored = shutil.ignore_patterns('__pycache__', '*.pyc', '*.sqlite3', 'node_modules', 'media', 'staticfiles', '*.tsbuildinfo')
    for name in ('backend', 'frontend', 'demo', 'portable', 'docs', 'scripts'):
        shutil.copytree(ROOT / name, destination / name, ignore=ignored)
    for name in ('start-portable.cmd', 'stop-portable.cmd', 'README.md', 'SECURITY.md'):
        shutil.copyfile(ROOT / name, destination / name)
    if not (destination / 'frontend/dist/index.html').is_file():
        raise RuntimeError('Build frontend first: pnpm install --frozen-lockfile && pnpm build')


def build(output, cache, archive_path=None):
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise RuntimeError('Build on 64-bit Windows; the resulting ZIP runs offline without build tools')
    output = output.resolve()
    if output.exists():
        raise RuntimeError('Output already exists. Choose a fresh output directory; existing data is never deleted.')
    cache.mkdir(parents=True, exist_ok=True)
    sources = json.loads((ROOT / 'portable/sources.json').read_text(encoding='utf-8'))
    fetched = {name: download(source, cache) for name, source in sources.items()}
    wheel_sources = json.loads((ROOT / 'portable/wheels.json').read_text(encoding='utf-8'))
    normalized = {name.lower().replace('_', '-'): value['version'] for name, value in wheel_sources.items()}
    for line in (ROOT / 'backend/requirements-portable.txt').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.strip().split('==')
            name = name.split('[', 1)[0].lower().replace('_', '-')
            if normalized.get(name) != version:
                raise RuntimeError('Portable requirements and wheels.json differ; update and audit the wheel lock first')
    wheel_cache = cache / 'wheels'
    wheel_cache.mkdir(exist_ok=True)
    wheels = [download(source, wheel_cache) for source in wheel_sources.values()]
    output.mkdir(parents=True)
    copy_sources(output)
    python = output / 'runtime/python'
    postgres = output / 'runtime/postgresql'
    extract_zip(fetched['python'], python)
    extract_zip(fetched['postgresql'], postgres,
                select=lambda n: n.startswith(('pgsql/bin/', 'pgsql/lib/', 'pgsql/share/'))
                or (n.startswith('pgsql/') and n.count('/') == 1 and 'license' in n.lower()), strip=1)
    pth = next(python.glob('python*._pth'))
    version = pth.stem
    pth.write_text(f'{version}.zip\n.\nLib/site-packages\n../../backend\n../../portable\nimport site\n', encoding='utf-8')
    packages = python / 'Lib/site-packages'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-index', '--no-deps', '--no-compile',
                    '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '3.14',
                    '--implementation', 'cp', '--abi', 'cp314', '--target', str(packages),
                    *[str(wheel) for wheel in wheels]], check=True)
    for metadata in packages.glob('*.dist-info/direct_url.json'):
        metadata.unlink()  # Local build paths are not portable package provenance.
    # The helper reads CAB payloads and uses expand.exe; it never launches the EXE.
    from extract_vcredist import extract_vcredist
    crt = output / 'runtime/crt'
    extract_vcredist(fetched['vcredist'], crt, license_docx=fetched['vcredist_license'])
    for dll in crt.glob('*.dll'):
        shutil.copy2(dll, postgres / 'bin' / dll.name)
        shutil.copy2(dll, python / dll.name)
    shutil.copy2(fetched['vcredist_license'], crt / fetched['vcredist_license'].name)
    shutil.copy2(ROOT / 'portable/sources.json', output / 'runtime/sources.json')
    # Verify the embedded runtime imports its bundled dependencies without PATH.
    clean = os.environ | {'PATH': str(Path(os.environ['SystemRoot']) / 'System32')}
    subprocess.run([str(python / 'python.exe'), '-I', '-c',
                    'import django, psycopg, PIL, waitress; print("Bundled imports OK")'], env=clean, check=True)
    subprocess.run([str(postgres / 'bin/postgres.exe'), '--version'], env=clean, check=True)
    manifest = {str(path.relative_to(output)).replace('\\', '/'): digest(path)
                for path in output.rglob('*') if path.is_file()}
    (output / 'runtime/sha256.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if archive_path:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive_path, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
            for path in output.rglob('*'):
                if path.is_file():
                    archive.write(path, Path('LocalInterfaceWeb') / path.relative_to(output))
        archive_path.with_suffix('.zip.sha256').write_text(f'{digest(archive_path)}  {archive_path.name}\n', encoding='ascii')
        print(f'Offline archive: {archive_path} ({archive_path.stat().st_size} bytes)')
    print(f'Offline bundle ready: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build-portable/LocalInterfaceWeb')
    parser.add_argument('--cache', type=Path, default=ROOT / '.portable-build-cache')
    parser.add_argument('--zip', dest='archive', type=Path)
    args = parser.parse_args()
    build(args.output, args.cache, args.archive)
