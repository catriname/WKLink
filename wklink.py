"""
WKLink - WinKeyer to VBand Bridge
Connects a K1EL WinKeyer USB device to VBand (hamradio.solutions/vband)
by translating paddle echo bytes into simulated keyboard presses.

Supports iambic paddles, bugs, and straight keys.
Author: K5GRR
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import serial
import serial.tools.list_ports
import threading
import time
import queue
from datetime import datetime

try:
    from pynput.keyboard import Key, Controller as KeyboardController
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

# ── Morse code table ──────────────────────────────────────────────────────────
MORSE = {
    'A': '.-',   'B': '-...', 'C': '-.-.', 'D': '-..',  'E': '.',
    'F': '..-.', 'G': '--.',  'H': '....', 'I': '..',   'J': '.---',
    'K': '-.-',  'L': '.-..', 'M': '--',   'N': '-.',   'O': '---',
    'P': '.--.', 'Q': '--.-', 'R': '.-.',  'S': '...',  'T': '-',
    'U': '..-',  'V': '...-', 'W': '.--',  'X': '-..-', 'Y': '-.--',
    'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
    '.': '.-.-.-', ',': '--..--', '?': '..--..', '/': '-..-.',
    'AR': '.-.-.', 'SK': '...-.-', 'KN': '-.--.',  'BT': '-...-',
}

# ── WinKeyer protocol constants ───────────────────────────────────────────────
WK_ADMIN        = 0x00
WK_ADMIN_OPEN   = 0x02
WK_ADMIN_CLOSE  = 0x05
WK_SET_WPM      = 0x02
WK_SET_SIDETONE = 0x01
WK_SET_MODE     = 0x0E
WK_BAUD         = 1200

# Mode byte bits
WK_MODE_PADDLE_ECHO = (1 << 6)  # bit 6: echo paddle input as ASCII
WK_MODE_BUG         = (1 << 2)  # bit 2: bug mode (combined with iambic sel)
# Iambic mode A = 0b00, B = 0b00 with bit set, Bug = see docs
# We set mode to enable echo; iambic/bug mode is set on the keyer itself

# Status byte identification
def is_status_byte(b):  return (b & 0xC0) == 0xC0
def is_pot_byte(b):     return (b & 0xC0) == 0x80
def pot_wpm(b, min_wpm=10, range_wpm=20):
    """Convert pot byte to WPM. Range 0-31 mapped to min_wpm..min_wpm+range_wpm."""
    val = b & 0x1F
    return min_wpm + int(val * range_wpm / 31)


# ── Main Application ──────────────────────────────────────────────────────────
class WKLink(tk.Tk):

    GREEN      = '#00ff41'
    DIM_GREEN  = '#007a1e'
    BG         = '#0a0f0a'
    PANEL      = '#0f1a0f'
    BORDER     = '#1a3a1a'
    RED        = '#ff4444'
    AMBER      = '#ffb347'
    WHITE      = '#d0ffd0'
    FONT_MONO  = ('Consolas', 10)
    FONT_LARGE = ('Consolas', 28, 'bold')
    FONT_MED   = ('Consolas', 12, 'bold')
    FONT_SM    = ('Consolas', 9)

    def __init__(self):
        super().__init__()
        self.title('WKLink  ·  WinKeyer → VBand')
        self.configure(bg=self.BG)
        self.resizable(False, False)

        self.serial_port  = None
        self.read_thread  = None
        self.send_thread  = None
        self.connected    = False
        self.current_wpm  = 20
        self.send_queue   = queue.Queue()
        self.kb           = KeyboardController() if KEYBOARD_AVAILABLE else None
        self.mute_sidetone = tk.BooleanVar(value=True)
        self.always_on_top = tk.BooleanVar(value=True)
        self.key_sending   = False  # prevent overlapping send jobs

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

        # status dot
        self.status_dot = tk.Label(hdr, text='●', font=('Consolas', 14),
                                   bg=self.BG, fg=self.RED)
        self.status_dot.pack(side='right')
        self.status_lbl = tk.Label(hdr, text='OFFLINE', font=self.FONT_SM,
                                   bg=self.BG, fg=self.RED)
        self.status_lbl.pack(side='right', padx=(0, 4))

        sep = tk.Frame(self, bg=self.BORDER, height=1)
        sep.pack(fill='x', padx=10)

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

        self.scan_btn = tk.Button(row1, text='⟳', font=('Consolas', 11, 'bold'),
                                  bg=self.PANEL, fg=self.DIM_GREEN, relief='flat',
                                  activebackground=self.BORDER, activeforeground=self.GREEN,
                                  bd=0, cursor='hand2', command=self._scan_ports)
        self.scan_btn.pack(side='left', padx=(0, 10))

        self.connect_btn = tk.Button(row1, text='CONNECT', font=self.FONT_MED,
                                     bg=self.PANEL, fg=self.GREEN, relief='flat',
                                     activebackground=self.DIM_GREEN, activeforeground=self.BG,
                                     bd=1, cursor='hand2', padx=12,
                                     command=self._toggle_connect)
        self.connect_btn.pack(side='left')

        # ── WPM display ───────────────────────────────────────────────────
        wpm_frame = tk.Frame(self, bg=self.PANEL, relief='flat', bd=0,
                             highlightbackground=self.BORDER, highlightthickness=1)
        wpm_frame.pack(fill='x', padx=10, pady=(0, 4))

        inner = tk.Frame(wpm_frame, bg=self.PANEL)
        inner.pack(fill='x', padx=12, pady=8)

        tk.Label(inner, text='WPM', font=self.FONT_SM, bg=self.PANEL,
                 fg=self.DIM_GREEN).pack(side='left')
        self.wpm_lbl = tk.Label(inner, text='--', font=self.FONT_LARGE,
                                bg=self.PANEL, fg=self.GREEN)
        self.wpm_lbl.pack(side='left', padx=(8, 0))

        # paddle indicator
        right_side = tk.Frame(inner, bg=self.PANEL)
        right_side.pack(side='right')
        tk.Label(right_side, text='DIT', font=self.FONT_SM, bg=self.PANEL,
                 fg=self.DIM_GREEN).grid(row=0, column=0, padx=4)
        tk.Label(right_side, text='DAH', font=self.FONT_SM, bg=self.PANEL,
                 fg=self.DIM_GREEN).grid(row=0, column=1, padx=4)
        self.dit_dot = tk.Label(right_side, text='●', font=('Consolas', 16),
                                bg=self.PANEL, fg=self.BORDER)
        self.dit_dot.grid(row=1, column=0, padx=4)
        self.dah_dot = tk.Label(right_side, text='●', font=('Consolas', 16),
                                bg=self.PANEL, fg=self.BORDER)
        self.dah_dot.grid(row=1, column=1, padx=4)

        # ── Options row ───────────────────────────────────────────────────
        opts = tk.Frame(self, bg=self.BG)
        opts.pack(fill='x', padx=10, pady=(2, 4))

        self._checkbox(opts, 'Mute WK sidetone', self.mute_sidetone,
                       cmd=self._apply_sidetone).pack(side='left', padx=(0, 16))
        self._checkbox(opts, 'Always on top', self.always_on_top,
                       cmd=self._apply_always_on_top).pack(side='left')

        # ── Info / log ────────────────────────────────────────────────────
        sep2 = tk.Frame(self, bg=self.BORDER, height=1)
        sep2.pack(fill='x', padx=10, pady=(2, 0))

        log_frame = tk.Frame(self, bg=self.BG)
        log_frame.pack(fill='both', expand=True, padx=10, pady=(4, 0))

        tk.Label(log_frame, text='DECODED OUTPUT', font=self.FONT_SM,
                 bg=self.BG, fg=self.DIM_GREEN, anchor='w').pack(fill='x')

        self.log = scrolledtext.ScrolledText(
            log_frame, height=6, bg=self.PANEL, fg=self.GREEN,
            font=self.FONT_MONO, relief='flat', bd=0, insertbackground=self.GREEN,
            selectbackground=self.DIM_GREEN, wrap='word', state='disabled',
            highlightbackground=self.BORDER, highlightthickness=1)
        self.log.pack(fill='both', expand=True, pady=(2, 0))

        # ── Footer ────────────────────────────────────────────────────────
        footer = tk.Frame(self, bg=self.BG)
        footer.pack(fill='x', padx=10, pady=(4, 8))
        tk.Label(footer,
                 text='Keep VBand active in browser  ·  Set VBand to Iambic or Straight Key to match your key type',
                 font=self.FONT_SM, bg=self.BG, fg=self.DIM_GREEN).pack()

        self.geometry('420x420')

    def _checkbox(self, parent, text, var, cmd=None):
        return tk.Checkbutton(parent, text=text, variable=var, font=self.FONT_SM,
                              bg=self.BG, fg=self.DIM_GREEN, activebackground=self.BG,
                              activeforeground=self.GREEN, selectcolor=self.BG,
                              relief='flat', bd=0, cursor='hand2',
                              command=cmd)

    def _style_combobox(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=self.PANEL,
                        background=self.PANEL, foreground=self.GREEN,
                        selectbackground=self.BORDER, selectforeground=self.GREEN,
                        arrowcolor=self.DIM_GREEN, bordercolor=self.BORDER)
        style.map('TCombobox',
                  fieldbackground=[('readonly', self.PANEL), ('disabled', self.PANEL)],
                  foreground=[('readonly', self.GREEN), ('disabled', self.DIM_GREEN)])
        # Style the dropdown listbox (Windows ignores ttk style for this)
        self.option_add('*TCombobox*Listbox.background', self.PANEL)
        self.option_add('*TCombobox*Listbox.foreground', self.GREEN)
        self.option_add('*TCombobox*Listbox.selectBackground', self.BORDER)
        self.option_add('*TCombobox*Listbox.selectForeground', self.GREEN)

    # ── Port scanning ─────────────────────────────────────────────────────────

    def _scan_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb['values'] = ports
        if ports:
            # prefer ports with FTDI/WK description
            best = next((p.device for p in serial.tools.list_ports.comports()
                         if 'FTDI' in (p.description or '') or
                            'WinKey' in (p.description or '') or
                            'CH340' in (p.description or '')), ports[0])
            self.port_var.set(best)
        self._log(f'Found {len(ports)} port(s)')

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def _toggle_connect(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port:
            self._log('ERROR: No port selected', error=True)
            return
        try:
            self.serial_port = serial.Serial(port, WK_BAUD, timeout=0.1)
            time.sleep(0.5)

            # Admin:Close first to reset any previous host session
            self.serial_port.reset_input_buffer()
            self.serial_port.write(bytes([WK_ADMIN, 0x03]))  # sub-cmd 0x03 = HostClose
            time.sleep(1.0)  # WK needs time to fully reset
            self.serial_port.reset_input_buffer()

            # Admin:Open
            self.serial_port.write(bytes([WK_ADMIN, WK_ADMIN_OPEN]))
            deadline = time.time() + 1.0
            resp = b''
            while time.time() < deadline:
                if self.serial_port.in_waiting:
                    resp += self.serial_port.read(self.serial_port.in_waiting)
                    if len(resp) >= 2:
                        break
                time.sleep(0.01)

            ver = None
            for i in range(len(resp) - 1):
                if resp[i] == 0x00 and 0x10 <= resp[i + 1] <= 0x40:
                    ver = resp[i + 1]
                    break
            if ver is None and resp and 0x10 <= resp[-1] <= 0x40:
                ver = resp[-1]
            self._log(f'WinKeyer connected  firmware v{ver}' if ver else 'WinKeyer open — no version')

            # Mode 0xCE: watchdog off, paddle echo, iambic B, paddle swap, serial echo, auto space
            mode_byte = 0xCE
            self.serial_port.write(bytes([WK_SET_MODE, mode_byte]))
            time.sleep(0.1)

            # Optionally mute sidetone
            if self.mute_sidetone.get():
                self.serial_port.write(bytes([WK_SET_SIDETONE, 0x00]))

            self.connected = True
            self._set_status(True)
            self.connect_btn.config(text='DISCONNECT', fg=self.RED)

            # Start read thread
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()

            # Start send worker
            self.send_thread = threading.Thread(target=self._send_worker, daemon=True)
            self.send_thread.start()

        except serial.SerialException as e:
            self._log(f'ERROR: {e}', error=True)

    def _disconnect(self):
        self.connected = False
        try:
            if self.serial_port and self.serial_port.is_open:
                # Restore sidetone
                if self.mute_sidetone.get():
                    self.serial_port.write(bytes([WK_SET_SIDETONE, 0x04]))  # 1000 Hz (4000/4)
                # Admin:Close
                self.serial_port.write(bytes([WK_ADMIN, 0x03]))  # HostClose
                time.sleep(0.05)
                self.serial_port.close()
        except Exception:
            pass
        self._set_status(False)
        self.connect_btn.config(text='CONNECT', fg=self.GREEN)
        self._log('Disconnected')

    # ── WinKeyer read loop ────────────────────────────────────────────────────

    def _read_loop(self):
        """Runs in background thread. Reads bytes from WinKeyer and dispatches."""
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
                    self._handle_echo(b)
            except serial.SerialException:
                break
            except Exception as e:
                self.after(0, lambda err=str(e): self._log(f'RX ERROR: {err}', error=True))

    def _handle_status(self, b):
        # Bit 1 = BreakIn (paddle active)
        # Bit 0 = XOFF
        paddle_active = bool(b & 0x02)
        if paddle_active:
            self.after(0, lambda: self.dit_dot.config(fg=self.GREEN))
            self.after(0, lambda: self.dah_dot.config(fg=self.AMBER))
        else:
            self.after(0, lambda: self.dit_dot.config(fg=self.BORDER))
            self.after(0, lambda: self.dah_dot.config(fg=self.BORDER))

    def _handle_pot(self, b):
        wpm = pot_wpm(b)
        self.current_wpm = wpm
        self.after(0, lambda: self.wpm_lbl.config(text=str(wpm)))

    def _handle_echo(self, b):
        """An ASCII character was just sent by the WK. Queue it for VBand replay."""
        try:
            char = chr(b).upper()
            if char in MORSE or char == ' ':
                self.send_queue.put(char)
                self.after(0, lambda c=char: self._append_log(c))
        except Exception:
            pass

    # ── VBand keypress replay ─────────────────────────────────────────────────

    def _send_worker(self):
        """Sends Ctrl keypresses to VBand based on decoded echo characters."""
        while self.connected:
            try:
                char = self.send_queue.get(timeout=0.2)
                if self.kb:
                    self._play_char(char)
            except queue.Empty:
                continue
            except Exception:
                pass

    def _play_char(self, char):
        """Simulate dit/dah keypresses for one character at current WPM."""
        wpm = self.current_wpm
        dit_ms = 1200.0 / wpm / 1000.0  # dit duration in seconds

        if char == ' ':
            # Word space: 7 dits, minus 3 already from letter space
            time.sleep(dit_ms * 4)
            return

        pattern = MORSE.get(char, '')
        for i, sym in enumerate(pattern):
            if not self.connected:
                break
            if sym == '.':
                # dit = Left Ctrl
                self.kb.press(Key.ctrl_l)
                time.sleep(dit_ms)
                self.kb.release(Key.ctrl_l)
            else:
                # dah = Right Ctrl (3 dits)
                self.kb.press(Key.ctrl_r)
                time.sleep(dit_ms * 3)
                self.kb.release(Key.ctrl_r)

            # Inter-element space (1 dit) unless last element
            if i < len(pattern) - 1:
                time.sleep(dit_ms)

        # Inter-letter space (3 dits total, 1 already waited)
        time.sleep(dit_ms * 2)

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg, error=False):
        ts  = datetime.now().strftime('%H:%M:%S')
        fg  = self.RED if error else self.DIM_GREEN
        self.log.config(state='normal')
        self.log.insert('end', f'[{ts}] {msg}\n', ('col',))
        self.log.tag_config('col', foreground=fg)
        self.log.see('end')
        self.log.config(state='disabled')

    def _append_log(self, char):
        """Append a decoded character to the log (inline, no newline unless space)."""
        self.log.config(state='normal')
        if char == ' ':
            self.log.insert('end', '  ')
        else:
            self.log.insert('end', char, ('decoded',))
        self.log.tag_config('decoded', foreground=self.GREEN, font=('Consolas', 11, 'bold'))
        self.log.see('end')
        self.log.config(state='disabled')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, online):
        color = self.GREEN if online else self.RED
        text  = 'ONLINE' if online else 'OFFLINE'
        self.status_dot.config(fg=color)
        self.status_lbl.config(fg=color, text=text)

    def _apply_always_on_top(self):
        self.attributes('-topmost', self.always_on_top.get())

    def _apply_sidetone(self):
        if not self.connected or not self.serial_port:
            return
        if self.mute_sidetone.get():
            self.serial_port.write(bytes([WK_SET_SIDETONE, 0x00]))
        else:
            self.serial_port.write(bytes([WK_SET_SIDETONE, 0x04]))  # 1000 Hz (4000/4)

    def _on_close(self):
        if self.connected:
            self._disconnect()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = WKLink()
    app.mainloop()
