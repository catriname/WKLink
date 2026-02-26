"""
WKLink v2 — WinKeyer → VBand Bridge
K5GRR

How it works:
  WinKeyer runs in host mode with paddle echo (PECHO) enabled.  As you key,
  WK decodes each element and echoes the completed character as ASCII.
  WKLink receives those characters, maps each element (dit/dah) to the
  matching Ctrl keypress that VBand listens for, and holds the key for
  exactly the element's duration at the current WPM.

  No inter-character sleep is added — the natural cadence of WK echo bytes
  IS the gap between characters.  Held paddles produce a continuous stream
  of echo bytes; WKLink forwards them as fast as they arrive.

  WK status bytes are used only to track the speed pot (WPM display) and
  as a safety net to release stuck keys on disconnect.  They are NOT used
  for dit/dah detection — the WK status byte does not contain individual
  paddle contact bits.

Works with iambic paddles, bugs, and straight keys.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import serial
import serial.tools.list_ports
import threading
import queue
import time
from datetime import datetime

try:
    from pynput.keyboard import Key, Controller as KeyboardController
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

# ── Morse table ───────────────────────────────────────────────────────────────

MORSE = {
    'A': '.-',   'B': '-...', 'C': '-.-.', 'D': '-..',  'E': '.',
    'F': '..-.', 'G': '--.',  'H': '....', 'I': '..',   'J': '.---',
    'K': '-.-',  'L': '.-..', 'M': '--',   'N': '-.',   'O': '---',
    'P': '.--.', 'Q': '--.-', 'R': '.-.',  'S': '...',  'T': '-',
    'U': '..-',  'V': '...-', 'W': '.--',  'X': '-..-', 'Y': '-.--',
    'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
}

# ── WinKeyer protocol constants ───────────────────────────────────────────────

WK_BAUD         = 1200
WK_HOST_OPEN    = bytes([0x00, 0x02])
WK_HOST_CLOSE   = bytes([0x00, 0x03])
WK_SET_MODE_CMD = 0x0E
WK_SET_SIDETONE = 0x01

# Mode byte (command 0x0E — WinkeyerMode register):
#   Bit 7: PDY   — paddle watchdog disable  (1 = disabled)
#   Bit 6: PECHO — paddle echo              (1 = WK echoes decoded ASCII to host)
#   Bit 5-4: keyer mode                     (00 = Iambic B)
#   Bit 3: SWAP  — swap dit/dah inputs      (1 = swapped)
#   Bit 2: AUTOSPACE                        (0 = off)
#   Bit 1: CTspc — contest spacing          (0 = off)
#   Bit 0: SECHO — serial echo              (0 = off)
WK_MODE_BASE = 0xC0   # PDY=1 | PECHO=1
WK_MODE_SWAP = 0xC8   # WK_MODE_BASE | bit 3

# Byte-type identification
def is_status_byte(b): return (b & 0xC0) == 0xC0   # 0xC0–0xFF
def is_pot_byte(b):    return (b & 0xC0) == 0x80   # 0x80–0xBF


def pot_to_wpm(b, min_wpm=5, range_wpm=50):
    """Lower 5 bits of a pot byte (0–31) mapped to min_wpm..min_wpm+range_wpm."""
    return min_wpm + round((b & 0x1F) * range_wpm / 31)


# ── Application ───────────────────────────────────────────────────────────────

class WKLink(tk.Tk):

    GREEN      = '#00ff41'
    DIM_GREEN  = '#007a1e'
    BG         = '#0a0f0a'
    PANEL      = '#0f1a0f'
    BORDER     = '#1a3a1a'
    RED        = '#ff4444'
    AMBER      = '#ffb347'
    FONT_MONO  = ('Consolas', 10)
    FONT_LARGE = ('Consolas', 28, 'bold')
    FONT_MED   = ('Consolas', 12, 'bold')
    FONT_SM    = ('Consolas', 9)

    def __init__(self):
        super().__init__()
        self.title('WKLink  ·  WinKeyer → VBand')
        self.configure(bg=self.BG)
        self.resizable(False, False)

        # Connection state
        self.serial_port = None
        self.read_thread = None
        self.send_thread = None
        self.connected   = False
        self.current_wpm = 20       # default until pot byte arrives

        # VBand forwarding queue
        self.send_queue  = queue.Queue()

        # Held-key tracking (safety release only)
        self._dit_held = False
        self._dah_held = False

        # Settings
        self.mute_sidetone = tk.BooleanVar(value=True)
        self.always_on_top = tk.BooleanVar(value=True)
        self.swap_paddles  = tk.BooleanVar(value=False)

        self.kb = KeyboardController() if KEYBOARD_AVAILABLE else None

        self._build_ui()
        self._scan_ports()
        self._apply_always_on_top()
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        pad = dict(padx=10, pady=6)

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=self.BG)
        hdr.pack(fill='x', **pad)
        tk.Label(hdr, text='⊻ WKLINK', font=('Consolas', 18, 'bold'),
                 bg=self.BG, fg=self.GREEN).pack(side='left')
        tk.Label(hdr, text='WinKeyer → VBand Bridge', font=self.FONT_SM,
                 bg=self.BG, fg=self.DIM_GREEN).pack(side='left', padx=(8, 0), pady=(6, 0))

        self.status_dot = tk.Label(hdr, text='●', font=('Consolas', 14),
                                   bg=self.BG, fg=self.RED)
        self.status_dot.pack(side='right')
        self.status_lbl = tk.Label(hdr, text='OFFLINE', font=self.FONT_SM,
                                   bg=self.BG, fg=self.RED)
        self.status_lbl.pack(side='right', padx=(0, 4))

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill='x', padx=10)

        # ── Port row ──────────────────────────────────────────────────────
        row1 = tk.Frame(self, bg=self.BG)
        row1.pack(fill='x', **pad)
        tk.Label(row1, text='PORT', font=self.FONT_SM, bg=self.BG,
                 fg=self.DIM_GREEN, width=6, anchor='w').pack(side='left')
        self.port_var = tk.StringVar()
        self.port_cb  = ttk.Combobox(row1, textvariable=self.port_var, width=14,
                                     state='readonly', font=self.FONT_MONO)
        self._style_combobox()
        self.port_cb.pack(side='left', padx=(0, 6))
        tk.Button(row1, text='⟳', font=('Consolas', 11, 'bold'),
                  bg=self.PANEL, fg=self.DIM_GREEN, relief='flat',
                  activebackground=self.BORDER, activeforeground=self.GREEN,
                  bd=0, cursor='hand2', command=self._scan_ports
                  ).pack(side='left', padx=(0, 10))
        self.connect_btn = tk.Button(
            row1, text='CONNECT', font=self.FONT_MED,
            bg=self.PANEL, fg=self.GREEN, relief='flat',
            activebackground=self.DIM_GREEN, activeforeground=self.BG,
            bd=1, cursor='hand2', padx=12, command=self._toggle_connect)
        self.connect_btn.pack(side='left')

        # ── WPM + contact indicators ──────────────────────────────────────
        wpm_frame = tk.Frame(self, bg=self.PANEL, relief='flat', bd=0,
                             highlightbackground=self.BORDER, highlightthickness=1)
        wpm_frame.pack(fill='x', padx=10, pady=(0, 4))
        inner = tk.Frame(wpm_frame, bg=self.PANEL)
        inner.pack(fill='x', padx=12, pady=8)

        tk.Label(inner, text='WPM', font=self.FONT_SM,
                 bg=self.PANEL, fg=self.DIM_GREEN).pack(side='left')
        self.wpm_lbl = tk.Label(inner, text='--', font=self.FONT_LARGE,
                                bg=self.PANEL, fg=self.GREEN)
        self.wpm_lbl.pack(side='left', padx=(8, 0))

        indicators = tk.Frame(inner, bg=self.PANEL)
        indicators.pack(side='right')
        tk.Label(indicators, text='DIT', font=self.FONT_SM,
                 bg=self.PANEL, fg=self.DIM_GREEN).grid(row=0, column=0, padx=4)
        tk.Label(indicators, text='DAH', font=self.FONT_SM,
                 bg=self.PANEL, fg=self.DIM_GREEN).grid(row=0, column=1, padx=4)
        self.dit_dot = tk.Label(indicators, text='●', font=('Consolas', 16),
                                bg=self.PANEL, fg=self.BORDER)
        self.dit_dot.grid(row=1, column=0, padx=4)
        self.dah_dot = tk.Label(indicators, text='●', font=('Consolas', 16),
                                bg=self.PANEL, fg=self.BORDER)
        self.dah_dot.grid(row=1, column=1, padx=4)

        # ── Options ───────────────────────────────────────────────────────
        opts = tk.Frame(self, bg=self.BG)
        opts.pack(fill='x', padx=10, pady=(2, 4))
        self._cb(opts, 'Mute WK sidetone', self.mute_sidetone,
                 cmd=self._apply_sidetone).pack(side='left', padx=(0, 12))
        self._cb(opts, 'Always on top', self.always_on_top,
                 cmd=self._apply_always_on_top).pack(side='left', padx=(0, 12))
        self._cb(opts, 'Swap paddles', self.swap_paddles,
                 cmd=self._apply_swap).pack(side='left')

        # ── Log ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill='x', padx=10, pady=(2, 0))
        log_frame = tk.Frame(self, bg=self.BG)
        log_frame.pack(fill='both', expand=True, padx=10, pady=(4, 0))
        tk.Label(log_frame, text='DECODED OUTPUT', font=self.FONT_SM,
                 bg=self.BG, fg=self.DIM_GREEN, anchor='w').pack(fill='x')
        self.log_box = scrolledtext.ScrolledText(
            log_frame, height=6, bg=self.PANEL, fg=self.GREEN,
            font=self.FONT_MONO, relief='flat', bd=0,
            insertbackground=self.GREEN, selectbackground=self.DIM_GREEN,
            wrap='word', state='disabled',
            highlightbackground=self.BORDER, highlightthickness=1)
        self.log_box.pack(fill='both', expand=True, pady=(2, 0))

        # ── Footer ────────────────────────────────────────────────────────
        footer = tk.Frame(self, bg=self.BG)
        footer.pack(fill='x', padx=10, pady=(4, 8))
        tk.Label(footer,
                 text='VBand must remain the active browser tab while keying',
                 font=self.FONT_SM, bg=self.BG, fg=self.DIM_GREEN).pack()

        self.geometry('420x430')

    def _cb(self, parent, text, var, cmd=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, font=self.FONT_SM,
            bg=self.BG, fg=self.DIM_GREEN, activebackground=self.BG,
            activeforeground=self.GREEN, selectcolor=self.BG,
            relief='flat', bd=0, cursor='hand2', command=cmd)

    def _style_combobox(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure('TCombobox',
                    fieldbackground=self.PANEL, background=self.PANEL,
                    foreground=self.GREEN, selectbackground=self.BORDER,
                    selectforeground=self.GREEN, arrowcolor=self.DIM_GREEN,
                    bordercolor=self.BORDER)
        s.map('TCombobox',
              fieldbackground=[('readonly', self.PANEL), ('disabled', self.PANEL)],
              foreground=[('readonly', self.GREEN), ('disabled', self.DIM_GREEN)])
        self.option_add('*TCombobox*Listbox.background', self.PANEL)
        self.option_add('*TCombobox*Listbox.foreground', self.GREEN)
        self.option_add('*TCombobox*Listbox.selectBackground', self.BORDER)
        self.option_add('*TCombobox*Listbox.selectForeground', self.GREEN)

    # ── Port scanning ─────────────────────────────────────────────────────────

    def _scan_ports(self):
        ports = list(serial.tools.list_ports.comports())
        devices = [p.device for p in ports]
        self.port_cb['values'] = devices
        if devices:
            best = next(
                (p.device for p in ports
                 if any(kw in (p.description or '')
                        for kw in ('FTDI', 'WinKey', 'CH340', 'Silicon'))),
                devices[0])
            self.port_var.set(best)
        self._log(f'Found {len(devices)} serial port(s)')

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def _toggle_connect(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port:
            self._log('ERROR: no port selected', error=True)
            return
        try:
            self.serial_port = serial.Serial(port, WK_BAUD, timeout=0.05)
            time.sleep(0.3)
            self.serial_port.reset_input_buffer()

            # Close any stale host session, then open fresh
            self.serial_port.write(WK_HOST_CLOSE)
            time.sleep(0.5)
            self.serial_port.reset_input_buffer()
            self.serial_port.write(WK_HOST_OPEN)

            # Wait for version byte(s)
            deadline = time.time() + 1.5
            resp = b''
            while time.time() < deadline:
                chunk = self.serial_port.read(4)
                if chunk:
                    resp += chunk
                if len(resp) >= 2:
                    break
                time.sleep(0.01)

            ver = next((b for b in resp if 0x10 <= b <= 0x40), None)
            self._log(f'WinKeyer v{ver} connected' if ver else 'WinKeyer open (version unknown)')

            # Mode: paddle watchdog off, PECHO on, iambic B
            mode = WK_MODE_SWAP if self.swap_paddles.get() else WK_MODE_BASE
            self.serial_port.write(bytes([WK_SET_MODE_CMD, mode]))
            time.sleep(0.05)

            if self.mute_sidetone.get():
                self.serial_port.write(bytes([WK_SET_SIDETONE, 0x00]))
            time.sleep(0.05)

            self.connected  = True
            self._dit_held  = False
            self._dah_held  = False

            # Drain the send queue in case anything is left from a previous session
            while not self.send_queue.empty():
                try:
                    self.send_queue.get_nowait()
                except queue.Empty:
                    break

            self._set_status(True)
            self.connect_btn.config(text='DISCONNECT', fg=self.RED)

            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()

            self.send_thread = threading.Thread(target=self._send_worker, daemon=True)
            self.send_thread.start()

        except serial.SerialException as e:
            self._log(f'ERROR: {e}', error=True)

    def _disconnect(self):
        was_connected = self.connected
        self.connected = False

        # Unblock the send worker so it can exit cleanly
        self.send_queue.put(None)

        self._release_keys()
        try:
            if self.serial_port and self.serial_port.is_open:
                if was_connected and self.mute_sidetone.get():
                    self.serial_port.write(bytes([WK_SET_SIDETONE, 0x04]))  # restore 1000 Hz
                self.serial_port.write(WK_HOST_CLOSE)
                time.sleep(0.05)
                self.serial_port.close()
        except Exception:
            pass

        self._set_status(False)
        self.connect_btn.config(text='CONNECT', fg=self.GREEN)
        self.after(0, lambda: self.dit_dot.config(fg=self.BORDER))
        self.after(0, lambda: self.dah_dot.config(fg=self.BORDER))
        self._log('Disconnected')

    # ── Read loop ─────────────────────────────────────────────────────────────

    def _read_loop(self):
        """Background thread: reads WinKeyer bytes."""
        while self.connected:
            try:
                if not self.serial_port or not self.serial_port.is_open:
                    break
                raw = self.serial_port.read(1)
                if not raw:
                    continue
                b = raw[0]
                if is_status_byte(b):
                    self._handle_status(b)
                elif is_pot_byte(b):
                    self._handle_pot(b)
                else:
                    # PECHO: a decoded ASCII character — forward to VBand
                    self._handle_echo(b)
            except serial.SerialException:
                break
            except Exception as e:
                self.after(0, lambda err=str(e): self._log(f'RX: {err}', error=True))

        self._release_keys()

    def _handle_status(self, b):
        """Status bytes are used only for safety — not for dit/dah detection.

        The WK status byte does NOT carry individual paddle contact bits.
        Bit 1 (0x02) is BreakIn (any paddle) and bit 0 is XOFF.  Treating
        them as dit/dah contact bits produces spurious repeating keypresses.
        Element detection is done via PECHO bytes instead.
        """
        pass  # nothing to do; safety key release is handled on disconnect

    def _handle_pot(self, b):
        wpm = pot_to_wpm(b)
        self.current_wpm = wpm
        self.after(0, lambda w=wpm: self.wpm_lbl.config(text=str(w)))

    def _handle_echo(self, b):
        """PECHO decoded character — queue for VBand forwarding and log display."""
        try:
            char = chr(b).upper()
            if char in MORSE:
                self.send_queue.put(char)
                self.after(0, lambda c=char: self._append_decoded(c))
            elif char == ' ':
                self.send_queue.put(' ')
                self.after(0, lambda: self._append_decoded(' '))
        except Exception:
            pass

    # ── VBand send worker ─────────────────────────────────────────────────────

    def _send_worker(self):
        """Forwards decoded characters to VBand as Ctrl keypresses.

        Each element is held for its exact duration at current WPM.
        No inter-character sleep is added — the natural gap between WK echo
        bytes is the inter-character spacing.
        """
        while self.connected:
            try:
                char = self.send_queue.get(timeout=0.3)
                if char is None:        # sentinel: shutdown signal
                    break
                if char == ' ':
                    time.sleep(4 * (1.2 / max(5, self.current_wpm)))  # extra word gap
                    continue
                if self.kb and char in MORSE:
                    self._play_char(char)
            except queue.Empty:
                continue
            except Exception:
                pass

        self._release_keys()

    def _play_char(self, char):
        """Simulate dit/dah Ctrl keypresses for one character at current WPM."""
        wpm    = max(5, self.current_wpm)
        dit    = 1.2 / wpm          # dit duration in seconds

        pattern = MORSE.get(char, '')
        for i, sym in enumerate(pattern):
            if not self.connected:
                break
            if sym == '.':
                self._dit_held = True
                self.kb.press(Key.ctrl_l)
                self.after(0, lambda: self.dit_dot.config(fg=self.GREEN))
                time.sleep(dit)
                self.kb.release(Key.ctrl_l)
                self._dit_held = False
                self.after(0, lambda: self.dit_dot.config(fg=self.BORDER))
            else:
                self._dah_held = True
                self.kb.press(Key.ctrl_r)
                self.after(0, lambda: self.dah_dot.config(fg=self.AMBER))
                time.sleep(dit * 3)
                self.kb.release(Key.ctrl_r)
                self._dah_held = False
                self.after(0, lambda: self.dah_dot.config(fg=self.BORDER))

            # Inter-element space (1 dit) between elements within a character
            if i < len(pattern) - 1:
                time.sleep(dit)

        # No inter-character sleep — WK echo timing provides the natural gap

    def _release_keys(self):
        """Safety: release any held Ctrl keys."""
        if not self.kb:
            return
        try:
            if self._dit_held:
                self.kb.release(Key.ctrl_l)
                self._dit_held = False
            if self._dah_held:
                self.kb.release(Key.ctrl_r)
                self._dah_held = False
        except Exception:
            pass

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg, error=False):
        ts = datetime.now().strftime('%H:%M:%S')
        fg = self.RED if error else self.DIM_GREEN
        self.log_box.config(state='normal')
        self.log_box.insert('end', f'\n[{ts}] {msg}', ('ev',))
        self.log_box.tag_config('ev', foreground=fg)
        self.log_box.see('end')
        self.log_box.config(state='disabled')

    def _append_decoded(self, char):
        """Append a PECHO-decoded character inline in the log."""
        self.log_box.config(state='normal')
        self.log_box.insert('end', ' ' if char == ' ' else char, ('dec',))
        self.log_box.tag_config('dec', foreground=self.GREEN,
                                font=('Consolas', 11, 'bold'))
        self.log_box.see('end')
        self.log_box.config(state='disabled')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, online):
        c = self.GREEN if online else self.RED
        self.status_dot.config(fg=c)
        self.status_lbl.config(fg=c, text='ONLINE' if online else 'OFFLINE')

    def _apply_always_on_top(self):
        self.attributes('-topmost', self.always_on_top.get())

    def _apply_sidetone(self):
        if not self.connected or not self.serial_port:
            return
        val = 0x00 if self.mute_sidetone.get() else 0x04
        self.serial_port.write(bytes([WK_SET_SIDETONE, val]))

    def _apply_swap(self):
        if not self.connected or not self.serial_port:
            return
        mode = WK_MODE_SWAP if self.swap_paddles.get() else WK_MODE_BASE
        self.serial_port.write(bytes([WK_SET_MODE_CMD, mode]))

    def _on_close(self):
        if self.connected:
            self._disconnect()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = WKLink()
    app.mainloop()
