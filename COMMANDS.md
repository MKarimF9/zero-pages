# pi-ereader: every command, and how to reach everything

Copy-paste cheat sheet. **Mac** means a Terminal on your Mac. **Pi** means an SSH session on the Pi Zero.

> Values that are specific to your setup (change them if yours differ):
> Pi address `192.168.1.50` (can change after a reboot or router restart: run `hostname -I` on the Pi),
> Pi user `pi`, hostname `books.local`, project on the Mac `~/Downloads/pi-ereader`,
> books on the Mac `~/Downloads/books`, books on the Pi `~/books`.

---

## 0. What does what (read this first)

There are **four separate things**. Mixing them up is the usual reason "it's running but I see nothing".

| Thing | What it is | How it starts | Port |
|---|---|---|---|
| **The reader** (`main.py`) | The e-reader app itself: opens books, lays out pages, handles keys/buttons. It draws to a PNG file (no screen) or to the e-paper (real screen). | By hand: `python3 main.py ...`, or at boot by the systemd service | none |
| **The preview server** (`python3 -m http.server 8000`) | A plain web server that shows the reader's PNG in a browser, refreshed every second. Only needed **without** the screen. It must be started **inside** `~/pi-ereader/preview`. | By hand, in a second window | 8000 |
| **The phone page** (Manage books) | A web page to add, organise (category, Up next, pin), move your place in, and delete books. It is **part of the reader**: it exists only while the reader is on the *Manage books over Wi-Fi* screen, with a QR code and PIN. | Library, hold select (`m`), *Manage books over Wi-Fi*. Press select to stop. | 8080 |
| **The boot service** (`pi-ereader.service`) | Starts the reader on boot for the **real** e-paper screen and GPIO buttons. Do **not** enable it until the screen and buttons are connected: without them it crashes and restarts forever. | `sudo systemctl enable --now pi-ereader` | none |

**Which setup am I in?**

| You have | Run | Look at |
|---|---|---|
| No screen or buttons yet (now) | the reader **and** the preview server, by hand, in two SSH windows (section 2) | `http://192.168.1.50:8000` in a browser; type keys in the reader's window |
| E-paper screen and three buttons | the boot service (section 6) | the e-paper; the phone page for managing books |

**Shortcuts** so you don't retype the long commands (run once on the Pi):
```bash
echo "alias reader='cd ~/pi-ereader && python3 main.py --books ~/books --width 400 --height 300'" >> ~/.bashrc
echo "alias preview='cd ~/pi-ereader/preview && python3 -m http.server 8000'" >> ~/.bashrc
source ~/.bashrc
# from then on: type  reader  in one SSH window and  preview  in another
```
Keep the reader running after you close SSH (the reader stops when its SSH window closes):
```bash
sudo apt install tmux
tmux                      # start the reader inside it, then press Ctrl+B and then D to leave it running
tmux attach               # come back to it later
```

---

## 1. Where to open what

