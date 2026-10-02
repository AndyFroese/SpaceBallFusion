"""Fusion add-in: navigate the viewport with a 3Dconnexion SpaceBall 5000 USB without 3DxWare.

A background thread reads the HID reports; Fusion's camera may only be touched on the main
thread, so the thread fires a custom event and the handler applies the motion.
"""
import adsk.core
import json
import math
import os
import sys
import threading
import time
import traceback

import importlib  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
import hidreader  # noqa: E402
import settings_panel  # noqa: E402

# Fusion keeps imported modules across add-in restarts; reload so edits take effect
importlib.reload(hidreader)
importlib.reload(settings_panel)

EVENT_ID = 'SpaceBallFusion_Motion'
FULL_SCALE = 350.0  # same normalisation as Bambu Studio / PrusaSlicer (raw / 350)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

# "Meine Einstellung": the user's tuned custom mapping (2026-10-02), fixed so that editing the
# custom mode (Benutzerdefiniert) does not change it
MY_PRESET = {
    'axis_map': {'tx': 'pan_x', 'ty': 'pan_y', 'tz': 'none', 'rx': 'tilt', 'ry': 'zoom', 'rz': 'spin'},
    'custom_rot': 'turntable', 'custom_pivot': 'target',
}

# Defaults = the user's tuned settings ("Meine Einstellung", saved 2026-10-02).
DEFAULTS = {
    'pan_speed': 0.437, 'zoom_speed': 0.635, 'rotate_speed': 0.771,
    'deadzone': 11, 'fps': 60, 'smoothing_alpha': 0.793, 'release_alpha': 0.986, 'response_curve': 1.0,
    'axis_filter': 0.041, 'dominant_mode': False, 'dominant_switch': 2.0, 'dominant_onset': 60, 'mode': 'meine',
    'fine_factor': 0.291,
    # custom mode ('benutzer'): device axis -> Fusion function; starts as a copy of "Meine Einstellung"
    'axis_map': dict(MY_PRESET['axis_map']),
    'custom_rot': MY_PRESET['custom_rot'], 'custom_pivot': MY_PRESET['custom_pivot'],
    'invert_pan_x': -1, 'invert_pan_y': -1, 'invert_zoom': 1,
    'invert_tilt': -1, 'invert_spin': 1, 'invert_roll': 1,
    'buttons': {'1': 'fit', '2': 'home', '3': 'top', '4': 'left', '5': 'mode_next', '6': 'iso',
                '7': 'top', '8': 'home', '9': 'none', '10': 'none', '11': 'none', '12': 'none'},
    'debug_log': False,
}

app = None
ui = None
_handlers = []
_custom_event = None
_stop = threading.Event()
_thread = None
_lock = threading.Lock()
_state = {'axes': [0] * 6, 'buttons_pressed': [], 'busy': False, 'last': time.time()}
_cfg = {}


def _load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    return cfg


def cfg():
    """The live settings dict (shared with the settings dialog)."""
    return _cfg


def save_config():
    data = {k: v for k, v in _cfg.items() if not k.startswith('_')}
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent='\t', ensure_ascii=False)


def resource_dir():
    return os.path.join(os.path.dirname(__file__), 'resources')


MAX_PLAUSIBLE = 800  # the real ball peaks at ~±450; anything far beyond is a corrupt packet


def _parse(report, axes):
    """Updates axes in place; returns button bitmask or None. Drops implausible packets."""
    rid = report[0]
    if rid in (1, 2) and len(report) >= 7:
        vals = [int.from_bytes(report[1 + 2 * i:3 + 2 * i], 'little', signed=True)
                for i in range(6 if len(report) >= 13 else 3)]
        if any(abs(v) > MAX_PLAUSIBLE for v in vals):
            _state['dropped'] = _state.get('dropped', 0) + 1
            return None
    if rid == 1 and len(report) >= 13:  # translation + rotation in one report
        axes[:] = [int.from_bytes(report[1 + 2 * i:3 + 2 * i], 'little', signed=True) for i in range(6)]
    elif rid == 1 and len(report) >= 7:
        axes[0:3] = [int.from_bytes(report[1 + 2 * i:3 + 2 * i], 'little', signed=True) for i in range(3)]
    elif rid == 2 and len(report) >= 7:
        axes[3:6] = [int.from_bytes(report[1 + 2 * i:3 + 2 * i], 'little', signed=True) for i in range(3)]
    elif rid == 3:
        return int.from_bytes(report[1:3], 'little')  # 12 buttons in the first two bytes
    return None


