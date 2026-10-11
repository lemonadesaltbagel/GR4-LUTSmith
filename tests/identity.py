"""The app and the command line must build the same firmware, byte for byte, from the same LUT and choices.
Usage: GR4_FIRMWARE=… GR4_LUT=… GR4_DNG=… identity.py <folder>  (full-length fit; takes a few minutes)."""
import os, subprocess, time
if __name__ == '__main__':                   # helper processes of the turns import this file: they run nothing
    exec(open(os.path.join(os.path.dirname(__file__), 'drive_app.py'), encoding='utf-8').read())
    FW = os.environ.get('GR4_FIRMWARE')            # the official fwdc248b.bin
    LUT = os.environ.get('GR4_LUT')                 # a .cube LUT
    DNG = os.environ.get('GR4_DNG')                 # a DNG from a GR IV
    if not (FW and LUT and DNG):
        raise SystemExit('set GR4_FIRMWARE, GR4_LUT and GR4_DNG')
    ROOT = Path(__file__).resolve().parents[1]


    def wait(cond, limit=3600):
        t0 = time.time()
        while not cond():
            app.processEvents(); time.sleep(0.05)
            if time.time() - t0 > limit:
                raise SystemExit('timed out')


    w = M.Window(); w.show()
    w.choose_firmware(FW); wait(lambda: w.S.fw_status != 'checking')
    w.choose_lut(LUT); w.choose_dng(DNG); w.set_slot('vivid'); w.S.name = 'Portra'
    w.S.after, w.S.turns = True, 1                 # one turn, as the command line's fit
    w.start_fit(); wait(lambda: not w.S.fitting)
    look = w.current_look()['look']
    w.build([look]); wait(lambda: w.S.built is not None)
    app_sha = w.S.built[1]
    print('app :', app_sha, '| result', w.S.selected)
    cli = lambda *a: subprocess.run(['uv', 'run', 'gr4_lutsmith.py', *a], cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace')
    r = cli('fit', LUT, '--photo', DNG, '--after-curves', '--slot', 'vivid', '--name', 'Portra', '--camera-language', 'English', '-o', str(OUT / 'cli'))
    rec = 'turns/' + w.S.selected if (OUT / 'cli' / 'turns').exists() else ''
    look_json = OUT / 'cli' / (rec + '/look.json' if rec else 'look.json')
    r = cli('build', str(look_json), FW, '-o', str(OUT / 'cli-build'))
    cli_sha = [l for l in r.stdout.splitlines() if l.startswith('wrote')][0].split('SHA-256 ')[1].rstrip(')')
    print('cli :', cli_sha, '| from', look_json.relative_to(OUT))
    print('IDENTICAL' if app_sha == cli_sha else 'DIFFERENT')
