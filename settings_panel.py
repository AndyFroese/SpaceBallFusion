"""Settings dialog for SpaceBallFusion: sliders, direction toggles and button assignment.

Changes apply live while the dialog is open; OK saves config.json, Cancel restores the old values.
"""
import adsk.core
import copy
import json
import os
import traceback

CMD_ID = 'SpaceBallFusion_Settings'
PANEL_ID = 'SolidScriptsAddinsPanel'  # Design workspace > Utilities > Add-Ins
NUM_BUTTONS = 12

ACTIONS = [
    ('none', 'Keine Funktion'),
    ('fit', 'Einpassen'),
    ('home', 'Startansicht'),
    ('iso', 'Iso-Ansicht'),
    ('front', 'Vorne'),
    ('back', 'Hinten'),
    ('top', 'Oben'),
    ('bottom', 'Unten'),
    ('left', 'Links'),
    ('right', 'Rechts'),
    ('level', 'Horizont gerade richten'),
    ('toggle_projection', 'Ortho/Perspektive umschalten'),
    ('rot90_left', '90 Grad links drehen'),
    ('rot90_right', '90 Grad rechts drehen'),
    ('zoom_in', 'Zoom +'),
    ('zoom_out', 'Zoom -'),
    ('save_view', 'Ansicht merken'),
    ('restore_view', 'Gemerkte Ansicht wiederherstellen'),
    ('escape', 'Befehl abbrechen (Esc)'),
    ('mode_meine', 'Zu Modus 1 (Meine Einstellung)'),
    ('fine_toggle', 'Feinmodus an/aus'),
    ('mode_next', 'Naechster Modus'),
    ('mode_prev', 'Vorheriger Modus'),
]

def all_actions():
    """Built-in actions plus Fusion commands listed in fusion_actions.json ([[id, label], ...])."""
    extra = []
    try:
        path = os.path.join(os.path.dirname(__file__), 'fusion_actions.json')
        with open(path, encoding='utf-8') as f:
            extra = [('cmd:' + cid, 'Fusion: ' + label) for cid, label in json.load(f)]
    except (OSError, ValueError):
        pass
    return ACTIONS + extra


MODE_CHOICES = [
    ('meine', '1. Meine Einstellung'),
    ('objekt_frei', '2. Objekt frei (in alle Richtungen)'),
    ('drehteller', '3. Drehteller (Horizont bleibt gerade)'),
    ('drehteller_zoom_z', '4. Drehteller + Zoom durch Ziehen/Druecken'),
    ('kamera', '5. Kamera bewegen (alles umgekehrt)'),
    ('joystick', '6. Joystick (seitlich kippen = drehen)'),
    ('drehteller_mitte', '7. Drehteller um Modellmitte'),
    ('nur_drehen', '8. Nur Drehen (kein Verschieben/Zoom)'),
    ('nur_verschieben', '9. Nur Verschieben + Zoom (2D/Skizze)'),
    ('flug', '10. Flug (umschauen + vorwaerts fliegen)'),
    ('benutzer', '11. Benutzerdefiniert (Achsen selbst belegen)'),
]

AXIS_LABELS = [
    ('tx', 'X: links/rechts schieben'),
    ('ty', 'Y: vor/zurueck schieben'),
    ('tz', 'Z: ziehen/druecken'),
    ('rx', 'Rx: vor/zurueck kippen'),
    ('ry', 'Ry: seitlich kippen'),
    ('rz', 'Rz: drehen (Drehknopf)'),
]
FUNCTION_CHOICES = [
    ('none', 'Keine Funktion'),
    ('pan_x', 'Verschieben links/rechts'),
    ('pan_y', 'Verschieben hoch/runter'),
    ('zoom', 'Zoomen'),
    ('tilt', 'Kippen (vor/zurueck)'),
    ('spin', 'Drehen (um Hochachse)'),
    ('roll', 'Rollen / seitlich kippen (Drehart mit Rollen)'),
]
ROT_CHOICES = [('turntable', 'Drehteller (Horizont gerade)'),
               ('turntable_roll', 'Drehteller + Rollen (seitlich kippen)'),
               ('free', 'Frei (inkl. Rollen)')]
PIVOT_CHOICES = [('target', 'Bildmitte'), ('model', 'Modellmitte')]


def _select(dd, choices, value):
    keys = [k for k, _ in choices]
    dd.listItems.item(keys.index(value) if value in keys else 0).isSelected = True


def _selected(dd, choices):
    sel = dd.selectedItem
    return choices[sel.index][0] if sel else choices[0][0]