def _reader():
    """Reads reports as they come and, on a fixed tick, low-pass filters the axes and fires a frame."""
    dev = None
    prev_buttons = 0
    raw = [0] * 6
    smooth = [0.0] * 6
    tick = 1.0 / _cfg.get('fps', 60)
    next_tick = time.perf_counter()
    last_report = 0.0
    while not _stop.is_set():
        try:
            if dev is None:
                try:
                    dev = hidreader.SpaceBall()
                except Exception:
                    time.sleep(2)  # not plugged in yet -> retry
                    continue
            wait_ms = max(1, int((next_tick - time.perf_counter()) * 1000))
            report = dev.read(wait_ms)
            if report:
                last_report = time.perf_counter()
                mask = _parse(report, raw)
                if mask is not None:
                    newly = mask & ~prev_buttons
                    prev_buttons = mask
                    with _lock:
                        for b in range(16):
                            if newly & (1 << b):
                                _state['buttons_pressed'].append(b + 1)
            now = time.perf_counter()
            if now < next_tick:
                continue
            next_tick = max(next_tick + tick, now)
            if now - last_report > 0.08:  # device goes silent when released -> treat as zero
                raw[:] = [0] * 6
            # normal smoothing while held; stop quickly only once the ball is fully let go
            released = not any(raw)
            alpha = _cfg.get('smoothing_alpha', 0.25)  # read live: the settings dialog may change it
            release_alpha = _cfg.get('release_alpha', 0.7)
            for i in range(6):
                smooth[i] += (release_alpha if released else alpha) * (raw[i] - smooth[i])
                if abs(smooth[i]) < 0.5 and raw[i] == 0:
                    smooth[i] = 0.0
            with _lock:
                _state['axes'] = list(smooth)
                moving = any(abs(a) > _cfg.get('deadzone', 15) for a in smooth)
                fire = (moving or _state['buttons_pressed']) and not _state['busy']
                if fire:
                    _state['busy'] = True
            if fire:
                app.fireCustomEvent(EVENT_ID, '')
        except Exception:
            if dev:
                dev.close()
            dev = None
            time.sleep(1)
    if dev:
        dev.close()


def _spin_view(vp, angle):
    """Turn the view around the world's vertical axis through the target (animated)."""
    cam = vp.camera
    wup = _world_up()
    off = _rotate(cam.target.vectorTo(cam.eye), wup, angle)
    cam.eye = _add(cam.target, off)
    cam.upVector = _rotate(cam.upVector, wup, angle)
    cam.isSmoothTransition = True
    vp.camera = cam


def _zoom_step(vp, factor):
    cam = vp.camera
    off = cam.target.vectorTo(cam.eye)
    off.scaleBy(factor)
    cam.eye = _add(cam.target, off)
    cam.viewExtents = cam.viewExtents * factor * factor
    cam.isSmoothTransition = True
    vp.camera = cam


