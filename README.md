# WKLink — WinKeyer → VBand Bridge

Connect your K1EL WinKeyer USB device to [VBand](https://hamradio.solutions/vband/) for online CW practice with your real paddle, bug, or straight key.

## How it works

WKLink puts the WinKeyer in host mode and monitors **raw paddle contact state** from its status bytes in real time. When a contact changes, WKLink immediately presses or releases the corresponding key that VBand listens for:

- **Dit paddle** (or straight key down) → `Left Ctrl` pressed / released
- **Dah paddle** → `Right Ctrl` pressed / released

There is no re-encoding of timing in software. The WinKeyer hardware handles all element timing. VBand handles iambic sequencing. WKLink is purely a transparent bridge — contact closed → key held, contact open → key released.

## Works with

- ✅ Iambic paddles (A or B — set VBand's mode to match)
- ✅ Bug
- ✅ Straight key
- ✅ Any WinKeyer USB (WKUSB, WKmini, WK3serial)

## Setup

1. Plug in your WinKeyer USB
2. Open VBand in Chrome or Edge and join a channel
3. Set VBand's mode to match your key type (Iambic A/B, Bug, or Straight Key)
4. Launch WKLink, select your COM port, click **CONNECT**
5. Keep VBand as the active browser tab while keying

## Options

- **Mute WK sidetone** — recommended so you only hear VBand's audio, not both at once
- **Always on top** — keeps WKLink visible above the browser so you can see WPM
- **Swap paddles** — reverses dit and dah at the WinKeyer level; takes effect immediately

## Downloads

Latest release: [github.com/catriname/WKLink/releases/latest](https://github.com/catriname/WKLink/releases/latest)

- `WKLink.exe` — portable executable, no installation needed
- `WKLink-Setup.exe` — Windows installer with Start Menu and desktop shortcuts

## Build from source (Windows)

```
pip install -r requirements.txt
pyinstaller --onefile --windowed --name WKLink wklink.py
```

To also build the installer (requires [NSIS](https://nsis.sourceforge.io/)):

```
makensis installer.nsi
```

Or run `build.bat` to do both in one step.

## Notes

- WPM display updates from your speed pot when the pot is turned
- Decoded characters appear in the log window (WinKeyer's own paddle echo, for display only — not used for VBand timing)
- WK3 keyer settings (iambic mode A/B, weighting, etc.) are configured on the keyer itself, not in WKLink
- Paddle swap can be toggled live without reconnecting

## License

MIT — 73 de K5GRR