| What | Where | Needs |
|---|---|---|
| SSH into the Pi | `ssh pi@192.168.1.50` (or `ssh pi@books.local`) | Pi on Wi-Fi |
| Preview of the screen (Pi) | http://192.168.1.50:8000 | the reader **and** the preview server running on the Pi |
| Preview of the screen (Mac) | http://localhost:8000 | the same two things, running on the Mac |
| Manage books over Wi-Fi (add, categories, Up next, pin, delete) | the address and PIN shown on the reader's *Manage books* screen, e.g. http://192.168.1.50:8080 | the reader on that screen, or `python3 -m ereader.upload` |
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
cd ~/pi-ereader/preview        # IMPORTANT: inside preview/, not ~/pi-ereader
python3 -m http.server 8000
```
A web server shows whatever folder it was started in. If your browser lists `.git`, `ereader`, `main.py`..., you started it one folder too high: press `Ctrl+C`, `cd` into `preview` and start it again (or open `http://192.168.1.50:8000/preview/`).

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
| Library | move between books | open the book | options: sort, filter, sleep screen, **flash cards and quotes**, add books over Wi-Fi |
| Reading | previous / next page | menu | same as select |
| Menu (scrolls) | move | choose (*Resume*, *Table of contents*, *Bookmarks*, *Bookmark this page*, *Back to library*, *Font*, *Font size*, *Pin to top*, *Delete this book*) | same as select |
| Bookmarks | move (first row is *Back*) | go to the bookmark | **remove** the bookmark |
| Delete this book? | choose *Keep the book* (default) or *Delete it* | confirm the choice | same as select |
| Contents | move 1 row (or 5, 10, 25: see hold) | jump to the chapter (first row is *Back*) | change the jump size: 1, 5, 10, 25 |
| Library options | move | change the value (*Sort by*, *Show*: All / Reading / New / Finished / **Up next** / one per category, *Sleep screen*), or open *Manage books over Wi-Fi*, or go back | same as select |
| Manage books over Wi-Fi | refresh | stop and go back | same as select |
| Cards: deck list | move | open the deck (last row: back to the library) | same as select |
| Cards: a deck | move | *Review* (cards due), *Browse all*, *Back to decks* | same as select |
| Cards: review | **before the answer:** up = exit, down = skip. **After it:** up = *Forgot*, down = *Got it* | show / hide the answer (a quote with no back: *Next*) | same as select |
| Cards: browse | up = previous card, down = next card (after the last one comes an *End* page) | show / hide the answer; on a quote or the *End* page: go back | same as select |

Your place, font and settings are saved. Reset everything on the Pi:
```bash
ls ~/.pi-ereader          # state.json, library.json, covers/, images/
rm -r ~/.pi-ereader       # forget places, settings and cached covers and pictures (books are not touched)
```

---

## 4. Adding books

**Easiest: over Wi-Fi.** On the reader: library, hold select (`m`), *Manage books over Wi-Fi*. It shows a QR code (address and PIN inside), the address and the PIN. Scan the QR code with your phone's camera, or open the address on any device on the same Wi-Fi and type the PIN. You can organise your books (category, Up next, pin, delete) and add `.epub` files under *Add books*. Press select on the reader when done (the server stops).

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

**Start on boot (systemd)**: only once the screen and buttons are connected. Without them the service crashes and restarts in a loop (see section 11).
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
Branches so far: `feature/open-last-book`, `feature/metadata-cache`, `feature/covers`, `feature/reading-status`, `feature/wrapped-titles-rtl-rows`, `feature/library-sort-filter`, `feature/sleep-screen`, `feature/chunked-layout`, `feature/arrow-keys`, `feature/wifi-upload`, `docs/commands-cheatsheet`, `feature/book-images`, `feature/delete-book`, `feature/anchor-chapters`, `feature/class-headings`, `feature/pinned-books`, `feature/bookmarks`, `feature/quick-jump`.

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
| Upload page says wrong PIN / locked | The PIN is new each time the *Manage books* screen opens. Five wrong tries lock it for a minute. |
| A book shows as `(unreadable)` | The file didn't copy fully or isn't a valid EPUB; copy it again. |
| Browser shows a folder listing with `.git`, `ereader`, `main.py` | The preview server was started in `~/pi-ereader`. Stop it (`Ctrl+C`), `cd ~/pi-ereader/preview`, start it again. |
| `systemctl status pi-ereader` says *running* but nothing works; the log says *Scheduled restart job, restart counter is at N* | The service is crashing and restarting in a loop (it needs the e-paper screen and buttons). With no hardware: `sudo systemctl stop pi-ereader && sudo systemctl disable pi-ereader`, then use section 2. See why it fails: `journalctl -u pi-ereader -n 30 --no-pager`. |
| Nothing on port 8000 | `ss -ltnp \| grep -E '8000\|8080'`. Nothing listed = the preview server isn't running (it is **not** part of the reader or the service). 8080 exists only while the reader is on *Manage books over Wi-Fi*. |
| SSH won't connect | `ping 192.168.1.50` (Pi on and on the same Wi-Fi as the Mac?), try `ssh pi@books.local`, and read the exact message: *timed out* = not reachable, *refused* = SSH off or wrong port, *Permission denied* = wrong user or key. |
| Port 8000 or 8080 already in use | Stop the other program, or pass another port (`python3 -m http.server 8001`, `--port 8081`). |
| Reader feels slow | Paste the `[reader] section ... pages in ...s` lines and say whether it's startup, opening a book, or each key press. |
| Screen stuck after a crash (real hardware) | `sudo systemctl stop pi-ereader`, wait a few seconds, start it again. |