def _apply_view(vp, action):
    if action.startswith('cmd:'):  # any Fusion command by its id, e.g. cmd:SketchCreate
        cmd_def = ui.commandDefinitions.itemById(action[4:])
        if cmd_def:
            cmd_def.execute()
        else:
            app.log('SpaceBallFusion: Befehl nicht gefunden: ' + action[4:])
        return
    if action == 'escape':
        ui.terminateActiveCommand()
        return
    if action == 'toggle_projection':
        cam = vp.camera
        ct = adsk.core.CameraTypes
        cam.cameraType = (ct.PerspectiveCameraType if cam.cameraType == ct.OrthographicCameraType
                          else ct.OrthographicCameraType)
        vp.camera = cam
        return
    if action in ('rot90_left', 'rot90_right'):
        _spin_view(vp, math.pi / 2 * (1 if action == 'rot90_left' else -1))
        return
    if action in ('zoom_in', 'zoom_out'):
        _zoom_step(vp, 0.7 if action == 'zoom_in' else 1 / 0.7)
        return
    if action == 'save_view':
        cam = vp.camera
        _state['saved_view'] = (cam.eye.copy(), cam.target.copy(), cam.upVector.copy(), cam.viewExtents)
        return
    if action == 'restore_view':
        saved = _state.get('saved_view')
        if saved:
            cam = vp.camera
            cam.eye, cam.target, cam.upVector, cam.viewExtents = saved
            cam.isSmoothTransition = True
            vp.camera = cam
        return
    if action == 'mode_meine':
        _cfg['mode'] = 'meine'
        save_config()
        return
    if action == 'fine_toggle':
        _state['fine'] = not _state.get('fine')
        return
    if action == 'level':  # straighten the horizon after rolling
        cam = vp.camera
        look = cam.eye.vectorTo(cam.target)
        look.normalize()
        cam.upVector = _level_up(look, _world_up(), cam.upVector)
        cam.isSmoothTransition = True
        vp.camera = cam
        return
    if action in ('mode_next', 'mode_prev'):
        cur = _cfg.get('mode', 'objekt_frei')
        i = MODE_ORDER.index(cur) if cur in MODE_ORDER else 0
        _cfg['mode'] = MODE_ORDER[(i + (1 if action == 'mode_next' else -1)) % len(MODE_ORDER)]
        save_config()  # remembered, and the live view shows the current mode
        return
    vo = adsk.core.ViewOrientations
    cam = vp.camera
    cam.isSmoothTransition = True
    mapping = {
        'front': vo.FrontViewOrientation, 'back': vo.BackViewOrientation,
        'top': vo.TopViewOrientation, 'bottom': vo.BottomViewOrientation,
        'left': vo.LeftViewOrientation, 'right': vo.RightViewOrientation,
        'iso': vo.IsoTopRightViewOrientation,
    }
    if action == 'fit':
        vp.fit()
        return
    if action == 'home':
        cam.viewOrientation = vo.IsoTopRightViewOrientation
        vp.camera = cam
        vp.fit()
        return
    if action in mapping:
        cam.viewOrientation = mapping[action]
        vp.camera = cam
        vp.fit()


_stats = {'t': 0.0, 'frames': 0, 'cam': 0.0, 'dtmax': 0.0}


def _stats_frame(now, cam_time, dt):
    """Appends frames/s and camera-update cost to stats.log every 2 s of motion (for tuning)."""
    s = _stats
    s['frames'] += 1
    s['cam'] += cam_time
    s['dtmax'] = max(s['dtmax'], dt)
    if now - s['t'] >= 2.0:
        if s['t']:
            line = 'v2 fps=%.1f  kamera_ms=%.1f  max_luecke_ms=%.0f\n' % (
                s['frames'] / (now - s['t']), 1000 * s['cam'] / s['frames'], 1000 * s['dtmax'])
            with open(os.path.join(os.path.dirname(__file__), 'stats.log'), 'a') as f:
                f.write(line)
        s.update(t=now, frames=0, cam=0.0, dtmax=0.0)


def _mode(rot='free', invert=False, zoom_z=False, spin_from='rz', pivot='target', pan=True, zoom=True):
    """rot: free | turntable | look (camera turns in place) | none"""
    return dict(rot=rot, invert=invert, zoom_z=zoom_z, spin_from=spin_from, pivot=pivot, pan=pan, zoom=zoom)


MODE_ORDER = ['meine', 'objekt_frei', 'drehteller', 'drehteller_zoom_z', 'kamera', 'joystick',
              'drehteller_mitte', 'nur_drehen', 'nur_verschieben', 'flug', 'benutzer']
