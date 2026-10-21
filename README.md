# pi-ereader

A DIY e-paper ebook reader for the original Raspberry Pi Zero W (ARMv6, 512 MB RAM), written in Python.

The structure is modeled on [atomic14's ESP32 ePub reader](https://github.com/atomic14/diy-esp32-epub-reader) (MIT license): the same pipeline, the same three-button controls, and the same split between app logic and hardware. No code was copied from it; this is an independent Python version for Linux hardware. [PiBook](https://github.com/rolohaun/PiBook) is a second reference for Pi- and Waveshare-specific parts.

## How it fits together

```
 .epub (zip)
    │  epub.py         container.xml → .opf → title, author, spine (reading order)
    ▼
 chapter XHTML
    │  html_parser.py  <p>, <h1>, <b>, <i>, <img> → list of blocks
    ▼
 blocks
    │  layout.py       measure words → break into lines → stack lines into pages
    ▼
 pages (positioned words)
    │  screens.py      draw a page into a black-and-white image
    ▼
 image
    │  display.py      PNGDisplay (now) or EPaperDisplay (later)
    ▼
 screen
```

Input comes from `controls.py`: the keyboard over SSH now, GPIO buttons later. `main.py` runs the loop: draw, show, wait for a button, switch screens.

The display and the controls are interfaces. The rest of the app never knows whether it's drawing to a PNG or to e-paper, or whether input comes from a keyboard or buttons. Swapping in hardware means writing one class each, nothing else.

## What's done and what's yours

| File | Status |
|---|---|
| `blocks.py`, `page.py`, `fonts.py`, `state.py` | Done |
| `display.py` → `PNGDisplay` | Done |
| `controls.py` → `KeyboardControls` | Done |
| `screens.py` → library screen, page drawing | Done |
| `epub.py` | **Milestone 1** |
| `html_parser.py` | **Milestone 2** |
| `layout.py` → `break_lines` | **Milestone 3** |
| `layout.py` → `paginate` | **Milestone 4** |
| `screens.py` → refresh policy and saving progress | **Milestone 5** |
| `display.py` → `EPaperDisplay`, `controls.py` → `GPIOControls` | **Milestone 6** (needs hardware) |

The app runs from day one. Anything you haven't written yet shows up on screen as "Not implemented yet: …", telling you which function to write next.

## Setup (on the Pi)

```bash
sudo apt install python3-pil python3-pytest
```

Use apt, not pip. On ARMv6, pip may try to compile Pillow from source.

Pillow also needs libraqm to join Arabic letters; check with `python3 -c "from PIL import features; print(features.check('raqm'))"` (it should print `True`). No system fonts are needed: the fonts are in `assets/fonts` (below).

Copy the project over from your Mac:

```bash
scp -r pi-ereader youruser@books.local:~
```

## Run it

**Terminal 1** (SSH into the Pi):

```bash
cd ~/pi-ereader
python3 main.py --books ~/books
```

Keys: `w`/`k` = up, `s`/`j` = down, space/enter = select, `q` = quit.

Using it (three buttons only). The app opens the book you were last reading; pass `--start library` to open the library instead.
- **Library:** up/down pick a book (the one you read last is first, with a progress bar), select opens it.
- **Reading:** down = next page, up = previous page, **select = menu**.
- **Menu:** Resume, Table of contents, Font size (select cycles 18-32 and remembers it), Back to library.
- **Status bar** under each page: progress bar, chapter title, page in chapter, percent read. Right-to-left books are mirrored.

**Terminal 2** (SSH into the Pi), the live preview server:

```bash
cd ~/pi-ereader/preview && python3 -m http.server 8000
```

On your Mac, open **http://books.local:8000**. The page refreshes every second, so you see what the e-paper would show.

## On the real hardware (milestone 6)

Written for the **Waveshare 4.2" V2** panel (400x300) and three push buttons. Not yet tested on a real device.

1. Enable SPI: `sudo raspi-config nonint do_spi 0`, then reboot.
2. Install the libraries the Waveshare driver needs: `sudo apt install python3-spidev python3-gpiozero python3-rpi.gpio` (on Raspberry Pi OS Bookworm use `python3-rpi-lgpio` instead of `python3-rpi.gpio`).
3. Get the driver (it isn't on PyPI):
   ```bash
   git clone --depth 1 https://github.com/waveshareteam/e-Paper
   mkdir -p ~/pi-ereader/lib
   cp -r e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd ~/pi-ereader/lib/
   ```
4. Wire the buttons between a GPIO pin and GND (the Pi's internal pull-ups are used): **up = BCM 5** (header pin 29), **down = BCM 6** (pin 31), **select = BCM 13** (pin 33). The HAT uses BCM 17, 25, 8, 24, 18 and SPI, so avoid those. Change the pins in `main.py` if you like.
5. Run it: `python3 main.py --books ~/books --display epaper --controls gpio`. The screen size comes from the panel; the keyboard and PNG options still work.

The panel goes to sleep by itself after 60 seconds with no page turn (the image stays), and the next turn after that is a full refresh. To start on boot, edit `deploy/pi-ereader.service` (it explains the install steps) and enable it with systemd. Your user must be in the `spi` and `gpio` groups.

## Fonts

Bundled in `assets/fonts`, all under the SIL Open Font License (license texts are next to them):
- **Literata** (default), **Noto Serif** and **Charis SIL** for Latin text. Switch between them with the **Font** row in the reader menu; the choice is remembered.
- **Noto Naskh Arabic** for all Arabic text, whichever Latin font is selected.

Literata, Noto Serif and Noto Naskh Arabic are stored as small static files (about 60-140 KB each) built from Google Fonts' variable fonts by `tools/make_fonts.py`. Charis SIL is copied unchanged, because its license reserves the font name.

## Icons

The menu, library and button hints use Google's [Material Symbols](https://github.com/google/material-design-icons) (Apache 2.0). `assets/MaterialSymbols-subset.ttf` is a 3 KB font holding only the icons we use. To add one, add it to `ICONS` in `ereader/ui.py` and rebuild the font with `tools/make_icon_font.py` (its docstring says where to get the full font). If the font file is missing, the icons are just left out.

## Tests

```bash
python3 -m pytest -v                         # everything
python3 -m pytest tests/test_epub.py -v      # one milestone at a time
```

Almost every test fails at first with `NotImplementedError`. That's expected: each milestone is done when its tests pass.

## Milestones

- [x] **1. Open EPUBs** (`epub.py`). `test_epub.py` passes, and the library shows real titles.
- [x] **2. Parse chapters** (`html_parser.py`). `test_html_parser.py` passes.
- [x] **3. Break lines** (`layout.break_lines`, greedy). The `break_lines` tests pass.
- [x] **4. Paginate** (`layout.paginate`). All tests pass, and a real book shows pages in the preview.
- [x] **5. Behave like a reader.** Full refresh every N turns; progress saved and restored.
- [ ] **6. Hardware.** `EPaperDisplay` and `GPIOControls`; then start on boot with a systemd service.

Stretch goals:
- Dynamic-programming line breaking (`break_lines_dp`), compared against greedy
- Lazy layout, one chapter at a time. Time it with `time.perf_counter()` first.
- Real images in pages
- Turn off Wi-Fi while reading; measure the power draw before and after
- A Wi-Fi upload page for adding books
