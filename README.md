# TurboFetch Download Manager

A production-grade, multi-threaded download manager for **Windows**, built with
**Python 3.11 + PyQt6** and packaged as a standalone `.exe` with **PyInstaller**.

![status](https://img.shields.io/badge/status-production-blue)
![python](https://img.shields.io/badge/python-3.11-blue)
![qt](https://img.shields.io/badge/Qt-6.11-green)

## Features

### Download engine
- **Multi-threaded segment downloads** – 8–32 parallel byte-range segments per
  file (server capability detected automatically via `HEAD` / range probe).
- **HTTP / HTTPS / FTP** support with authentication, custom headers,
  user-agent and referer per download.
- **Pause / resume** – progress is persisted per segment, so downloads survive
  app restarts (state in SQLite, part-files on disk).
- **Automatic retries** with backoff for flaky servers (408/429/5xx) and
  connection errors.
- **Speed throttle** – global bandwidth cap applied across all active
  downloads (reconfigurable at runtime).
- **Resume across restarts** – incomplete downloads are restored and resumed
  automatically (configurable).

### Queue & scheduling
- **Priority queue** (high / medium / low) with a configurable maximum number
  of concurrent downloads (default 3).
- **Scheduler** – queue a download for a specific date/time; due downloads are
  promoted automatically.
- Pause all / resume all / cancel all from the menu or toolbar.

### File management
- **Auto-categorize** by extension: documents, music, videos, images,
  compressed, programs, others – with per-category download folders.
- **Duplicate policy** – skip / overwrite / rename (configurable).
- Per-download SHA-256 / MD5 checksums on completion.

### URL input
- Manual entry (Add URL dialog with all advanced options).
- **Clipboard detection** – optional auto-add of URLs found in the clipboard.
- **Batch import** – paste many URLs or load them from a `.txt` / `.url` file.
- **Drag & drop** URLs/files straight onto the window.

### History & statistics
- Searchable, filterable **download history** with **CSV export**.
- Live **speed graph** (120 s window, auto-scaling), per-download speed,
  average speed, ETA, live segment counters.

### Actions & notifications
- Post-download actions: **open file / open folder / shut down PC** (when the
  queue empties).
- Desktop notifications (native Windows toasts) + bundled sound effects.
- **System tray** – minimize to tray, live state (idle / downloading /
  paused), balloon notifications, full context menu.

### Settings
- Folders, max concurrent downloads, segment limits, speed cap, retry count,
  proxy (host/port/auth), user-agent/referer, theme (**dark / light**),
  autostart with Windows, minimize-to-tray, clipboard detection,
  auto-resume, notifications & sounds.

## Requirements

| Component  | Version   |
|------------|-----------|
| Windows    | 10 / 11   |
| Python     | 3.11+     |
| PyQt6      | 6.11      |
| NSIS       | 3.x (installer only) |

## Installation

### Prebuilt (easiest)
Download `TurboFetch-Setup-*.exe` from the
[Releases](../../releases) page and run the installer, or grab the portable
`TurboFetch.exe` and run it – no installation needed.

### From source
```bat
git clone https://github.com/sufyan-vip/My-first-idm.git
cd My-first-idm
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Usage

- **Add a download** – `Ctrl+T`, the toolbar Add button, paste from the clipboard
  (`Ctrl+V`), drag & drop, or enable clipboard auto-detection in Settings.
- **Manage** – single-click to select (multi-select supported), right-click a
  row for pause / resume / cancel / open file / open folder / re-download /
  details / delete.
- **Queue** – use the *Queue* menu for start / pause queue and *History*.
- **Tray** – close the window to keep downloading in the background
  (Settings > Minimize to tray).

### CLI mode (source runs)
```bat
python main.py --url "https://example.com/file.zip" --output D:\Downloads
python main.py --version
```

## Project structure

```
My-first-idm/
├── main.py                  # entry point (GUI + CLI)
├── idm.spec                 # PyInstaller specification
├── requirements.txt
├── core/                    # download engine (no Qt in worker threads)
│   ├── download_engine.py   #   orchestration, 1 Hz tick, queue fill
│   ├── protocol_handler.py  #   HTTP/HTTPS/FTP probe + stream (requests/ftplib)
│   ├── segment_manager.py   #   byte-range planning
│   ├── queue_manager.py     #   priority queue
│   ├── scheduler.py         #   time-based promotion
│   ├── bandwidth_limiter.py #   global token-bucket throttle
│   ├── speed_calculator.py  #   speed / ETA / average
│   ├── resume_manager.py    #   part-file + resume JSON handling
│   ├── proxy_handler.py
│   └── url_parser.py        #   URL extraction / categorization
├── database/
│   ├── db_manager.py        #   SQLite (WAL, single locked connection)
│   ├── migrations.py        #   schema versioning
│   └── models.py            #   dataclasses (Download, Segment, snapshots)
├── ui/
│   ├── main_window.py       #   main window, list, speed graph, DnD
│   ├── add_download_dialog.py
│   ├── settings_dialog.py
│   ├── history_window.py
│   ├── about_dialog.py
│   ├── system_tray.py       #   system tray icon
│   ├── themes/              #   dark / light QSS
│   ├── resources/           #   icons, tray icons, sounds
│   └── widgets/             #   download row, progress bar, graph, tabs
├── utils/
│   ├── config.py            #   typed settings (SQLite-backed, Qt signal)
│   ├── constants.py
│   ├── file_utils.py        #   human sizes, ETA, categories
│   ├── logger.py            #   rotating file + console logging
│   ├── notifications.py     #   toasts + bundled sounds
│   ├── clipboard_monitor.py
│   ├── network_utils.py
│   └── system_utils.py      #   open file/folder, autostart, shutdown
├── build/
│   ├── app_icon.ico
│   ├── build.bat            #   one-shot local build (venv → test → exe)
│   └── installer.nsi        #   NSIS installer script
├── scripts/
│   └── make_assets.py       #   regenerates icons/sounds
└── tests/                   #   40 tests: engine e2e, queue, units, UI smoke
```

## Building the .exe

### One click (Windows)
```bat
build\build.bat
```
Creates a virtualenv, installs dependencies, runs the test suite and produces
`build\dist\TurboFetch.exe` plus the NSIS installer.

### Manual
```bat
pip install -r requirements.txt
pyinstaller idm.spec --noconfirm --distpath build/dist --workpath build/pyi_build
makensis build\installer.nsi
```

### GitHub Actions
The [build workflow](.github/workflows/build.yml) runs on every push, PR and
manual dispatch:

1. **Test** – full pytest suite (offscreen Qt) on `windows-latest`.
2. **Build** – PyInstaller onefile exe, then NSIS installer, uploaded as run
   artifacts. NSIS is installed on the fly with Chocolatey because
   `windows-latest` (the `windows-2025` image) does not ship `makensis` –
   only `windows-2022` preinstalls NSIS 3.10.
3. **Release** – when you push a tag like `v1.0.1`, the artifacts are
   published as a GitHub Release automatically. The installer is uploaded
   under the stable name `TurboFetch-Setup.exe` (the versioned
   `TurboFetch-Setup-1.0.0.exe` stays in `build/dist/` too).

> **Note:** the corrected workflow is committed at
> [`build/workflow-build.yml`](build/workflow-build.yml) – copy its contents
> into `.github/workflows/build.yml` to activate it. GitHub refuses pushes to
> `.github/workflows/**` from an App token that lacks the *workflows*
> permission, so that one file has to be edited by you (the GitHub web editor
> works: **Actions → Build Windows EXE → ⋯ → Edit**, or open
> `.github/workflows/build.yml` and press `e`).

You can also trigger it manually from the *Actions* tab
(`workflow_dispatch`) and download the artifacts from the run summary.

## Testing

```bat
set QT_QPA_PLATFORM=offscreen
python -m pytest tests -v
```

40 tests cover the engine end-to-end (multi-segment transfer, pause/resume,
restart-resume, 404, flaky-server retry, no-ranges fallback, cancel, speed
limit, scheduler), the priority queue, segment math, URL parsing and an
offscreen UI smoke suite (window boot, live download rows, tabs, search,
history, settings, details, speed graph, close-to-tray).

## License

[MIT](LICENSE) © 2026 sufyan-vip
