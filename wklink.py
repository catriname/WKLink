"""
WKLink v2 — WinKeyer → VBand Direct Bridge
K5GRR

Directly forwards raw paddle contact state from WinKeyer status bytes to
VBand (hamradio.solutions/vband) as Left Ctrl (dit) / Right Ctrl (dah) keypresses.

The WinKeyer hardware handles all element timing. VBand handles iambic
sequencing. This app is a transparent bridge — zero timing added in software.

Works with iambic paddles, bugs, and straight keys.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import serial
import serial.tools.list_ports
import threading
import time
from datetime import datetime

try:
    from pynput.keyboard import Key, Controller as KeyboardController
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

# ── WinKeyer protocol constants ───────────────────────────────────────────────

WK_BAUD         = 1200
WK_HOST_OPEN    = bytes([0x00, 0x02])
WK_HOST_CLOSE   = bytes([0x00, 0x03])
WK_SET_MODE_CMD = 0x0E
WK_SET_SIDETONE = 0x01

# Mode byte (command 0x0E — WinkeyerMode register):
#   Bit 7: PDY   — paddle watchdog disable  (1 = disabled, prevents WK resetting our mode)
#   Bit 6: PECHO — paddle echo              (1 = WK echoes decoded chars back to host for log display)
#   Bit 5-4: keyer mode                     (00 = Iambic B; not relevant since VBand does its own iambic)
#   Bit 3: SWAP  — swap dit/dah inputs      (1 = swapped)
#   Bit 2: AUTOSPACE                        (0 = off)
#   Bit 1: CTspc — contest spacing          (0 = off)
#   Bit 0: SECHO — serial echo              (0 = off)
WK_MODE_BASE = 0xC0   # PDY=1 (watchdog off) | PECHO=1 (echo chars for log)
WK_MODE_SWAP = 0xC8   # WK_MODE_BASE | bit 3 (swap paddles)

# Byte-type identification
def is_status_byte(b): return (b & 0xC0) == 0xC0   # 0xC0–0xFF
def is_pot_byte(b):    return (b & 0xC0) == 0x80   # 0x80–0xBF

# Status byte bit masks (bits within the lower 6 bits):
#   Bit 5 (0x20): SP — speed pot active
#   Bit 4 (0x10): BK — break-in (any contact active)
#   Bit 3 (0x08): TU — tune mode
#   Bit 2 (0x04): PT — PTT output active
#   Bit 1 (0x02): DT — dit contact  (1 = closed / paddle pressed)
#   Bit 0 (0x01): DH — dah contact  (1 = closed / paddle pressed)
STATUS_DIT = 0x02
STATUS_DAH = 0x01


def pot_to_wpm(b, min_wpm=5, range_wpm=50):
    """Lower 5 bits of a pot byte (0–31) mapped to min_wpm .. min_wpm+range_wpm."""
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
        self.connected   = False
        self.current_wpm = 0

        # Contact state tracking (for edge detection)
        self._prev_dit = False
        self._prev_dah = False
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

            # Read version byte(s) from WK response
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

            # Set mode: paddle watchdog off, paddle echo on (for log), iambic B
            mode = WK_MODE_SWAP if self.swap_paddles.get() else WK_MODE_BASE
            self.serial_port.write(bytes([WK_SET_MODE_CMD, mode]))
            time.sleep(0.05)

            if self.mute_sidetone.get():
                self.serial_port.write(bytes([WK_SET_SIDETONE, 0x00]))
            time.sleep(0.05)

            self.connected  = True
            self._prev_dit  = False
            self._prev_dah  = False
            self._dit_held  = False
            self._dah_held  = False
            self._set_status(True)
            self.connect_btn.config(text='DISCONNECT', fg=self.RED)

            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()

        except serial.SerialException as e:
            self._log(f'ERROR: {e}', error=True)

    def _disconnect(self):
        was_connected = self.connected
        self.connected = False
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
        """Background thread: reads WinKeyer bytes, forwards contacts in real time."""
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
                    # PECHO decoded ASCII char — log display only
                    self._handle_echo(b)
            except serial.SerialException:
                break
            except Exception as e:
                self.after(0, lambda err=str(e): self._log(f'RX: {err}', error=True))

        self._release_keys()

    def _handle_status(self, b):
        """Forward raw dit/dah contact changes to VBand immediately."""
        dit = bool(b & STATUS_DIT)
        dah = bool(b & STATUS_DAH)

        if dit != self._prev_dit:
            self._prev_dit = dit
            self._press_dit(dit)

        if dah != self._prev_dah:
            self._prev_dah = dah
            self._press_dah(dah)

    def _press_dit(self, pressed):
        if not self.kb:
            return
        self._dit_held = pressed
        if pressed:
            self.kb.press(Key.ctrl_l)
            self.after(0, lambda: self.dit_dot.config(fg=self.GREEN))
        else:
            self.kb.release(Key.ctrl_l)
            self.after(0, lambda: self.dit_dot.config(fg=self.BORDER))

    def _press_dah(self, pressed):
        if not self.kb:
            return
        self._dah_held = pressed
        if pressed:
            self.kb.press(Key.ctrl_r)
            self.after(0, lambda: self.dah_dot.config(fg=self.AMBER))
        else:
            self.kb.release(Key.ctrl_r)
            self.after(0, lambda: self.dah_dot.config(fg=self.BORDER))

    def _handle_pot(self, b):
        wpm = pot_to_wpm(b)
        self.current_wpm = wpm
        self.after(0, lambda w=wpm: self.wpm_lbl.config(text=str(w)))

    def _handle_echo(self, b):
        """PECHO byte — decoded char from WK, display in log only."""
        try:
            char = chr(b).upper()
            if char.isprintable():
                self.after(0, lambda c=char: self._append_decoded(c))
        except Exception:
            pass

    def _release_keys(self):
        """Safety: release any held Ctrl keys on disconnect or thread exit."""
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
        val = 0x00 if self.mute_sidetone.get() else 0x04  # 0x04 = ~1000 Hz
        self.serial_port.write(bytes([WK_SET_SIDETONE, val]))

    def _apply_swap(self):
        """Toggle paddle swap on the WinKeyer — takes effect live."""
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
