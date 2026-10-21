# pi-ereader: every command, and how to reach everything

Copy-paste cheat sheet. **Mac** means a Terminal on your Mac. **Pi** means an SSH session on the Pi Zero.

> Values that are specific to your setup (change them if yours differ):
> Pi address `192.168.1.50` (can change after a reboot or router restart: run `hostname -I` on the Pi),
> Pi user `pi`, hostname `books.local`, project on the Mac `~/Downloads/pi-ereader`,
> books on the Mac `~/Downloads/books`, books on the Pi `~/books`.

---

## 1. Where to open what

| What | Where | Needs |
|---|---|---|
| SSH into the Pi | `ssh pi@192.168.1.50` (or `ssh pi@books.local`) | Pi on Wi-Fi |
| Preview of the screen (Pi) | http://192.168.1.50:8000 | the reader **and** the preview server running on the Pi |
| Preview of the screen (Mac) | http://localhost:8000 | the same two things, running on the Mac |
| Add books over Wi-Fi | the address and PIN shown on the reader's *Add books* screen, e.g. http://192.168.1.50:8080 | the reader on that screen, or `python3 -m ereader.upload` |
| The code on GitHub (private) | https://github.com/MKarimF9/zero-pages | your GitHub login |
| PDFLoom backend (PDF to EPUB) | http://localhost:3000 | PDFLoom backend running on the Mac |

---

## 2. Everyday: run the reader and see it in a browser (no screen needed)

Open **two** SSH windows to the Pi.

**Window 1: the reader**
```bash
cd ~/pi-ereader
python3 main.py --books ~/books --width 400 --height 300
```

**Window 2: the preview server** (start the reader first: it creates the `preview` folder)
```bash
cd ~/pi-ereader/preview
python3 -m http.server 8000
```

**Your Mac's browser:** open http://192.168.1.50:8000. It refreshes every second.

**Keys** (click the reader window first so it gets the keys):

| Key | Does |
|---|---|
| `w`, `k`, Up arrow | up / previous page |
| `s`, `j`, Down arrow | down / next page |
| Space or Enter | select |
| `m` | hold select (library options) |
| `q` | quit (saves your place) |

**Same thing on the Mac** (to try changes before putting them on the Pi):
```bash
cd ~/Downloads/pi-ereader
python3 main.py --books ../books --width 400 --height 300
# second Terminal:
cd ~/Downloads/pi-ereader/preview && python3 -m http.server 8000
# browser: http://localhost:8000
```

**Options for `main.py`**

| Option | Meaning |
|---|---|
| `--books DIR` | folder with `.epub` files (default `~/books`) |
| `--start last` / `--start library` | open the last book (default) or the library |
| `--display png` / `--display epaper` | PNG preview (default) or the real screen |
| `--controls keyboard` / `--controls gpio` | keyboard (default) or the three buttons |
| `--width N --height N` | preview size, PNG only (the e-paper uses its own 400x300) |
| `--font-size N` | book text size (default: your last choice in the menu, else 22) |

Test the real buttons with no screen (wire them to GPIO 5, 6, 13 and GND):
```bash
python3 main.py --display png --controls gpio --books ~/books --width 400 --height 300
```

---

## 3. Using the reader (three buttons)

| Where | Up / Down | Select | Hold select (`m`) |
|---|---|---|---|
| Library | move between books | open the book | options: sort, filter, sleep screen, add books over Wi-Fi |
| Reading | previous / next page | menu | same as select |
| Menu | move | choose (*Resume*, *Table of contents*, *Font*, *Font size*, *Pin to top*, *Back to library*, *Delete this book*) | same as select |
| Delete this book? | choose *Keep the book* (default) or *Delete it* | confirm the choice | same as select |
| Contents | move | jump to the chapter (first row is *Back*) | same as select |
| Library options | move | change the value (*Sort by*, *Show*, *Sleep screen*), or open *Add books over Wi-Fi*, or go back | same as select |
| Add books over Wi-Fi | refresh | stop and go back | same as select |

Your place, font and settings are saved. Reset everything on the Pi:
```bash
ls ~/.pi-ereader          # state.json, library.json, covers/, images/
rm -r ~/.pi-ereader       # forget places, settings and cached covers and pictures (books are not touched)
```

---

## 4. Adding books

**Easiest: over Wi-Fi.** On the reader: library, hold select (`m`), *Add books over Wi-Fi*. It shows an address and a PIN. Open the address on any device on the same Wi-Fi, type the PIN, pick or drag `.epub` files. Press select on the reader when done (the server stops).

