# WKLink — WinKeyer → VBand Bridge

Connect your K1EL WinKeyer USB device to [VBand](https://hamradio.solutions/vband/) for online CW practice with your real paddle or bug.

## How it works

WinKeyer operates in **host mode** with paddle echo enabled. As you key, WK3 decodes each character and echoes it back as ASCII. WKLink receives those characters, looks up the Morse pattern, and simulates the appropriate `Left Ctrl` (dit) and `Right Ctrl` (dah) keypresses that VBand listens for — at the correct WPM from your speed pot.

No latency from the original VBand USB adapter problem: your WinKeyer handles all timing hardware-side.

## Works with

- ✅ Iambic paddles (A or B — set on the WinKeyer itself)
- ✅ Bug (WK3 in bug mode — set via WK3tools or paddle commands)
- ✅ Straight key
- ✅ Any WinKeyer USB device (WKUSB, WKmini, WK3serial, etc.)

## Setup

1. Plug in your WinKeyer USB
2. Open VBand in Chrome/Edge and join a channel
3. Set VBand's mode to match your key type:
   - **Iambic A or B** for paddles
   - **Bug** for a bug
   - **Straight Key** for a straight key
4. Launch WKLink, select your COM port, click **CONNECT**
5. Keep VBand as the active browser window while keying

## Build from source (Windows)

```
pip install -r requirements.txt
build.bat
```

The compiled `WKLink.exe` will appear in the `dist\` folder. No Python installation needed to run it.

## Notes

- **Mute WK sidetone** is enabled by default so you only hear VBand's audio, not double
- **Always on top** keeps WKLink visible above other windows so you can see WPM
- WPM display tracks your speed pot in real time
- VBand must remain the active browser tab while keying (browser limitation)
- There is ~1 character of latency between keying and VBand playback (inherent to echo mode)
- WK3 settings (iambic mode, bug mode, weighting) are set on the keyer itself, not in WKLink

## License

MIT — share freely, 73 de K5GRR
