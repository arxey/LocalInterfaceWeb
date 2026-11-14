"""Extract the pinned Microsoft x64 CRT for a Windows portable build.

This build tool reads the signed redistributable as data. It runs only Windows'
expand.exe on embedded cabinets; it never launches the EXE, MSI, or an installer.
Runtime DLL redistribution remains subject to Microsoft's applicable terms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile


VCREDIST_VERSION = '14.52.37029.0'
VCREDIST_URL = (
    'https://download.visualstudio.microsoft.com/download/pr/'
    '78d061c5-9ef3-42ab-98ed-3309d1095dc4/'
    '9C02C9ED120F671B900E6B9471D890E3B2414117A7E53F4AE2E855466FA2B7C9/'
    'VC_redist.x64.exe'
)
VCREDIST_SHA256 = '9c02c9ed120f671b900e6b9471d890e3b2414117a7e53f4ae2e855466fa2b7c9'
LICENSE_URL = (
    'https://visualstudio.microsoft.com/wp-content/uploads/2025/10/'
    'Visual-C-V14-License-Redistributable_and_Runtime_ENU.docx'
)
LICENSE_SHA256 = '08651651a7602fc7c0e2763de0fde1ff9f868df2780597cd1775ee9d6441c783'
CRT_DLLS = frozenset({
    'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vcamp140.dll',
    'vccorlib140.dll', 'vcomp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
    'vcruntime140_threads.dll',
})
BURN_NAMESPACE = '{http://schemas.microsoft.com/wix/2008/Burn}'


def cabinet_members(data: bytes) -> list[str]:
    """Read the member table before passing a cabinet to Windows expand.exe."""
    if len(data) < 36 or data[:4] != b'MSCF':
        raise ValueError('Invalid cabinet header.')
    size, table = struct.unpack_from('<I4xI', data, 8)
    count, flags = struct.unpack_from('<HH', data, 28)
    # Signed cabinets have a reserved area (flag 4) and a signature after cbCabinet.
    if size > len(data) or flags & ~4 or not 36 <= table < size:
        raise ValueError('Unsupported cabinet layout.')
    members = []
    offset = table
    for _ in range(count):
        if offset + 16 > len(data):
            raise ValueError('Truncated cabinet file table.')
        end = data.find(b'\0', offset + 16)
        if end < 0:
            raise ValueError('Unterminated cabinet filename.')
        name = data[offset + 16:end].decode('ascii')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
            raise ValueError(f'Unsafe cabinet member: {name!r}')
        if name.casefold() in {member.casefold() for member in members}:
            raise ValueError(f'Duplicate cabinet member: {name!r}')
        members.append(name)
        offset = end + 1
    return members


def embedded_cabinets(data: bytes) -> list[bytes]:
    """Find validated complete cabinets in the pinned WiX Burn executable."""
    cabinets = []
    offset = 0
    while (offset := data.find(b'MSCF', offset)) >= 0:
        if offset + 36 <= len(data):
            size = struct.unpack_from('<I', data, offset + 8)[0]
            if size >= 36 and offset + size <= len(data):
                candidate = data[offset:offset + size]
                cabinet_members(candidate)
                cabinets.append(candidate)
                offset += size
                continue
        offset += 4
    if len(cabinets) != 2:
        raise ValueError('Expected two embedded cabinets in the pinned redistributable.')
    return cabinets


def _expand(data: bytes, root: Path, name: str) -> Path:
    cabinet_members(data)
    cabinet = root / f'{name}.cab'
    cabinet.write_bytes(data)
    destination = root / name
    destination.mkdir()
    expand = Path(os.environ['SystemRoot']) / 'System32' / 'expand.exe'
    subprocess.run([str(expand), '-F:*', str(cabinet), str(destination)],
                   check=True, capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return destination


def _check_x64_dll(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError(f'Not a PE DLL: {path.name}')
    offset = struct.unpack_from('<I', data, 60)[0]
    if offset + 24 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError(f'Invalid PE header: {path.name}')
    if struct.unpack_from('<H', data, offset + 4)[0] != 0x8664:
        raise ValueError(f'Runtime DLL is not x64: {path.name}')


def extract_vcredist(redist_path: Path | str, output_dir: Path | str,
                     *, license_docx: Path | str | None = None) -> dict:
    """Copy twelve matching x64 CRT DLLs and provenance; optionally add the EULA.

    The input EXE must match VCREDIST_SHA256. Call this on a Windows build host,
    then copy the returned dll_sha256 filenames beside the packaged executables.
    No global runtime installation or administrative rights are required.
    """
    redist_path, output_dir = Path(redist_path), Path(output_dir)
    data = redist_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != VCREDIST_SHA256:
        raise ValueError('SHA256 mismatch for the Microsoft redistributable.')
    if os.name != 'nt':
        raise OSError('Build-time extraction requires Windows expand.exe.')
    eula = Path(license_docx) if license_docx is not None else None
    if eula is not None and hashlib.sha256(eula.read_bytes()).hexdigest() != LICENSE_SHA256:
        raise ValueError('SHA256 mismatch for the Microsoft runtime license.')
    cabinets = embedded_cabinets(data)
    manifest = {
        'version': VCREDIST_VERSION,
        'source_url': VCREDIST_URL,
        'source_sha256': VCREDIST_SHA256,
        'license_url': LICENSE_URL,
        'license_sha256': LICENSE_SHA256,
        'extraction': 'Windows expand.exe; redistributable and MSI never executed',
        'dll_sha256': {},
    }
    with tempfile.TemporaryDirectory(prefix='extract-vcredist-') as temporary:
        work = Path(temporary)
        bootstrapper = _expand(cabinets[0], work, 'bootstrapper')
        payloads = _expand(cabinets[1], work, 'payloads')
        burn = ET.fromstring((bootstrapper / '0').read_bytes())
        registration = burn.find(f'{BURN_NAMESPACE}Registration')
        if registration is None or registration.get('Version') != VCREDIST_VERSION:
            raise ValueError('Unexpected Microsoft redistributable version.')
        payload = next((p for p in burn.findall(f'{BURN_NAMESPACE}Payload')
                        if p.get('FilePath') == r'packages\vcRuntimeMinimum_amd64\cab1.cab'), None)
        if payload is None or payload.get('SourcePath') not in cabinet_members(cabinets[1]):
            raise ValueError('The x64 minimum-runtime cabinet was not found.')
        runtime = _expand((payloads / payload.get('SourcePath')).read_bytes(), work, 'runtime')
        actual = {p.name.removesuffix('_amd64') for p in runtime.iterdir() if p.is_file()}
        if actual != CRT_DLLS:
            raise ValueError(f'Unexpected CRT DLL inventory: {sorted(actual)}')
        for name in sorted(CRT_DLLS):
            source = runtime / f'{name}_amd64'
            _check_x64_dll(source)
            manifest['dll_sha256'][name] = hashlib.sha256(source.read_bytes()).hexdigest()
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(CRT_DLLS):
            shutil.copyfile(runtime / f'{name}_amd64', output_dir / name)
        if eula is not None:
            shutil.copyfile(eula, output_dir / 'MICROSOFT-VC-RUNTIME-LICENSE.docx')
            with zipfile.ZipFile(eula) as document:
                document_root = ET.fromstring(document.read('word/document.xml'))
            word = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            paragraphs = [''.join(t.text or '' for t in p.iter(f'{word}t'))
                          for p in document_root.iter(f'{word}p')]
            (output_dir / 'MICROSOFT-VC-RUNTIME-LICENSE.txt').write_text(
                '\n'.join(paragraphs) + '\n', encoding='utf-8')
        (output_dir / 'vcredist-provenance.json').write_text(
            json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('redist_path', type=Path)
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--license-docx', type=Path)
    args = parser.parse_args()
    try:
        manifest = extract_vcredist(args.redist_path, args.output_dir, license_docx=args.license_docx)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
