#!/usr/bin/env python3
"""
Organize Snapchat Memories (HTML export) — GUI met voortgang & annuleren
- Leest 'memories.html' (met <img>/<video> die naar lokale bestanden wijzen)
- Haalt datum uit HTML (div.text-line) of desnoods uit bestandsnaam/timestamp
- Kopieert naar mappen: OUT/ YYYY / YYYY-MM / YYYY-MM-DD_<origineel>
- Standaardbibliotheken: tkinter, html.parser, shutil, etc.
"""

import re, shutil, threading, queue
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# ---------------- HTML parser ----------------
class MemoriesHTML(HTMLParser):
    """
    Verzamelt paren (src, date_str). In veel exports staat <img|video src="...">
    met in de buurt een <div class="text-line">YYYY-MM-DD</div>.
    """
    def __init__(self):
        super().__init__()
        self.items = []            # list[{"src": str, "date": str|""}]
        self._buf = ""             # lopende tekstbuffer (context)
        self._in_textline = False
        self._last_media_index = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in ("img", "video"):
            src = attrs.get("src") or ""
            if src:
                self.items.append({"src": src, "date": ""})
                self._last_media_index = len(self.items) - 1
        elif tag == "div":
            cls = attrs.get("class", "")
            if isinstance(cls, str) and "text-line" in cls:
                self._in_textline = True

    def handle_endtag(self, tag):
        if tag.lower() == "div":
            self._in_textline = False

    def handle_data(self, data):
        if not data:
            return
        self._buf += data
        if self._in_textline and self._last_media_index is not None:
            m = DATE_RE.search(data)
            if m:
                self.items[self._last_media_index]["date"] = m.group(0)

# ---------------- helpers ----------------
def normalize_src(src: str) -> str:
    s = src.strip()
    if s.startswith(".//"):
        s = s[3:]
    elif s.startswith("./"):
        s = s[2:]
    s = s.split("?", 1)[0]  # strip query
    return s

def date_from_name(path: Path) -> str:
    m = DATE_RE.search(path.name)
    return m.group(0) if m else ""

def fallback_date_from_fs(path: Path) -> str:
    dt = datetime.fromtimestamp(path.stat().st_mtime)
    return dt.strftime("%Y-%m-%d")

# ---------------- worker thread ----------------
class Worker(threading.Thread):
    def __init__(self, html_path: Path, out_dir: Path, ui_q: "queue.Queue", cancel_flag: threading.Event):
        super().__init__(daemon=True)
        self.html_path = html_path
        self.out_dir = out_dir
        self.ui_q = ui_q
        self.cancel = cancel_flag

    def run(self):
        # lees html
        try:
            txt = self.html_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            self.ui_q.put(("error", f"Kon HTML niet lezen:\n{e}"))
            return

        parser = MemoriesHTML()
        parser.feed(txt)
        items = parser.items

        if not items:
            self.ui_q.put(("error", "Geen media-tags (img/video) gevonden in deze HTML."))
            return

        self.ui_q.put(("meta", len(items)))
        base_dir = self.html_path.parent

        ok = 0
        fail = 0
        for i, it in enumerate(items, 1):
            if self.cancel.is_set():
                self.ui_q.put(("log", f"Geannuleerd. Gelukt: {ok}, Mislukt: {fail}"))
                return

            raw_src = it.get("src") or ""
            rel = normalize_src(raw_src)
            src_path = (base_dir / rel).resolve()

            # Als pad niet bestaat, probeer alleen de bestandsnaam in dezelfde map
            if not src_path.exists():
                alt = base_dir / Path(rel).name
                if alt.exists():
                    src_path = alt

            if not src_path.exists():
                fail += 1
                self.ui_q.put(("progress", i, f"[{i}/{len(items)}] MISSEND: {raw_src}"))
                continue

            # datum bepalen
            date_str = it.get("date") or date_from_name(src_path) or fallback_date_from_fs(src_path)
            year = date_str[:4]
            ym = date_str[:7]
            tgt_dir = self.out_dir / year / ym
            tgt_dir.mkdir(parents=True, exist_ok=True)

            base_name = f"{date_str}_{src_path.name}"
            target = tgt_dir / base_name

            sfx = 1
            while target.exists():
                target = tgt_dir / f"{Path(base_name).stem}_{sfx}{Path(base_name).suffix}"
                sfx += 1

            try:
                shutil.copy2(src_path, target)
                ok += 1
                self.ui_q.put(("progress", i, f"[{i}/{len(items)}] OK → {target.name}"))
            except Exception as e:
                fail += 1
                self.ui_q.put(("progress", i, f"[{i}/{len(items)}] FOUT: {e}"))

        self.ui_q.put(("done", f"Klaar. Gelukt: {ok}, Mislukt: {fail}\nUitvoer: {self.out_dir}"))