AXIS_NAMES = ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']
FUNCTIONS = ['pan_x', 'pan_y', 'zoom', 'tilt', 'spin', 'roll']  # plus 'none'
MODES = {
    'objekt_frei': _mode(),
    'drehteller': _mode(rot='turntable'),
    'drehteller_zoom_z': _mode(rot='turntable', zoom_z=True),
    'kamera': _mode(invert=True),
    'joystick': _mode(rot='turntable', spin_from='ry'),  # tilt sideways spins, like a joystick
    'drehteller_mitte': _mode(rot='turntable', pivot='model'),
    'nur_drehen': _mode(rot='turntable', pan=False, zoom=False),
    'nur_verschieben': _mode(rot='none'),
    'flug': _mode(rot='look', invert=True),
}


def _custom_setup(mode_name):
    """(axis_map, rotation style, pivot) for the axis-mapped modes."""
    src = MY_PRESET if mode_name == 'meine' else _cfg
    return (src.get('axis_map', DEFAULTS['axis_map']), src.get('custom_rot', 'turntable'),
            src.get('custom_pivot', 'target'))


def _used_axes(mode_name):
    """Which device axes (tx, ty, tz, rx, ry, rz) have a function in the given mode."""
    if mode_name in ('benutzer', 'meine'):
        amap, rot, _ = _custom_setup(mode_name)
        free = rot in ('free', 'turntable_roll')  # roll allowed
        return [amap.get(a, 'none') != 'none' and (amap.get(a) != 'roll' or free) for a in AXIS_NAMES]
    m = MODES.get(mode_name, MODES['objekt_frei'])
    pan, zoom, rot = m['pan'], m['zoom'], m['rot'] != 'none'
    ry_used = rot and (m['spin_from'] == 'ry' or m['rot'] == 'free')
    rz_used = rot and m['spin_from'] == 'rz'
    if m['zoom_z']:
        return [pan, pan, zoom, rot, ry_used, rz_used]
    return [pan, zoom, pan, rot, ry_used, rz_used]


