# pi-ereader

A DIY e-paper ebook reader for the original Raspberry Pi Zero W (ARMv6, 512 MB RAM), written in Python.

**Every command, URL and troubleshooting tip is in [COMMANDS.md](COMMANDS.md).**

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

Keys: `w`/`k` or the Up arrow = up, `s`/`j` or the Down arrow = down, space/enter = select, `m` = hold select, `q` = quit.

Using it (three buttons only). The app opens the book you were last reading; pass `--start library` to open the library instead.
- **Library:** up/down pick a book, select opens it. Your current book is a larger *Continue reading* card on top. **Hold select** (keyboard: `m`) for options: sort by recent, title or author, show all, reading, new or finished books, and choose the sleep screen.
- **Covers and status:** each book shows its cover (books without one get a generated cover with the title), and either a NEW badge, a progress bar with percent, or a Finished mark. Covers are shrunk and dithered once, then cached in `~/.pi-ereader/covers`.
- **Sleep screen:** after 60 seconds idle the e-paper shows an empty white screen, then powers down (the image stays with no power). In the library options you can switch it to *Cover* (the current book's cover) or *Page* (leave the current page). In the PNG preview, quitting writes `preview/sleep.png` so you can see it.
- **Reading:** down = next page, up = previous page, **select = menu**.
- **Menu** (it scrolls; a scrollbar shows where you are): Resume, Table of contents, **Bookmarks**, **Bookmark this page** / Remove bookmark, Back to library, Font (Literata, Noto Serif or Charis SIL), Font size (select cycles 18-32 and remembers it), Pin to top / Unpin, and **Delete this book**. Delete asks first ("Keep the book" is selected by default), then removes the file, your saved place, and its cached covers and pictures.
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

The panel goes to sleep by itself after 60 seconds with no page turn (the image stays), and the next turn after that is a fast refresh. To start on boot, edit `deploy/pi-ereader.service` (it explains the install steps) and enable it with systemd. Your user must be in the `spi` and `gpio` groups.

   **Switching off.** Hold select for 3 seconds: the reader saves your page, leaves the sleep screen up and powers the Pi off (it needs passwordless `sudo`, which Raspberry Pi OS has by default). Unplug once the green LED stops blinking.

   **Refreshing.** Page turns and moving a highlight are partial refreshes (no flash, a little ghosting). A new screen, and every 8th page turn, is a *fast* refresh: about 1.5 s with one flash. Every 6th of those, and the sleep screen, is a *true full* refresh (several flashes, about 3 s) that wipes the ghosting away. Change `FAST_BEFORE_FULL` in `ereader/display.py` (higher = fewer big flashes, more ghosting) or `FULL_REFRESH_EVERY` in `ereader/screens.py`.

## Long contents lists

A chapter title that is too long for one line wraps onto up to three lines (`MAX_TOC_LINES` in `ereader/screens.py`); only a title longer than that is cut with `...`. Pages of the contents list therefore hold as many rows as fit.

On the contents screen, **hold select** to change how far up/down move: 1, 5, 10 or 25 rows at a time (the header shows `jump N`). With a big step the cursor stops at the first and last row before wrapping, so you can't skip past them. The Quran EPUB has 147 rows (30 juz and 114 surahs): jump 25 crosses it in about 6 presses, then drop back to 1 to fine-tune. The chapter you are in is marked with an arrow.

## Bookmarks

*Bookmark this page* in the reader menu marks the page (a small bookmark appears in its top corner); the same row says *Remove bookmark* on a marked page. *Bookmarks* lists them in reading order with the chapter name and percent read: select goes there, **hold select** removes one. A bookmark remembers where in the chapter chunk the page is as a fraction, so it still lands on the right text after you change the font size. Deleting the book deletes its bookmarks.

## Categories and Up next

Each book can have a **category** (a shelf name you choose, such as "Quran" or "Hadith") and can be on your **Up next** list of books you want to read, in the order you want. Both are set from your phone (see *Managing books from your phone*). In the library, hold select and use the **Show** row to pick *Up next* (in your order) or one category; the choice is remembered. Deleting a book clears its category and Up next entry.

## Pinned books

**Pin to top** in the reader menu keeps a book above all the others in the library (a pin icon marks it), whatever the sort order, with several pins in the order you pinned them. Pin the Quran, or whatever you open every day, and it never gets buried. The *Continue reading* card still goes to the book you last read: first if that book is pinned, otherwise right below the pinned ones.

## Verses stay with their translation

An Arabic paragraph followed by an English one (a verse and its translation, as in the Quran EPUBs) is kept on one page. If the pair doesn't fit in what is left of the page, it starts on the next page. If it is too tall for a whole page, the pair is shrunk together (2 px at a time, down to `MIN_VERSE_SIZE` = 14 in `ereader/layout.py`) until it fits, and gets a page of its own. Only a verse that is still too tall at that size, such as the long verse at 2:282, flows over pages as normal text. Other text keeps your chosen font size.

## Hadith books

`tools/make_hadith_epub.py` turns the JSON files of [AhmedBaset/hadith-json](https://github.com/AhmedBaset/hadith-json) (the `db/by_book/` folder, copied flat into one folder) into EPUBs: one book called **Hadith** with every collection, and one book per collection. The contents list is nested (Hadith > collection > chapter): chapters are indented under their collection, and on the contents screen **hold select until the header says `jump: parts`** to move up/down between collections instead of chapters. The status bar shows `Collection › Chapter` (the collection is shortened first if the line is too long). Each hadith is an Arabic paragraph then its English one, so the reader keeps them on one page (shrinking a long pair to fit).

```bash
python3 tools/make_hadith_epub.py ~/hadith-json ~/books-out                      # Hadith.epub + individual/*.epub
python3 tools/make_hadith_epub.py ~/hadith-json ~/books-out --only riyad_assalihin
```

The data was scraped from sunnah.com, the repository states no license, and the English translations belong to their translators, so keep these books on your own device. The data and the built books are in `.gitignore`.

## Headings

`<h1>`-`<h6>` are headings, and so are blocks the book marks with a title-like class (`chapter-title`, `subtitle`, `title`...), `role="heading"` or `epub:type="title"`: they are drawn bold and centered with extra space around them. A class-marked block longer than 40 words is treated as an ordinary paragraph.

## Chapters inside one file

Many EPUBs put several chapters in one long file and point to each with an `#anchor`. The reader follows those anchors: a chapter starts its own chunk (so choosing it in the contents lands exactly on it, with its title), and the status bar names the chapter you are really in. If the anchor sits just after the chapter's title, as in the Quran EPUBs, the title is included. A chapter whose anchor can't be found falls back to the start of its file.

## Pictures in books

PNG, JPEG, GIF, BMP and WebP pictures are shown, shrunk to fit the page and centered (a picture too tall for the rest of a page moves to the next one). Photos are dithered to black and white; diagrams and scanned pages are thresholded so lines stay crisp; transparent areas become white. A picture is decoded once and cached in `~/.pi-ereader/images`, so turning back to a page is quick. Pictures that can't be shown (SVG, missing files, damaged files) appear as a labelled box with the alt text. A picture is always on its own line: small pictures inside a line of text, such as a symbol drawn as an image, are not placed inline.

## Managing books from your phone

Hold select on the library (keyboard: `m`), choose **Manage books over Wi-Fi**. The screen shows a **QR code**, an address like `http://192.168.1.50:8080` and a 4-digit PIN. Scan the code with your phone's camera (same Wi-Fi): the code holds the address and the PIN (after a `#`, which stays in the browser and never reaches the Pi, and is wiped from the address bar), so it opens already unlocked. Or open the address on any device and type the PIN. The page lists your books with their progress and lets you:

- put a book in a **category** (pick one you have or make a new one) and see only that category on the reader (library options, **Show**),
- add it to **Up next** (books you want to read) and reorder that list with the arrows (library options, **Show: Up next**, shows it in your order),
- **rename** it with *Rename* (the name is kept on the Pi; the EPUB file is not touched, and an empty name goes back to the original),
- **move your place** with *Go to…*: pick a chapter from its contents list or type a percent. The book then opens there, and counts as the book you last read (the Continue reading card),
- **pin** it to the top of the library, or **delete** it (the file, its place, its category, its name and its cached covers),
- add books: open *Add books* and pick or drag in `.epub` files.

Press select on the reader when you're done. Press select on the reader when you're done: the upload server runs only while that screen is open, so nothing is listening on your network the rest of the time.

Safety: every request needs the PIN (5 wrong guesses lock it for a minute); only plain `.epub` file names up to 100 MB are accepted, each file must actually open as an EPUB or it's discarded, and uploads are written to a hidden temporary file first, so a half-finished upload never appears as a book. Uploading a name that already exists replaces that book.

You can also run it without the reader: `python3 -m ereader.upload --books ~/books` prints the address and PIN. It uses only the standard library, so there is nothing extra to install.

## Flash cards and quotes

For anything you want to remember. A **card** has a front, an optional back and an optional source, and belongs to a **deck**; a card with no back is a **quote**. Cards are added from the phone page (its **Cards** tab: one at a time, or paste or upload many, in Anki's "Notes in plain text" shape: `front`, tab, `back`, tab, `source`) and kept in `~/.pi-ereader/cards.json` (`ereader/cards.py`). On the reader, hold select in the library and choose *Flash cards and quotes* (`ereader/cardscreens.py`): **Review** shows the cards that are due (select shows the answer, up = forgot, down = got it) and schedules them with Leitner boxes (a known card comes back after 1, 3, 7, 14, then 30 days; a forgotten one at once); **Browse all** pages through every card. Card text is drawn with the book font and layout code, so Arabic and mixed lines read right to left like in books, and long text shrinks to fit. **Pictures:** a card can have a picture on the front and/or the back, added from the phone page (the browser shrinks it before sending, `images.prepare_picture` checks it and keeps a grayscale copy of at most 600 x 450 in `~/.pi-ereader/card-images/`). On the reader it is drawn above its text, as big as the text leaves room for (`cardscreens.draw_card`); diagrams are thresholded so lines stay crisp, photos are dithered (`images.to_one_bit`, the same code book pictures use). Photos with fine detail look coarse on a 400 x 300 black-and-white panel; diagrams, drawings, maps and handwriting look good.

## Long chapters

Books are laid out lazily: only the part you are reading, plus its neighbours, is in memory. A chapter file bigger than about 60 KB is cut into chunks of about 40 KB at paragraph ends (`CHUNK_BYTES` in `ereader/book.py`), so a huge single-chapter book opens at once and uses a few MB, not tens. Measured on a Mac with a synthetic 2 MB chapter: first page in 0.6 s (was 30 s), Python memory 5 MB (was 55 MB). Word widths are remembered per font (`Fonts.width`, up to 20,000 entries), which cut laying out a Quran chunk from 0.67 s to 0.05 s on the Mac. Pi Zero numbers will differ; time it with the `[reader] section ... pages in ...s` lines the app prints.

## Third-party code

`ereader/qrcodegen.py` is [Project Nayuki's QR Code generator](https://www.nayuki.io/page/qr-code-generator-library) (MIT license, header kept in the file). It draws the QR code on the Manage books screen and needs nothing installed.

## Fonts

Bundled in `assets/fonts`, all under the SIL Open Font License (license texts are next to them):
- **Literata** (default), **Noto Serif** and **Charis SIL** for Latin text, including the transliteration letters (ḥ ṣ ḍ ṭ ẓ ʻ ʼ). Switch between them with the **Font** row in the reader menu; the choice is remembered.
- **Amiri Quran** for all Arabic text, whichever Latin font is selected. It draws the vowel marks and Quranic stop signs properly. It has no bold, so bold Arabic (headings) uses **Noto Naskh Arabic** Bold. Arabic is drawn 20% larger than the Latin text (`ARABIC_SCALE` in `ereader/fonts.py`) and its lines get extra height for the marks (`ARABIC_PAD`).

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
- Turn off Wi-Fi while reading; measure the power draw before and after
