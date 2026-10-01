"""Download a JPL ephemeris into data/.

    python tools/download_ephemeris.py de440     # 1550-2650, about 114 MB
    python tools/download_ephemeris.py de440s    # 1849-2150, about 32 MB (downloaded automatically by run.py)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from eclipsecalc.context import DOWNLOADABLE, ensure_ephemeris  # noqa: E402


def main():
    name = (sys.argv[1] if len(sys.argv) > 1 else 'de440').lower().removesuffix('.bsp') + '.bsp'
    if name not in DOWNLOADABLE:
        print('対応している暦: ' + ', '.join(n.removesuffix('.bsp') for n in DOWNLOADABLE))
        return 1
    ensure_ephemeris(name)
    print('UI の「詳細設定 → 暦」で選択できます。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