**Without the reader (Pi):**
```bash
cd ~/pi-ereader
python3 -m ereader.upload --books ~/books            # prints the address and PIN
python3 -m ereader.upload --books ~/books --port 8080 --pin 1234   # your own PIN
# Ctrl+C to stop
```

**Copy a file with scp (Mac):**
```bash
scp "/Users/you/Downloads/books/My Book.epub" pi@192.168.1.50:~/books/
```

**What the Pi sees (Pi):**
```bash
ls -lh ~/books
cd ~/pi-ereader
python3 -c "
import pathlib
from ereader.library import BookIndex
for b in BookIndex('/tmp/lib.json').scan(pathlib.Path('~/books').expanduser()):
    print(b.path.name, '|', b.title, '|', b.author)"
```

**Delete a book:** on the reader, open a book, press select for the menu, move to **Delete this book**, select, choose *Delete it*. Or use the *Delete* button on the Wi-Fi page, or `rm ~/books/"name.epub"` on the Pi.

**Only `.epub` works.** For PDFs see section 8.

---

## 5. Install and update on the Pi

**First install**
```bash
# Pi
sudo apt update
sudo apt install python3-pil python3-spidev python3-gpiozero python3-rpi-lgpio git
python3 --version                       # must be 3.10 or newer
python3 -c "from PIL import features; print(features.check('raqm'))"   # must print True (Arabic letters join)
mkdir -p ~/books
```
(If `python3-rpi-lgpio` doesn't exist, use `python3-rpi.gpio`.) Use apt, not pip, for these.

**Get the code with the read-only deploy key** (already set up; for a new Pi):
```bash
# Pi: make a key, then add its .pub to GitHub as a read-only deploy key (from the Mac:
#   gh repo deploy-key add pi_ereader.pub --repo MKarimF9/zero-pages --title "pi zero")
ssh-keygen -t ed25519 -f ~/.ssh/pi_ereader -N ""
cat ~/.ssh/pi_ereader.pub
GIT_SSH_COMMAND="ssh -i ~/.ssh/pi_ereader -o IdentitiesOnly=yes" git clone git@github.com:MKarimF9/zero-pages.git
cd pi-ereader
git config core.sshCommand "ssh -i ~/.ssh/pi_ereader -o IdentitiesOnly=yes"
```

**Update to the latest version**
```bash
# Pi
cd ~/pi-ereader && git pull
```
Quit and restart the reader afterwards. (No GitHub on the Pi? From the Mac:
`rsync -av --exclude preview --exclude __pycache__ ~/Downloads/pi-ereader/ pi@192.168.1.50:~/pi-ereader/`)

---

## 6. The real screen and buttons (when they arrive)

```bash
# Pi
sudo raspi-config nonint do_spi 0 && sudo reboot          # turn SPI on
ls /dev/spidev0.*                                          # should list spidev0.0 and spidev0.1
sudo usermod -aG spi,gpio pi                          # then log out and back in

# the Waveshare driver (not on pip): copy its waveshare_epd folder into ~/pi-ereader/lib/
git clone --depth 1 https://github.com/waveshareteam/e-Paper
mkdir -p ~/pi-ereader/lib
cp -r e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd ~/pi-ereader/lib/

cd ~/pi-ereader
python3 main.py --books ~/books --display epaper --controls gpio
```
Wiring: buttons between a GPIO pin and GND: **up = GPIO 5** (header pin 29), **down = GPIO 6** (pin 31), **select = GPIO 13** (pin 33). The e-paper HAT uses GPIO 17, 25, 8, 24, 18 and SPI; keep the buttons off those. The e-paper path has **not been tested on real hardware yet**.

**Start on boot (systemd)**
```bash
# Pi
cd ~/pi-ereader
sed -i 's/youruser/pi/g' deploy/pi-ereader.service
sudo cp deploy/pi-ereader.service /etc/systemd/system/
sudo systemctl enable --now pi-ereader

sudo systemctl status pi-ereader          # is it running?
journalctl -u pi-ereader -f               # live log (Ctrl+C to stop watching)
sudo systemctl restart pi-ereader         # after git pull
sudo systemctl stop pi-ereader            # BEFORE running the reader by hand (both need the screen)
sudo systemctl disable pi-ereader         # stop starting on boot
```

---

## 7. Check speed, memory and tests

**Speed:** the reader prints a line each time it lays out part of the book:
`[reader] section 5/961: 3 pages in 0.04s`. Paste these when asking about slowness.

**Memory (Pi, in a third window while reading):**
```bash
free -m
```

**Tests**
```bash
# Mac
pip3 install pytest fonttools
cd ~/Downloads/pi-ereader && python3 -m pytest -q

# Pi
sudo apt install python3-pytest python3-fonttools
cd ~/pi-ereader && python3 -m pytest -q
```
(`fonttools` is only for one test that checks the icon font.)

---

## 8. PDFs and other formats to EPUB

The reader only opens **EPUB**. Convert on the Mac, not the Pi.

**With PDFLoom** (also handles scanned Arabic PDFs; needs Docker and your `GEMINI_API_KEY`):
```bash
cd <PDFLoom>/backend
docker compose up -d && npm run migrate && npm run dev        # server on :3000

curl -F pdf=@book.pdf -F title="Book title" -F author="Author" http://localhost:3000/api/upload
#   -> {"bookId":"...","status":"processing"}
curl -s http://localhost:3000/api/books                        # wait for "status":"READY" (see processingProgress)
curl -o book.epub http://localhost:3000/api/books/<bookId>/epub
```
Then send `book.epub` to the Pi (section 4). Free Gemini keys allow roughly 20 requests per model per day, so very long books can take more than one day. Start with a short PDF to check the result.

**With Calibre** (PDFs that contain real text; not scans):
```bash
brew install --cask calibre
/Applications/calibre.app/Contents/MacOS/ebook-convert book.pdf book.epub --language ar
```

Google Docs: *File, Download, EPUB Publication (.epub)* gives a real EPUB.

---

## 9. Git and GitHub (how changes are tracked)

Every feature gets its own branch, then is merged into `main`.
```bash
# Mac
cd ~/Downloads/pi-ereader
git checkout main && git pull
git checkout -b feature/my-change
# ...edit, test...
git add -A && git commit -m "Describe the change"
git push -u origin feature/my-change
git checkout main && git merge --no-ff feature/my-change -m "Merge feature/my-change" && git push origin main

git log --oneline --graph | head -20     # history
git branch -a                            # all branches
gh repo view --web                       # open the repo in your browser
```
Branches so far: `feature/open-last-book`, `feature/metadata-cache`, `feature/covers`, `feature/reading-status`, `feature/wrapped-titles-rtl-rows`, `feature/library-sort-filter`, `feature/sleep-screen`, `feature/chunked-layout`, `feature/arrow-keys`, `feature/wifi-upload`, `docs/commands-cheatsheet`, `feature/book-images`, `feature/delete-book`, `feature/anchor-chapters`, `feature/class-headings`, `feature/pinned-books`.

The Pi has a **read-only** deploy key, so it can `git pull` but never push.

---

## 10. Maintenance tools (rarely needed)

```bash
# After adding an icon to ICONS in ereader/ui.py: rebuild the 3 KB icon font
pip install fonttools
python3 tools/make_icon_font.py MaterialSymbolsOutlined.ttf MaterialSymbolsOutlined.codepoints

# Rebuild the book fonts (Literata, Noto Serif, Noto Naskh Arabic; Charis SIL is copied as is)
python3 tools/make_fonts.py <folder with the downloaded font files>
```
Both scripts say in their docstrings where to download the inputs.

---

## 11. Troubleshooting

| Problem | Try |
|---|---|
| Browser preview blank or old | Is the reader running? Did you start the preview server **inside** `preview/`? Wrong IP? Run `hostname -I` on the Pi. |
| `books.local` not found | Use the IP address instead. |
| `Permission denied (publickey)` on `git pull` | `git config core.sshCommand` must print the `ssh -i ~/.ssh/pi_ereader ...` line; if empty, set it (section 5). |
| `ModuleNotFoundError: No module named 'PIL'` | `sudo apt install python3-pil` |
| Arabic letters not joined | `python3 -c "from PIL import features; print(features.check('raqm'))"` must be `True`. |
| App won't start with `TypeError: unsupported operand type(s) for \|` | Python is older than 3.10. Use a newer Raspberry Pi OS. |
| Arrow keys do nothing | `git pull` (arrow keys were added after the first version); letters `w`/`s` always work. |
| Upload page says wrong PIN / locked | The PIN is new each time the *Add books* screen opens. Five wrong tries lock it for a minute. |
| A book shows as `(unreadable)` | The file didn't copy fully or isn't a valid EPUB; copy it again. |
| Port 8000 or 8080 already in use | Stop the other program, or pass another port (`python3 -m http.server 8001`, `--port 8081`). |
| Reader feels slow | Paste the `[reader] section ... pages in ...s` lines and say whether it's startup, opening a book, or each key press. |
| Screen stuck after a crash (real hardware) | `sudo systemctl stop pi-ereader`, wait a few seconds, start it again. |
