# SpaceBallFusion

Ein Add-in für **Autodesk Fusion**, mit dem sich die alte **3Dconnexion SpaceBall 5000 USB** wieder als 3D-Maus nutzen lässt – ganz ohne 3Dconnexion-Treiber.

3Dconnexion unterstützt die SpaceBall 5000 nicht mehr: Der letzte Treiber (3DxSoftware 3.7.22, 2010) ist mit Fusion nicht kompatibel, und der aktuelle 3DxWare 10 erkennt das Gerät nicht. Dieses Add-in liest die Kugel deshalb direkt über USB (Windows-HID) aus und steuert damit die Kamera in Fusion.

## Funktionen

- **6 Achsen + 12 Tasten** direkt über USB, ohne zusätzliche Python-Pakete (nur `ctypes`)
- **11 Navigations-Modi**, u. a. Drehteller (Horizont bleibt gerade), Objekt frei, Kamera, Joystick, Flug, nur Drehen, nur Verschieben/Zoom – plus ein **benutzerdefinierter Modus**, in dem jede Achse frei einer Funktion zugeordnet wird
- **Einstellungs-Dialog in Fusion** mit Live-Vorschau: Tempo, Totzone, Kennlinie, Glättung, Achsenfilter, Dominant-Modus, Richtungsumkehr, Tastenbelegung
- **Tastenaktionen**: Standardansichten, Einpassen, Ortho/Perspektive, 90°-Schritte, Zoomstufen, Ansicht merken, Feinmodus, Moduswechsel – und über 70 **Fusion-Befehle** (Skizze, Extrusion, Abrunden, Messen, Rückgängig, Speichern …)
- **Robust gegen die Eigenheiten der alten Kugel**: Glättung, Dominant-Modus mit Anlaufschwelle (gegen das Mitzucken anderer Achsen), Filter für fehlerhafte USB-Pakete
- **Live-Ansicht im Browser** (`live_view.py`), die alle Sensoren und Tasten in Echtzeit zeigt

## Installation

1. Den Ordner `SpaceBallFusion` (dieses Repository) kopieren nach
   `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\SpaceBallFusion`
2. Fusion starten, **Umschalt+S** → Reiter **Zusatzmodule** → **SpaceBallFusion** → **Ausführen**
   (Haken bei *Beim Start ausführen* setzen)
3. Einstellungen: Arbeitsbereich *Konstruktion* → Reiter **Dienstprogramme** → **SpaceBall Einstellungen**

Nur Windows. Getestet mit der SpaceBall 5000 USB (VID `046D`, PID `C621`).

## Live-Ansicht

```
python live_view.py
```

Dann <http://localhost:8765> im Browser öffnen. Funktioniert parallel zu Fusion.

## Dateien

| Datei | Inhalt |
|---|---|
| `SpaceBallFusion.py` | Add-in: Lesethread, Glättung, Modi, Kamerasteuerung, Tastenaktionen |
| `hidreader.py` | Minimaler Windows-HID-Leser (ctypes) |
| `settings_panel.py` | Einstellungs-Dialog in Fusion |
| `config.json` | Gespeicherte Einstellungen |
| `fusion_actions.json` | Fusion-Befehle, die auf Tasten gelegt werden können |
| `live_view.py` / `live_view.html` | Live-Ansicht der Sensoren |

## Lizenz

[MIT](LICENSE) – frei nutzbar, auch für eigene Projekte, solange der Lizenzhinweis erhalten bleibt.
