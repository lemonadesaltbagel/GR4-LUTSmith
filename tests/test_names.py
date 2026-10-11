"""The name checks for a slot's look:  uv run python tests/test_names.py
The camera shows a name's characters only when its menu language uses that script; a name longer than the slot
takes or wider than its screen hint is refused or warned.  ASCII needs no camera language, so no question."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gr4_lutsmith as gl  # noqa: E402

failures = []


def check(name, ok, detail=''):
    print(('ok    ' if ok else 'FAIL  ') + name + (f' — {detail}' if detail and not ok else ''))
    if not ok:
        failures.append(name)


# the slots: what the camera's menu shows a name on, in RICOH's order
check('the default slot is high-contrast', gl.DEFAULT_SLOT == 'high-contrast', gl.DEFAULT_SLOT)
check('14 slots in the camera\'s order', len(gl.SLOT_NAMES) == 14, len(gl.SLOT_NAMES))

# slots that cannot be renamed (name_slot_limit is None) are the hidden monochrome Standard look and friends
fixed = [s for s in gl.SLOT_ORDER if gl.name_slot_limit(s) is None]
check('only the shared Standard look cannot be renamed', fixed == ['mono-standard'], fixed)
for s in ['high-contrast', 'vivid', 'retro', 'mono-soft', 'mono-solid']:
    check(f'limit for {s} is a number', isinstance(gl.name_slot_limit(s), int), gl.name_slot_limit(s))
check('high-contrast takes 17 characters', gl.name_slot_limit('high-contrast') == 17, gl.name_slot_limit('high-contrast'))
check('vivid takes 13 characters', gl.name_slot_limit('vivid') == 13, gl.name_slot_limit('vivid'))
check('retro takes 9 characters', gl.name_slot_limit('retro') == 9, gl.name_slot_limit('retro'))

# ASCII: no scripts, no bad characters, no camera language needed
check('ASCII scans to no scripts and no bad characters', gl.scan_name('Portra 123') == ({}, ''))
r = gl.name_report('Portra', 'vivid', 'English')
check('an ASCII name has no problem or question', not r['problem'] and not r['question'] and not r['scripts'])
check('an ASCII name keeps the slot\'s limit', r['most'] == 13, r['most'])

# the scripts and the camera languages that show each one
for name, script, langs in [('Müller', 'accented Latin', gl.LATIN_LANGUAGES),
                            ('Плёнка', 'Cyrillic', ('Russian',)),
                            ('ελληνικά', 'Greek', ('Greek',)),
                            ('ไทย', 'Thai', ('Thai',)),
                            ('한국어', 'Korean', ('Korean',)),
                            ('ハッセル', 'Japanese kana', ('Japanese',)),
                            ('東京', 'Chinese characters', ('Japanese', 'Chinese-S', 'Chinese-T'))]:
    found, bad = gl.scan_name(name)
    check(f'{name!r} scans to one script', list(found) == [script] and not bad, (found, bad))
    check(f'{name!r} shows in the right languages', set(found[script]) == set(langs), found)

# a name in one script is fine with the camera set to that language, else it asks
check('Cyrillic name asks in English', gl.name_report('Плёнка', 'vivid', 'English')['question'])
check('Cyrillic name is quiet in Russian', not gl.name_report('Плёнка', 'vivid', 'Russian')['question'])
check('kana name asks in English', gl.name_report('ハッセル', 'vivid', 'English')['question'])
check('kana name is quiet in Japanese', not gl.name_report('ハッセル', 'vivid', 'Japanese')['question'])
check('accented Latin is quiet in English', not gl.name_report('Müller', 'retro', 'English')['question'])
check('accented Latin asks in Thai', gl.name_report('Müller', 'retro', 'Thai')['question'])
both = gl.name_report('Плёнка ハ', 'vivid', 'English')
check('Cyrillic + kana has no language showing all of it', not both['languages'], both['languages'])
check('Cyrillic + kana still asks in English', both['question'])

# characters no camera language shows are refused, and the space rule
check('emoji is a characters problem', gl.name_report('Film 😀', 'vivid', 'English')['problem'] == 'characters')
check('the emoji is reported', gl.name_report('Film 😀', 'vivid', 'English')['detail'] == '😀',
      repr(gl.name_report('Film 😀', 'vivid', 'English')['detail']))
check('leading space is a space problem', gl.name_report(' x', 'vivid', 'English')['problem'] == 'space')
check('trailing space is a space problem', gl.name_report('x ', 'vivid', 'English')['problem'] == 'space')
check('an empty name is a space problem', gl.name_report('', 'vivid', 'English')['problem'] == 'space')

# length: the slot's limit is per language, and the width hint cuts wide names off early
check('at the limit is fine', not gl.name_report('x' * 13, 'vivid', 'English')['problem'])
check('one past the limit is too long', gl.name_report('x' * 14, 'vivid', 'English')['problem'] == 'length')
check('the length detail is the count', gl.name_report('x' * 14, 'vivid', 'English')['detail'] == 14)
r = gl.name_report('一二三四五六七八九', 'vivid', 'English')   # 9 wide characters = 18 columns > the 16-column hint
check('a wide CJK name is warned even under the limit', not r['problem'] and r['long'], r)
check('the width hint is 16 columns', gl.SLOT_DATA['names']['display_hint'] == 16, gl.SLOT_DATA['names']['display_hint'])
check('half-width kana counts as narrow', not gl.name_report('ｶﾀｶﾅ', 'vivid', 'English')['long'])

# the fixed slot refuses every name
r = gl.name_report('Portra', 'mono-standard', 'English')
check('the shared Standard look cannot be renamed', r['problem'] == 'fixed' and r['most'] is None, r)

# check_name refuses each problem and returns notes and a question otherwise
for name, slot in [('Film 😀', 'vivid'), (' x', 'vivid'), ('x' * 14, 'vivid'), ('Portra', 'mono-standard')]:
    try:
        gl.check_name(name, slot, 'English')
        raised = False
    except gl.Refused:
        raised = True
    check(f'check_name refuses {name!r} on {slot}', raised)

notes, q = gl.check_name('Плёнка', 'vivid', 'Russian')
check('accepted Cyrillic returns notes, no question', not q and notes and 'Cyrillic' in notes[0], (notes, q))
notes, q = gl.check_name('Плёнка', 'vivid', 'English')
check('a script the camera may miss asks a question', q and not notes, (notes, q))
notes, q = gl.check_name('一二三四五六七八九', 'vivid', 'English')
check('a wide name is noted, and a script asks', any('long' in n for n in notes) and q, (notes, q))
notes, q = gl.check_name('Portra', 'vivid', 'English')
check('a clean name has no notes or question', notes == [] and q is None, (notes, q))

# system_language: the camera language to assume comes from the environment
def lang_of(**env):
    e = dict(os.environ)
    for k, v in env.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    r = subprocess.run([sys.executable, '-c', f'import sys; sys.path.insert(0, r"{ROOT}");'
                       'import gr4_lutsmith as g; print(g.system_language())'],
                       capture_output=True, encoding='utf-8', env=e)
    return r.stdout.strip()


for code, want in [('ja_JP', 'Japanese'), ('zh_CN', 'Chinese-S'), ('zh_TW', 'Chinese-T'), ('zh_HK', 'Chinese-T'),
                   ('de_AT', 'German'), ('C', 'English')]:
    check(f'LC_ALL={code} is {want}', lang_of(LC_ALL=f'{code}.UTF-8', LANG='C') == want, lang_of(LC_ALL=f'{code}.UTF-8', LANG='C'))
check('LC_ALL beats LANG', lang_of(LC_ALL='de_AT.UTF-8', LANG='ja_JP.UTF-8') == 'German',
      lang_of(LC_ALL='de_AT.UTF-8', LANG='ja_JP.UTF-8'))
check('with no LANG and no LC_ALL it falls back to English', lang_of(LC_ALL=None, LANG=None) == 'English',
      lang_of(LC_ALL=None, LANG=None))

print('\n' + ('all checks passed' if not failures else f'{len(failures)} check(s) failed'))
sys.exit(1 if failures else 0)
