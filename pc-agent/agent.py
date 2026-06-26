"""
GameZone PC Agent — ide na svaki gaming RAČUNAR u igraonici.
Kad istekne plaćeno vrijeme → zaključa ekran ("Vrijeme isteklo").
Start/doplata sa pulta → otključa. Bez instalacije (čist Python + tkinter).

Pokretanje:  python agent.py   (ili start-agent.bat)
Prvi put: izabereš pult (server) i koju stanicu ovaj PC predstavlja.
"""
import json
import os
import threading
import time
import tkinter as tk
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "config.json")
POLL_SEC = 2.0


def load_cfg():
    if os.path.exists(CFG):
        try:
            return json.load(open(CFG, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cfg(cfg):
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def http_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# ──────────────────────────────────────────────────────────
#  SETUP (prvi put) — izbor servera i stanice
# ──────────────────────────────────────────────────────────
def run_setup():
    cfg = load_cfg()
    win = tk.Tk()
    win.title("GameZone PC Agent — podešavanje")
    win.configure(bg="#0b0d18")
    win.geometry("440x440")
    fg = "#eef0f6"

    tk.Label(win, text="GameZone — podešavanje računara", bg="#0b0d18", fg=fg,
             font=("Segoe UI", 14, "bold")).pack(pady=(18, 4))
    tk.Label(win, text="Adresa pulta (server):", bg="#0b0d18", fg="#aeb4c6",
             font=("Segoe UI", 10)).pack(anchor="w", padx=22)
    srv_var = tk.StringVar(value=cfg.get("server", "http://192.168.1.230:8770"))
    srv = tk.Entry(win, textvariable=srv_var, font=("Segoe UI", 11), width=40)
    srv.pack(padx=22, pady=(2, 10))

    tk.Label(win, text="Stanice (klikni Učitaj pa izaberi):", bg="#0b0d18",
             fg="#aeb4c6", font=("Segoe UI", 10)).pack(anchor="w", padx=22)
    listbox = tk.Listbox(win, font=("Segoe UI", 11), height=9, bg="#11131f",
                         fg=fg, selectbackground="#7c5cff", activestyle="none")
    listbox.pack(padx=22, pady=(2, 8), fill="both", expand=True)
    stations = []

    status = tk.Label(win, text="", bg="#0b0d18", fg="#ffcb5b", font=("Segoe UI", 9))
    status.pack()

    def load_list():
        nonlocal stations
        listbox.delete(0, tk.END)
        base = srv_var.get().rstrip("/")
        try:
            data = http_json(base + "/api/stations")
            stations = data.get("stations", [])
            for s in stations:
                tag = "💻" if s.get("control") == "pc" else "📺"
                listbox.insert(tk.END, f"{tag}  {s.get('name','')}  ({s.get('control','')})")
            status.config(text=f"Učitano {len(stations)} stanica. Izaberi ovu na kojoj je ovaj PC.")
        except Exception as e:
            status.config(text=f"Greška: {e} — provjeri adresu pulta i da je server upaljen.")

    def save_and_close():
        sel = listbox.curselection()
        if not sel:
            status.config(text="Izaberi stanicu iz liste.")
            return
        st = stations[sel[0]]
        cfg["server"] = srv_var.get().rstrip("/")
        cfg["station_id"] = st["id"]
        cfg["station_name"] = st.get("name", "")
        save_cfg(cfg)
        win.destroy()

    btns = tk.Frame(win, bg="#0b0d18")
    btns.pack(pady=10)
    tk.Button(btns, text="Učitaj stanice", command=load_list,
              font=("Segoe UI", 10, "bold"), bg="#1a1d2e", fg=fg,
              relief="flat", padx=14, pady=6).pack(side="left", padx=6)
    tk.Button(btns, text="Sačuvaj i pokreni", command=save_and_close,
              font=("Segoe UI", 10, "bold"), bg="#7c5cff", fg="#0b0d18",
              relief="flat", padx=14, pady=6).pack(side="left", padx=6)

    load_list()
    win.mainloop()
    return load_cfg()


# ──────────────────────────────────────────────────────────
#  LOCK OVERLAY + glavna petlja
# ──────────────────────────────────────────────────────────
class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg["server"].rstrip("/")
        self.sid = cfg["station_id"]
        self.name = cfg.get("station_name", "")
        self.locked = None  # nepoznato
        self.remaining = 0

        self.root = tk.Tk()
        self.root.withdraw()  # glavni prozor skriven

        # lock overlay
        self.ov = tk.Toplevel(self.root)
        self.ov.configure(bg="#05060c")
        self.ov.overrideredirect(True)
        self.ov.attributes("-topmost", True)
        self._fullscreen()
        self.ov.withdraw()
        # blokiraj zatvaranje
        self.ov.protocol("WM_DELETE_WINDOW", lambda: None)
        for seq in ("<Alt-F4>", "<Control-w>", "<Escape>"):
            self.ov.bind(seq, lambda e: "break")
        # ADMIN sigurnosni izlaz: Ctrl+Alt+Q (ugasi agent)
        for seq in ("<Control-Alt-q>", "<Control-Alt-Q>"):
            self.ov.bind(seq, lambda e: self._quit())
            self.root.bind(seq, lambda e: self._quit())

        wrap = tk.Frame(self.ov, bg="#05060c")
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(wrap, text="🔒", bg="#05060c", fg="#ff5876",
                 font=("Segoe UI", 70)).pack()
        tk.Label(wrap, text="VRIJEME ISTEKLO", bg="#05060c", fg="#eef0f6",
                 font=("Segoe UI", 40, "bold")).pack(pady=(6, 2))
        self.lbl_name = tk.Label(wrap, text="", bg="#05060c", fg="#19d3ff",
                                 font=("Segoe UI", 18, "bold"))
        self.lbl_name.pack()
        tk.Label(wrap, text="Javite se na pult za nastavak igre.",
                 bg="#05060c", fg="#aeb4c6", font=("Segoe UI", 16)).pack(pady=(10, 0))
        self.lbl_clock = tk.Label(wrap, text="", bg="#05060c", fg="#4a4f63",
                                  font=("Segoe UI", 12))
        self.lbl_clock.pack(pady=(20, 0))
        tk.Label(wrap, text="(admin izlaz: Ctrl+Alt+Q)", bg="#05060c", fg="#23283a",
                 font=("Segoe UI", 9)).pack(pady=(28, 0))

        self._tick_clock()
        self.root.after(200, self._poll)

    def _fullscreen(self):
        w = self.ov.winfo_screenwidth()
        h = self.ov.winfo_screenheight()
        self.ov.geometry(f"{w}x{h}+0+0")

    def _tick_clock(self):
        self.lbl_clock.config(text=time.strftime("%H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    def show_lock(self):
        self.lbl_name.config(text=self.name)
        self.ov.deiconify()
        self._fullscreen()
        self.ov.lift()
        self.ov.attributes("-topmost", True)
        try:
            self.ov.focus_force()
        except Exception:
            pass

    def hide_lock(self):
        self.ov.withdraw()

    def _poll(self):
        threading.Thread(target=self._fetch, daemon=True).start()
        self.root.after(int(POLL_SEC * 1000), self._poll)

    def _fetch(self):
        try:
            d = http_json(f"{self.base}/api/agent?id={self.sid}", timeout=4)
            locked = bool(d.get("locked", True))
            self.name = d.get("name", self.name)
            self.remaining = d.get("remaining", 0)
        except Exception:
            # ako server nedostupan — NE zaključavaj (da ne blokira PC zbog mreže)
            return
        if locked != self.locked:
            self.locked = locked
            self.root.after(0, self.show_lock if locked else self.hide_lock)
        elif locked:
            # drži ga na vrhu (iznad igara)
            self.root.after(0, lambda: (self.ov.lift(), self.ov.attributes("-topmost", True)))

    def _quit(self):
        try:
            self.ov.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def run(self):
        self.root.mainloop()


def main():
    cfg = load_cfg()
    if not cfg.get("server") or not cfg.get("station_id"):
        cfg = run_setup()
    if not cfg.get("server") or not cfg.get("station_id"):
        return  # otkazano
    Agent(cfg).run()


if __name__ == "__main__":
    main()