---

## 12. The phone page: what each button does

| Button | Does |
|---|---|
| Filter chips (All, Up next, Reading, New, Finished, your categories) | show only those books; the number is how many |
| **Up next** | add or remove the book from your reading list; in the *Up next* view, **▲ ▼** change the order |
| **Pin** | keep the book at the top of the library on the reader |
| Category dropdown | put the book on a shelf (pick one, *No category*, or *New category…*); the reader's *Show* option can then show just that shelf |
| **Rename** | give the book a name of your own (shown in the library, on the sleep cover and in the delete question); leave it empty to go back to the name inside the book. The file itself is not changed |
| **Go to…** | move your place: pick a chapter, or type a percent; the book then opens there |
| **Delete** | remove the file and everything remembered about it (asks first) |
| **Add books** (top of the page) | pick or drop `.epub` files to send them to the reader |
| **Books / Cards** tabs | switch between your books and your flash cards (see section 14) |

The QR code on the reader's *Manage books* screen holds the address and the PIN, so scanning it opens the page already unlocked.

---

## 13. Hadith books

```bash
# Get the data (the db/by_book/ files, flat in one folder), build, and copy to the books folder
python3 tools/make_hadith_epub.py ~/hadith-json ~/books-out
cp ~/books-out/Hadith.epub ~/books/
python3 tools/make_hadith_epub.py ~/hadith-json ~/books-out --only riyad_assalihin   # just one collection
```

On the contents screen, hold select until the header says `jump: parts` to move between collections.

---

## 14. Flash cards and quotes

For things you want to remember: a quote, a verse, a word and its meaning, a question and its answer. Text only for now (pictures may come later).

A **card** has a **front**, an optional **back** and an optional **source** (for example *Sahih al-Bukhari 1*), and lives in a **deck** (for example *Quotes*, *Arabic words*). A card with no back is a **quote**. Arabic and English both work, including a line of each.

**Add cards from your phone.** Open the phone page (library, hold select, *Manage books over Wi-Fi*, scan the QR code) and tap the **Cards** tab:

| Button | Does |
|---|---|
| **Add card** | adds it to the deck named in *Deck* (a new name makes a new deck; *quotes* and *Quotes* are the same deck) |
| **Import many cards** | paste lines, or choose a `.txt` / `.tsv` file; one card per line: `front`, a tab, `back`, a tab, `source` (back and source are optional). Anki's *File, Export, Notes in plain text* gives this shape; HTML in it (`<br>`, `<b>`) is turned into plain text and its `#separator` header lines are skipped. They go into the deck in *Deck* |
| Deck chips | show one deck; the number is how many cards, and how many are due |
| **Edit** / **Delete** | change or remove a card (asks before deleting) |
| **Delete this deck** | removes the deck and all its cards (asks first) |

**Use them on the reader.** Library, hold select (`m`), *Flash cards and quotes*, pick a deck:
- **Review** shows the cards that are due. Select shows the answer; then up = *Forgot*, down = *Got it*. A forgotten card comes back before you finish; a known card comes back later (1, 3, 7, 14, then 30 days). New cards are due at once. A quote (no back) just shows with its source; select = *Next*.
- **Browse all** pages through every card (up / down), select shows the answer.

Limits: 5000 cards, a front up to 1000 characters, a back up to 2000, a source up to 200. Text too long for the screen is shrunk (down to 14 px) and then cut with "…".

Where it is kept: `~/.pi-ereader/cards.json` (back it up with your books). The reader and the phone page share it. Deleting a book does not touch your cards.