def _model_center():
    """Centre of the design's bounding box, or None (empty design / not a design)."""
    try:
        bb = app.activeProduct.rootComponent.boundingBox
        a, b = bb.minPoint, bb.maxPoint
        if a.distanceTo(b) < 1e-9:
            return None
        return adsk.core.Point3D.create((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
    except Exception:
        return None


def _add(p, v, k=1.0):
    return adsk.core.Point3D.create(p.x + v.x * k, p.y + v.y * k, p.z + v.z * k)


def _rot_all(vec, rots):
    for axis, ang in rots:
        if ang:
            vec = _rotate(vec, axis, ang)
    return vec


def _level_up(look, wup, fallback):
    """Up vector that keeps the horizon level for the given viewing direction."""
    side = look.crossProduct(wup)
    if side.length < 1e-9:
        return fallback
    side.normalize()
    up = side.crossProduct(look)
    up.normalize()
    return up


def _world_up():
    """Vertical axis of the design: Z or Y depending on Fusion's modeling orientation setting."""
    try:
        zup = app.preferences.generalPreferences.defaultModelingOrientation == \
            adsk.core.DefaultModelingOrientations.ZUpModelingOrientation
    except Exception:
        zup = True
    return adsk.core.Vector3D.create(0, 0, 1) if zup else adsk.core.Vector3D.create(0, 1, 0)


def _debug_frame(vp, eye, target, new_eye, new_target, up, wup, axes, n, spin, tilt):
    """One line per frame in debug.log: input, chosen axis, what we set, what Fusion really shows.

    'extern' = how far Fusion's camera at frame start differed from what we set last frame
    (non-zero -> something else moved the camera); 'gesetzt_vs_gelesen' = whether Fusion kept
    the camera we just set.
    """
    def fmt(p):
        return '(%.2f %.2f %.2f)' % (p.x, p.y, p.z)
    prev = _state.get('dbg_last_eye')
    extern = prev.distanceTo(eye) if prev is not None else -1
    got = vp.camera
    kept = got.eye.distanceTo(new_eye)
    look = new_eye.vectorTo(new_target)
    look.normalize()
    names = ['X', 'Y', 'Z', 'Rx', 'Ry', 'Rz']
    la = _state.get('locked_axis')
    line = ('%.3f mode=%s achse=%s roh=%s n=%s spin=%+.4f tilt=%+.4f blick_vertikal=%+.2f '
            'up_vertikal=%+.2f eye=%s->%s extern=%.4f gesetzt_vs_gelesen=%.4f leser=%d verworfen=%d\n') % (
        time.time() % 1000, _cfg.get('mode'), names[la] if la is not None else '-',
        [int(a) for a in axes], [round(v, 2) for v in n], spin, tilt,
        look.dotProduct(wup), up.dotProduct(wup), fmt(eye), fmt(new_eye), extern, kept,
        sum(1 for t in threading.enumerate() if t.name.startswith('SpaceBallReader')), _state.get('dropped', 0))
    _state['dbg_last_eye'] = got.eye
    path = os.path.join(os.path.dirname(__file__), 'debug.log')
    try:
        if os.path.getsize(path) > 5_000_000:
            return
    except OSError:
        pass
    with open(path, 'a') as f:
        f.write(line)


def _rotate(vec, axis, angle):
    m = adsk.core.Matrix3D.create()
    m.setToRotation(angle, axis, adsk.core.Point3D.create(0, 0, 0))
    v = vec.copy()
    v.transformBy(m)
    return v


class MotionHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            with _lock:
                axes = list(_state['axes'])
                presses = _state['buttons_pressed'][:]
                _state['buttons_pressed'].clear()
                now = time.time()
                gap = now - _state['last']
                dt = min(gap, 0.05)  # no jump after idle pauses
                _state['last'] = now
                if gap > 0.15:  # ball was let go in between -> pick dominant axis / pivot anew
                    _state['locked_axis'] = None
                    _state['pivot'] = None
            vp = app.activeViewport
            if vp is None:
                return
            for b in presses:
                if settings_panel.is_open():
                    settings_panel.show_button(b)  # identify buttons instead of acting
                else:
                    _apply_view(vp, _cfg.get('buttons', {}).get(str(b), 'none'))

            # axes without a function in the current mode must not win the dominant-axis choice
            used = _used_axes(_cfg.get('mode', 'objekt_frei'))
            axes = [a if u else 0.0 for a, u in zip(axes, used)]
            dz = _cfg.get('deadzone', 15)
            expo = _cfg.get('response_curve', 1.6)
            n = []
            for a in axes:
                v = 0.0 if abs(a) <= dz else min(1.5, (abs(a) - dz) / (FULL_SCALE - dz))
                n.append(math.copysign(v ** expo, a) if v else 0.0)
            # crosstalk filter: the old ball always moves several axes a bit; fade out any axis
            # that is much weaker than the strongest one (0 = off, 1 = strongest axis only)
            ratio = _cfg.get('axis_filter', 0.0)
            peak = max(abs(v) for v in n)
            if _cfg.get('dominant_mode', False) and peak > 0:
                # one axis at a time: keep the axis chosen at the start of a motion until the ball
                # is released, unless another axis becomes clearly stronger (hysteresis)
                best = max(range(6), key=lambda i: abs(n[i]))
                locked = _state.get('locked_axis')
                if locked is None and abs(axes[best]) < _cfg.get('dominant_onset', 60):
                    # start of a motion: the old ball often twitches another axis first (measured:
                    # roll or pan leads a twist by ~30 ms) -> wait until one axis clearly leads
                    return
                if locked is None or abs(n[best]) > _cfg.get('dominant_switch', 2.0) * abs(n[locked]):
                    locked = best
                _state['locked_axis'] = locked
                n = [v if i == locked else 0.0 for i, v in enumerate(n)]
            elif ratio > 0 and peak > 0:
                lo = ratio * peak
                n = [0.0 if abs(v) <= lo else math.copysign((abs(v) - lo) / (peak - lo) * abs(v), v)
                     if abs(v) < peak else v for v in n]
            if not any(n):
                return
            t0 = time.perf_counter()
            mode_name = _cfg.get('mode', 'objekt_frei')
            if mode_name in ('benutzer', 'meine'):
                # axis-mapped modes: every device axis is assigned a Fusion function
                amap, rot, pivot = _custom_setup(mode_name)
                mode = _mode(rot=rot, pivot=pivot)
                f = dict.fromkeys(FUNCTIONS, 0.0)
                for name, v in zip(AXIS_NAMES, n):
                    fn = amap.get(name, 'none')
                    if fn in f:
                        f[fn] += v
                tx, pan_y_in, zoom_in = f['pan_x'], f['pan_y'], f['zoom']
                rx, spin_in, roll_in = f['tilt'], f['spin'], f['roll']
                if mode['rot'] not in ('free', 'turntable_roll'):
                    roll_in = 0.0
            else:
                mode = MODES.get(mode_name, MODES['objekt_frei'])
                if mode['invert']:  # move the camera instead of the object -> every direction flips
                    n = [-v for v in n]
                tx, ty, tz, rx, ry, rz = n
                if mode['zoom_z']:  # zoom by pulling/pushing, pan up/down by pushing forward/back
                    pan_y_in, zoom_in = -ty, -tz
                else:
                    pan_y_in, zoom_in = tz, ty
                if not mode['pan']:
                    tx = pan_y_in = 0.0
                if not mode['zoom']:
                    zoom_in = 0.0
                spin_in = ry if mode['spin_from'] == 'ry' else rz
                roll_in = ry if mode['rot'] == 'free' else 0.0
                if mode['rot'] == 'none':
                    rx = spin_in = roll_in = 0.0
            fine = _cfg.get('fine_factor', 0.3) if _state.get('fine') else 1.0

            cam = vp.camera
            cam.isSmoothTransition = False
            eye, target, up = cam.eye, cam.target, cam.upVector
            fwd = eye.vectorTo(target)
            dist = fwd.length
            fwd.normalize()
            up.normalize()
            right = fwd.crossProduct(up)
            right.normalize()
            up = right.crossProduct(fwd)
            up.normalize()

            # --- pan (device X -> screen X, device Z -> screen Y) ---
            ext = math.sqrt(max(cam.viewExtents, 1e-9))
            k = _cfg.get('pan_speed', 1.0) * ext * dt * 2.0 * fine
            px = -tx * _cfg.get('invert_pan_x', 1) * k
            py = pan_y_in * _cfg.get('invert_pan_y', 1) * k
            shift = adsk.core.Vector3D.create(right.x * px + up.x * py, right.y * px + up.y * py, right.z * px + up.z * py)

            zoom_rate = zoom_in * _cfg.get('invert_zoom', 1) * _cfg.get('zoom_speed', 1.0) * dt * 2.5 * fine
            rs = _cfg.get('rotate_speed', 1.0) * dt * 2.5 * fine
            tilt = -rx * _cfg.get('invert_tilt', 1) * rs
            spin = -spin_in * _cfg.get('invert_spin', 1) * rs
            roll = roll_in * _cfg.get('invert_roll', 1) * rs
            wup = _world_up()

            if mode['rot'] == 'look':
                # flight: the camera turns in place and flies along its viewing direction
                look = eye.vectorTo(target)
                look = _rotate(look, wup, spin)
                side = look.crossProduct(wup)
                if tilt and side.length > 1e-9:
                    pitched = _rotate(look, side, tilt)
                    pn = pitched.copy()
                    pn.normalize()
                    if abs(pn.dotProduct(wup)) < 0.995:
                        look = pitched
                fly = -zoom_rate * dist  # push forward -> fly forward
                new_eye = _add(_add(eye, shift), fwd, fly)
                new_target = _add(new_eye, look)
                ln = look.copy()
                ln.normalize()
                up = _level_up(ln, wup, up)
            else:
                pivot = target
                if mode['pivot'] == 'model':
                    if _state.get('pivot') is None:  # picked once per motion
                        _state['pivot'] = _model_center()
                    pivot = _state['pivot'] or target
                if mode['rot'] in ('turntable', 'turntable_roll'):
                    # spin around the world's vertical axis, tilt around the screen's horizontal
                    # axis; plain turntable keeps the horizon level (like Fusion's own orbit),
                    # turntable_roll additionally rolls around the viewing axis (tilt sideways)
                    side = right.copy()
                    rots = [(wup, spin)]
                    side = _rotate(side, wup, spin)
                    rots_t = rots + [(side, tilt)]
                    look = _rot_all(fwd, rots_t)
                    if abs(look.dotProduct(wup)) < 0.995:  # don't flip over the pole
                        rots = rots_t
                    if mode['rot'] == 'turntable_roll' and roll:
                        rots.append((_rot_all(fwd, rots), roll))
                else:
                    rots = [(right, tilt), (up, spin), (fwd, roll)]
                new_eye = _add(pivot, _rot_all(pivot.vectorTo(eye), rots))
                new_target = _add(pivot, _rot_all(pivot.vectorTo(target), rots))
                look = new_eye.vectorTo(new_target)
                look.normalize()
                up = _level_up(look, wup, up) if mode['rot'] == 'turntable' else _rot_all(up, rots)
                # zoom: move the eye towards/away from the target
                zoom = math.exp(zoom_rate)
                offset = new_target.vectorTo(new_eye)
                offset.scaleBy(zoom)
                new_target = _add(new_target, shift)
                new_eye = _add(new_target, offset)
                cam.viewExtents = cam.viewExtents * zoom * zoom

            cam.target = new_target
            cam.eye = new_eye
            cam.upVector = up
            vp.camera = cam
            vp.refresh()  # otherwise Fusion batches camera changes and repaints only now and then
            _stats_frame(now, time.perf_counter() - t0, dt)
            if _cfg.get('debug_log'):
                _debug_frame(vp, eye, target, new_eye, new_target, up, wup, axes, n, spin, tilt)
        except Exception:
            app.log('SpaceBallFusion: ' + traceback.format_exc())
        finally:
            with _lock:
                _state['busy'] = False


def _dump_commands():
    """Writes all Fusion command ids + names to commands.txt (to pick button actions from)."""
    try:
        defs = ui.commandDefinitions
        lines = []
        for i in range(defs.count):
            d = defs.item(i)
            try:
                lines.append('%s\t%s' % (d.id, d.name))
            except Exception:
                pass
        with open(os.path.join(os.path.dirname(__file__), 'commands.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(sorted(lines)))
    except Exception:
        app.log('SpaceBallFusion: commands.txt: ' + traceback.format_exc())


def run(context):
    global app, ui, _custom_event, _thread, _cfg
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        _cfg = _load_config()
        # Fusion keeps old module instances alive (e.g. "Run" pressed without "Stop", or a reload);
        # their reader threads would keep feeding the camera. Stop any previous instance first.
        running = sys.__dict__.setdefault('_spaceball_fusion_instances', [])
        for old_stop, old_thread in running:
            old_stop.set()
            old_thread.join(1.0)
        running.clear()
        try:
            app.unregisterCustomEvent(EVENT_ID)  # drops handlers of an old instance, if any
        except Exception:
            pass
        _custom_event = app.registerCustomEvent(EVENT_ID)
        h = MotionHandler()
        _custom_event.add(h)
        _handlers.append(h)
        _stop.clear()
        _thread = threading.Thread(target=_reader, daemon=True, name='SpaceBallReader')
        _thread.start()
        running.append((_stop, _thread))
        settings_panel.register(sys.modules[__name__], ui)
        _dump_commands()
    except Exception:
        if ui:
            ui.messageBox('SpaceBallFusion Start fehlgeschlagen:\n' + traceback.format_exc())


def stop(context):
    try:
        settings_panel.unregister(ui)
        _stop.set()
        if _thread:
            _thread.join(1.0)
        if _custom_event:
            for h in _handlers:
                _custom_event.remove(h)
        _handlers.clear()
        app.unregisterCustomEvent(EVENT_ID)
    except Exception:
        if ui:
            ui.messageBox('SpaceBallFusion Stop fehlgeschlagen:\n' + traceback.format_exc())