FLOATS = [  # key, label, min, max, step
    ('rotate_speed', 'Drehen', 0.02, 1.5, 0.01),
    ('pan_speed', 'Verschieben', 0.02, 1.5, 0.01),
    ('zoom_speed', 'Zoomen', 0.02, 1.5, 0.01),
    ('response_curve', 'Kennlinie (1 = linear, 3 = sehr fein)', 1.0, 3.0, 0.1),
    ('axis_filter', 'Achsenfilter (0 = aus, 1 = nur staerkste Achse)', 0.0, 1.0, 0.05),
    ('dominant_switch', 'Dominant: Achswechsel ab Faktor', 1.2, 5.0, 0.1),
    ('dominant_onset', 'Dominant: Anlaufschwelle (gegen Zucken)', 0.0, 200.0, 5.0),
    ('fine_factor', 'Feinmodus-Tempo (Taste)', 0.05, 1.0, 0.05),
    ('smoothing_alpha', 'Glaettung (klein = weicher)', 0.05, 1.0, 0.05),
    ('release_alpha', 'Stopp beim Loslassen (1 = sofort)', 0.1, 1.0, 0.05),
]

INVERTS = [
    ('invert_pan_x', 'Verschieben links/rechts'),
    ('invert_pan_y', 'Verschieben hoch/runter'),
    ('invert_zoom', 'Zoomen'),
    ('invert_tilt', 'Kippen (vor/zurueck)'),
    ('invert_spin', 'Drehen (um Hochachse)'),
    ('invert_roll', 'Rollen (seitlich)'),
]

main = None  # the add-in module, set by register()
_handlers = []
_ui_state = {'backup': None, 'saved': False, 'button_box': None}


def register(main_module, ui):
    global main
    main = main_module
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, 'SpaceBall Einstellungen', 'Tempo, Richtungen und Tasten der SpaceBall einstellen',
        main.resource_dir())
    on_created = _CreatedHandler()
    cmd_def.commandCreated.add(on_created)
    _handlers.append(on_created)
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    if panel and not panel.controls.itemById(CMD_ID):
        ctrl = panel.controls.addCommand(cmd_def)
        ctrl.isPromoted = True


def unregister(ui):
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    if panel:
        ctrl = panel.controls.itemById(CMD_ID)
        if ctrl:
            ctrl.deleteMe()
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    _handlers.clear()


def is_open():
    return _ui_state['button_box'] is not None


def show_button(number):
    """Called by the add-in when a SpaceBall button is pressed while the dialog is open."""
    box = _ui_state['button_box']
    if box:
        action = main.cfg().get('buttons', {}).get(str(number), 'none')
        label = dict(all_actions()).get(action, action)
        box.formattedText = '<b>Zuletzt gedrueckt: Taste %d</b> (aktuell: %s)' % (number, label)


def _fill(inputs, cfg):
    modes = [m for m, _ in MODE_CHOICES]
    mode = cfg.get('mode', main.DEFAULTS['mode'])
    inputs.itemById('mode').listItems.item(modes.index(mode) if mode in modes else 0).isSelected = True
    amap = cfg.get('axis_map', main.DEFAULTS['axis_map'])
    for axis, _ in AXIS_LABELS:
        _select(inputs.itemById('map_' + axis), FUNCTION_CHOICES, amap.get(axis, 'none'))
    _select(inputs.itemById('custom_rot'), ROT_CHOICES, cfg.get('custom_rot', main.DEFAULTS['custom_rot']))
    _select(inputs.itemById('custom_pivot'), PIVOT_CHOICES, cfg.get('custom_pivot', main.DEFAULTS['custom_pivot']))
    for key, *_ in FLOATS:
        inputs.itemById(key).valueOne = float(cfg.get(key, main.DEFAULTS[key]))
    inputs.itemById('deadzone').valueOne = int(cfg.get('deadzone', main.DEFAULTS['deadzone']))
    inputs.itemById('dominant_mode').value = bool(cfg.get('dominant_mode', main.DEFAULTS['dominant_mode']))
    for key, _ in INVERTS:
        inputs.itemById(key).value = cfg.get(key, 1) == -1
    keys = [a for a, _ in all_actions()]
    for b in range(1, NUM_BUTTONS + 1):
        action = cfg.get('buttons', {}).get(str(b), 'none')
        dd = inputs.itemById('btn%d' % b)
        idx = keys.index(action) if action in keys else 0
        dd.listItems.item(idx).isSelected = True


