"""Safety and integration checks for the build-time Microsoft CRT extractor."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest

from extract_vcredist import cabinet_members


SCRIPT = Path(__file__).with_name('extract_vcredist.py')
CACHE = Path(os.environ.get('PORTABLE_BUILD_CACHE', SCRIPT.parents[2] / 'portable-cache'))


class ExtractorTests(unittest.TestCase):
    def test_rejects_unsafe_cabinet_filenames_before_native_extraction(self):
        for name in ('../escape.dll', r'..\escape.dll', 'C:escape.dll', '/absolute.dll'):
            with self.subTest(name=name):
                entry = struct.pack('<IIHHHH', 0, 0, 0, 0, 0, 0) + name.encode() + b'\0'
                header = struct.pack('<4s5I2B5H', b'MSCF', 0, 44 + len(entry), 0, 44, 0,
                                     3, 1, 1, 1, 0, 0, 0)
                cabinet = header + bytes(8) + entry
                with self.assertRaisesRegex(ValueError, 'Unsafe cabinet member'):
                    cabinet_members(cabinet)

    def test_rejects_unverified_executable_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'modified.exe'
            archive.write_bytes(b'not a Microsoft redistributable')
            output = root / 'output'
            result = subprocess.run([sys.executable, str(SCRIPT), str(archive), str(output)],
                                    capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SHA256 mismatch', result.stderr)
            self.assertFalse(output.exists())

    @unittest.skipUnless(sys.platform == 'win32' and (CACHE / 'VC_redist.x64.exe').exists(),
                         'Requires the pinned Microsoft redistributable in portable-cache on Windows.')
    def test_real_archive_extracts_matching_x64_runtime_without_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'runtime'
            result = subprocess.run([sys.executable, str(SCRIPT), str(CACHE / 'VC_redist.x64.exe'),
                                     str(output)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((output / 'vcredist-provenance.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['version'], '14.52.37029.0')
            names = {path.name for path in output.glob('*.dll')}
            self.assertEqual(names, {
                'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
                'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vcamp140.dll',
                'vccorlib140.dll', 'vcomp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
                'vcruntime140_threads.dll',
            })
            for name, digest in manifest['dll_sha256'].items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            self.assertFalse(any(output.rglob('*.exe')))
            self.assertFalse(any(output.rglob('*.msi')))


if __name__ == '__main__':
    unittest.main()
