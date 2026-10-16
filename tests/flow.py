"""Walk the whole app offscreen with real files and save a screenshot of each step (development only).
Usage: GR4_FIRMWARE=… GR4_LUT=… [GR4_DNG=…] flow.py <folder>"""
import os, time
if __name__ == '__main__':                   # helper processes of the turns import this file: they run nothing
    exec(open(os.path.join(os.path.dirname(__file__), 'drive_app.py'), encoding='utf-8').read())
    from lutsmith_app import logic  # noqa: E402

    FW = os.environ.get('GR4_FIRMWARE')            # the official fwdc248b.bin
    LUT = os.environ.get('GR4_LUT')                 # a .cube LUT
    if not (FW and LUT):
        raise SystemExit('set GR4_FIRMWARE and GR4_LUT (and optionally GR4_DNG)')
    ITERS = int(os.environ.get('ITERS', '60'))
    fit_args = logic.fit_args
    logic.fit_args = lambda *a: (lambda ns: (setattr(ns, 'iters', ITERS), ns)[1])(fit_args(*a))   # quick fits for the walk


    def wait(w, cond, limit=600):
        t0 = time.time()
        while not cond():
            app.processEvents(); time.sleep(0.02)
            if time.time() - t0 > limit:
                raise SystemExit('timed out')


    w = M.Window(); w.resize(1240, 900); w.show()
    if os.environ.get('LANG_UI'):
        w.set_language(os.environ['LANG_UI'])
    if os.environ.get('NIGHT'):
        w.set_theme('night')
    shot(w, 's0-start')
    w.choose_firmware(FW); wait(w, lambda: w.S.fw_status not in ('checking',))
    shot(w, 's1-firmware')
    w.go(1); w.choose_lut(LUT)
    if os.environ.get('GR4_DNG'):
        w.choose_dng(os.environ['GR4_DNG'])
    shot(w, 's2-lut')
    w.go(2); w.set_slot('vivid')
    shot(w, 's3-slot')
    w.go(3); w.S.turns = 1; w.start_fit(); wait(w, lambda: not w.S.fitting)
    shot(w, 's4-fit')
    if os.environ.get('GR4_DNG'):
        wait(w, lambda: w.S.photo is not None and w.S.selected in w.S.photo_cam, 120); w.render()
        shot(w, 's4-fit-photo')
    w.go(4); shot(w, 's5-build')
    w.build([w.current_look()['look']]); wait(w, lambda: w.S.built is not None)
    shot(w, 's5-built')
    print('built sha', w.S.built[1])