def _read(inputs):
    cfg = main.cfg()
    sel = inputs.itemById('mode').selectedItem
    cfg['mode'] = MODE_CHOICES[sel.index][0] if sel else 'objekt_frei'
    cfg['axis_map'] = {axis: _selected(inputs.itemById('map_' + axis), FUNCTION_CHOICES) for axis, _ in AXIS_LABELS}
    cfg['custom_rot'] = _selected(inputs.itemById('custom_rot'), ROT_CHOICES)
    cfg['custom_pivot'] = _selected(inputs.itemById('custom_pivot'), PIVOT_CHOICES)
    for key, *_ in FLOATS:
        cfg[key] = round(inputs.itemById(key).valueOne, 3)
    cfg['deadzone'] = int(inputs.itemById('deadzone').valueOne)
    cfg['dominant_mode'] = inputs.itemById('dominant_mode').value
    for key, _ in INVERTS:
        cfg[key] = -1 if inputs.itemById(key).value else 1
    keys = [a for a, _ in all_actions()]
    buttons = {}
    for b in range(1, NUM_BUTTONS + 1):
        sel = inputs.itemById('btn%d' % b).selectedItem
        buttons[str(b)] = keys[sel.index] if sel else 'none'
    cfg['buttons'] = buttons


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            cmd.okButtonText = 'Speichern'
            cmd.setDialogMinimumSize(380, 500)
            _ui_state['backup'] = copy.deepcopy(main.cfg())
            _ui_state['saved'] = False
            inputs = cmd.commandInputs

            mode_dd = inputs.addDropDownCommandInput('mode', 'Modus', adsk.core.DropDownStyles.TextListDropDownStyle)
            for _, label in MODE_CHOICES:
                mode_dd.listItems.add(label, False, '')

            gc = inputs.addGroupCommandInput('grp_custom', 'Benutzerdefinierter Modus (Modus 11)')
            gc.isExpanded = main.cfg().get('mode') == 'benutzer'
            gcc = gc.children
            for axis, label in AXIS_LABELS:
                dd = gcc.addDropDownCommandInput('map_' + axis, label, adsk.core.DropDownStyles.TextListDropDownStyle)
                for _, flabel in FUNCTION_CHOICES:
                    dd.listItems.add(flabel, False, '')
            for key, label, choices in (('custom_rot', 'Drehart', ROT_CHOICES), ('custom_pivot', 'Drehpunkt', PIVOT_CHOICES)):
                dd = gcc.addDropDownCommandInput(key, label, adsk.core.DropDownStyles.TextListDropDownStyle)
                for _, clabel in choices:
                    dd.listItems.add(clabel, False, '')

            g = inputs.addGroupCommandInput('grp_speed', 'Tempo').children
            for key, label, lo, hi, step in FLOATS[:3]:
                s = g.addFloatSliderCommandInput(key, label, '', lo, hi, False)
                s.spinStep = step
            g2 = inputs.addGroupCommandInput('grp_feel', 'Gefuehl').children
            g2.addBoolValueInput('dominant_mode', 'Nur eine Bewegung gleichzeitig (Dominant)', True, '', True)
            g2.addIntegerSliderCommandInput('deadzone', 'Totzone', 0, 120)
            for key, label, lo, hi, step in FLOATS[3:]:
                s = g2.addFloatSliderCommandInput(key, label, '', lo, hi, False)
                s.spinStep = step

            g3 = inputs.addGroupCommandInput('grp_inv', 'Richtung umkehren').children
            for key, label in INVERTS:
                g3.addBoolValueInput(key, label, True, '', False)

            grp_btn = inputs.addGroupCommandInput('grp_btn', 'Tasten')
            g4 = grp_btn.children
            _ui_state['button_box'] = g4.addTextBoxCommandInput(
                'btn_info', '', 'Druecke eine SpaceBall-Taste, um ihre Nummer zu sehen.', 2, True)
            for b in range(1, NUM_BUTTONS + 1):
                dd = g4.addDropDownCommandInput('btn%d' % b, 'Taste %d' % b,
                                                adsk.core.DropDownStyles.TextListDropDownStyle)
                for _, label in all_actions():
                    dd.listItems.add(label, False, '')

            inputs.addBoolValueInput('reset', 'Standardwerte laden (Meine Einstellung)', False, '', False)

            _fill(inputs, main.cfg())

            for event, handler in ((cmd.inputChanged, _ChangedHandler()),
                                   (cmd.execute, _ExecuteHandler()),
                                   (cmd.destroy, _DestroyHandler())):
                event.add(handler)
                _handlers.append(handler)
        except Exception:
            main.ui.messageBox('SpaceBall Einstellungen:\n' + traceback.format_exc())


class _ChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            # args.inputs only holds the changed input's group; use the command's full input set
            inputs = args.firingEvent.sender.commandInputs
            if args.input.id == 'reset':
                defaults = copy.deepcopy(main.DEFAULTS)
                _fill(inputs, defaults)
            _read(inputs)  # live preview
            if args.input.id == 'mode':
                inputs.itemById('grp_custom').isExpanded = main.cfg().get('mode') == 'benutzer'
        except Exception:
            main.ui.messageBox('SpaceBall Einstellungen:\n' + traceback.format_exc())


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _read(args.command.commandInputs)
            main.save_config()
            _ui_state['saved'] = True
        except Exception:
            main.ui.messageBox('Speichern fehlgeschlagen:\n' + traceback.format_exc())


class _DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        _ui_state['button_box'] = None
        if not _ui_state['saved'] and _ui_state['backup'] is not None:
            main.cfg().clear()
            main.cfg().update(_ui_state['backup'])  # Cancel -> back to the old values
        _ui_state['backup'] = None
