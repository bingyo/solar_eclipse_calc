"""Translations of the page (no server, no internet needed).

Every Japanese text of the page must have its translations in static/i18n.js:
  * the keys of t('...') in static/app.js, and the other Japanese strings there (the labels of
    lists such as the time zones and the speeds, given to t() as variables);
  * the fixed texts of static/index.html (text, title / placeholder / aria-label attributes and the
    <title>), which translatePage() looks up in I18N, and the blocks with markup
    (data-i18n-html), which have their own HTML in HTML_BLOCKS;
  * the names the server gives to the page (cities, satellites, ephemeris downloads).
An entry of I18N has the 6 languages (en, fr, ru, es, zh, hi), none empty, each with the {fields}
of the Japanese text.

    python tests/test_ui_i18n.py
"""
import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eclipsecalc.presets import CITIES, SATELLITES  # noqa: E402

LANGS = ('en', 'fr', 'ru', 'es', 'zh', 'hi')
JP = re.compile(r'[぀-ヿ一-鿿＀-￯]')      # JP_TEXT of i18n.js
FIELD = re.compile(r'\{(\w+)\}')
# Japanese strings of app.js that are not shown as text
NOT_SHOWN = {'solar_eclipse_calc 画面'}      # SAVE_TOOL: the name of the tool in saved files
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def load_messages():
    src = (ROOT / 'static' / 'i18n.js').read_text(encoding='utf-8')
    m = re.search(r'/\*BEGIN-MESSAGES\*/(.*?)/\*END-MESSAGES\*/', src, re.S)
    body = re.sub(r',\s*}$', '}', m.group(1).strip())
    return json.loads(body), src


def app_strings():
    """The keys of t('...') and the other Japanese string literals of app.js (outside comments)."""
    src = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    code = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    code = re.sub(r'(^|\s)//[^\n]*', r'\1', code)          # not the // of URLs in strings
    lit = r"'((?:[^'\\\n]|\\.)*)'"
    keys = set(re.findall(r'(?<![\w$.])t\(\s*' + lit, code))
    others = {s for s in re.findall(lit, code) if JP.search(s)} - keys - NOT_SHOWN
    unquote = lambda s: s.replace("\\'", "'").replace('\\\\', '\\')
    return {unquote(k) for k in keys}, {unquote(s) for s in others}


class PageTexts(HTMLParser):
    """The texts of index.html that translatePage() translates, and the data-i18n-html blocks."""

    def __init__(self):
        super().__init__()
        self.texts, self.blocks, self.skip, self.raw, self.title = set(), [], 0, None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip:
            self.skip += tag not in VOID
            return
        if tag in ('script', 'style'):
            self.raw = tag
        self.title = tag == 'title'
        for name in ('title', 'placeholder', 'aria-label'):
            if a.get(name) and JP.search(a[name]):
                self.texts.add(' '.join(a[name].split()))
        if 'data-i18n-html' in a:
            self.blocks.append(a['data-i18n-html'])
            self.skip = 1

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
        elif tag == self.raw:
            self.raw = None
        self.title = False

    def handle_data(self, data):
        if not self.skip and not self.raw and JP.search(data):
            self.texts.add(' '.join(data.split()))


def server_names():
    names = {c[0] for c in CITIES}
    for s in SATELLITES:
        names.update([s['label'], s['spec'].get('name', '')])
    src = (ROOT / 'eclipsecalc' / 'context.py').read_text(encoding='utf-8')
    m = re.search(r'^DOWNLOADABLE = (\{.*?\})$', src, re.M)
    names.update(ast.literal_eval(m.group(1)).values())
    return {n for n in names if n and JP.search(n)}


def check_entry(msgs, key, where):
    v = msgs.get(key)
    if not check(v is not None, f'{where}: no translation of 「{key}」'):
        return
    if not check(isinstance(v, list) and len(v) == len(LANGS) and all(isinstance(x, str) and x.strip() for x in v),
                 f'{where}: 「{key}」 needs {len(LANGS)} non-empty translations ({", ".join(LANGS)})'):
        return
    fields = set(FIELD.findall(key))
    for lang, x in zip(LANGS, v):
        check(set(FIELD.findall(x)) == fields, f'{where}: the {lang} text of 「{key}」 has the fields '
              f'{sorted(set(FIELD.findall(x)))}, not {sorted(fields)}')


def main():
    msgs, i18n_src = load_messages()
    keys, others = app_strings()
    for k in sorted(keys):
        check_entry(msgs, k, 'app.js t()')
    for k in sorted(others):
        check_entry(msgs, k, 'app.js')
    page = PageTexts()
    page.feed((ROOT / 'static' / 'index.html').read_text(encoding='utf-8'))
    for k in sorted(page.texts):
        check_entry(msgs, k, 'index.html')
    check(page.blocks, 'index.html: no data-i18n-html blocks found')
    for name in page.blocks:
        m = re.search(r'^  ' + re.escape(name) + r': \{\n(.*?)\n  \},$', i18n_src, re.S | re.M)
        if check(m, f'HTML_BLOCKS: no block 「{name}」'):
            have = set(re.findall(r'^    (\w\w): `', m.group(1), re.M))
            check(have >= set(LANGS), f'HTML_BLOCKS 「{name}」: missing {sorted(set(LANGS) - have)}')
    for k in sorted(server_names()):
        check_entry(msgs, k, 'server name')
    n = len(keys) + len(others) + len(page.texts)
    if failures:
        for f in failures:
            print('FAIL', f)
        print(f'FAIL test_ui_i18n: {len(failures)} problem(s) in {n} texts')
        return 1
    print(f'PASS test_ui_i18n: {len(keys)} t() keys, {len(others)} other strings of app.js, '
          f'{len(page.texts)} texts and {len(page.blocks)} blocks of index.html have their {len(LANGS)} translations')
    return 0


if __name__ == '__main__':
    sys.exit(main())
