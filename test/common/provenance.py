"""Capture available source, dependency and build identities for a new run."""
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def capture_test13(directory, settings, profiles=None, extra_sources=()):
    paths = [ROOT / 'platformio.ini', Path(__file__),
             ROOT / 'test/test_11/test_11_ble_pc.py',
             ROOT / 'test/test_11/test_11_monitor_servers.py',
             ROOT / 'test/test_12/test_12_ble_pc.py',
             ROOT / 'test/test_13/test_13_ble_modem.py']
    paths += list((ROOT / 'src/test_13').rglob('*.cpp'))
    paths += list((ROOT / 'src/test_13').rglob('*.h'))
    paths += [Path(p) for p in extra_sources]
    sources = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    packages = {}
    for package in ('pyserial', 'bleak', 'matplotlib'):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    builds = {}
    for profile in profiles or ('esp_esp_ble_modem', 'esp_esp_ble_car_2', 'esp_esp_ble_car_3'):
        binary = ROOT / '.pio/build' / profile / 'firmware.bin'
        if binary.is_file():
            destination = directory / 'firmware' / (profile + '.bin')
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(binary, destination)
            builds[profile] = {'file': destination.relative_to(directory).as_posix(),
                               'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}
    manifest = {'python': platform.python_version(), 'python_executable': sys.executable,
                'platform': platform.platform(), 'packages': packages, 'settings': settings,
                'source_sha256': sources, 'available_builds': builds,
                'build_note': 'Copies of available PlatformIO builds; not firmware read back from the boards.'}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