# ---------------- GUI ----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Organize Snapchat Memories (HTML)")
        self.geometry("720x520")
        self.resizable(True, False)

        self.html_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Gereed.")

        self.cancel_flag = threading.Event()
        self.ui_q = queue.Queue()
        self.worker = None

        self._build_ui()
        self.after(100, self._pump)

    def _pick_html(self):
        p = filedialog.askopenfilename(
            title="Kies je memories.html",
            filetypes=[("HTML files","*.html;*.htm"), ("All files","*.*")]
        )
        if p:
            self.html_var.set(p)

    def _pick_out(self):
        p = filedialog.askdirectory(title="Kies de doelmap")
        if p:
            self.out_var.set(p)

    def _build_ui(self):
        pad = 10
        frm = ttk.Frame(self); frm.pack(fill="x", padx=pad, pady=pad)

        ttk.Label(frm, text="Memories HTML:").grid(row=0, column=0, sticky="w")
        row1 = ttk.Frame(frm); row1.grid(row=1, column=0, sticky="we"); row1.columnconfigure(0, weight=1)
        ttk.Entry(row1, textvariable=self.html_var).grid(row=0, column=0, sticky="we")
        ttk.Button(row1, text="Bladeren…", command=self._pick_html).grid(row=0, column=1, padx=6)

        ttk.Label(frm, text="Doelmap:").grid(row=2, column=0, sticky="w", pady=(8,0))
        row2 = ttk.Frame(frm); row2.grid(row=3, column=0, sticky="we"); row2.columnconfigure(0, weight=1)
        ttk.Entry(row2, textvariable=self.out_var).grid(row=0, column=0, sticky="we")
        ttk.Button(row2, text="Bladeren…", command=self._pick_out).grid(row=0, column=1, padx=6)

        ttk.Label(frm, text="Voortgang:").grid(row=4, column=0, sticky="w", pady=(8,0))
        self.pb = ttk.Progressbar(frm, orient="horizontal", mode="determinate", maximum=100)
        self.pb.grid(row=5, column=0, sticky="we", pady=2)
        ttk.Label(frm, textvariable=self.status_var).grid(row=6, column=0, sticky="w")

        ttk.Label(frm, text="Log:").grid(row=7, column=0, sticky="w", pady=(8,2))
        self.txt = tk.Text(frm, height=12, wrap="word"); self.txt.grid(row=8, column=0, sticky="we")
        frm.columnconfigure(0, weight=1)

        rowb = ttk.Frame(self); rowb.pack(fill="x", padx=pad, pady=(0,pad))
        self.btn_start = ttk.Button(rowb, text="Start", command=self._on_start); self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(rowb, text="Annuleren", command=self._on_cancel, state="disabled"); self.btn_cancel.pack(side="left", padx=6)

    def _on_start(self):
        html = self.html_var.get().strip()
        out = self.out_var.get().strip()
        if not html or not Path(html).exists():
            messagebox.showerror("Fout", "Kies een geldige memories.html")
            return
        if not out:
            messagebox.showerror("Fout", "Kies een doelmap")
            return
        self.cancel_flag.clear()
        self.worker = Worker(Path(html), Path(out), self.ui_q, self.cancel_flag)
        self.btn_start.configure(state="disabled"); self.btn_cancel.configure(state="normal")
        self.txt.delete("1.0","end"); self.status_var.set("Bezig...")
        self.pb.configure(value=0, maximum=1)
        self.worker.start()

    def _on_cancel(self):
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self.status_var.set("Annuleren...")

    def _pump(self):
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "meta":
                    total = msg[1]; self.pb.configure(value=0, maximum=max(total,1))
                elif kind == "progress":
                    idx, line = msg[1], msg[2]
                    self.pb.configure(value=idx); self.status_var.set(line)
                    self.txt.insert("end", line + "\n"); self.txt.see("end")
                elif kind == "log":
                    line = msg[1]
                    self.txt.insert("end", line + "\n"); self.txt.see("end")
                elif kind == "done":
                    line = msg[1]; self.status_var.set("Klaar")
                    self.txt.insert("end", line + "\n"); self.txt.see("end")
                    self.btn_start.configure(state="normal"); self.btn_cancel.configure(state="disabled")
                elif kind == "error":
                    line = msg[1]; messagebox.showerror("Fout", line)
                    self.txt.insert("end", "FOUT: " + line + "\n"); self.txt.see("end")
                    self.btn_start.configure(state="normal"); self.btn_cancel.configure(state="disabled")
                    self.status_var.set("Fout.")
                self.ui_q.task_done()
        except queue.Empty:
            pass
        self.after(100, self._pump)

if __name__ == "__main__":
    App().mainloop()
