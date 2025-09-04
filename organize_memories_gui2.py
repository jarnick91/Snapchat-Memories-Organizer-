#!/usr/bin/env python3
"""
Organize Snapchat Memories (HTML export) — GUI with progress bar & cancel
- Reads 'memories.html' (with <img>/<video> referencing local files)
- Extracts the date (from HTML <div class="text-line">, filename, or file timestamp)
- Copies files into folders: OUT/ YYYY / YYYY-MM / YYYY-MM-DD_<original>
- Uses only Python standard libraries (tkinter, html.parser, shutil, etc.)
"""

import re, shutil, threading, queue
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Multiple date patterns for different export formats
DATE_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",  # YYYY-MM-DD
    r"\b\d{2}-\d{2}-\d{4}\b",  # MM-DD-YYYY
    r"\b\d{4}_\d{2}_\d{2}\b",  # YYYY_MM_DD
    r"\b\d{8}\b",              # YYYYMMDD
    r"\b\d{6}\b",              # YYMMDD or MMDDYY
]

# ---------------- HTML parser ----------------
class MemoriesHTML(HTMLParser):
    """Collects (src, date) pairs from the HTML file."""
    def __init__(self):
        super().__init__()
        self.items = []
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
            if "class" in attrs and "text-line" in attrs["class"]:
                self._in_textline = True

    def handle_endtag(self, tag):
        if tag.lower() == "div":
            self._in_textline = False

    def handle_data(self, data):
        if self._in_textline and self._last_media_index is not None:
            # Try all date patterns
            for pattern in DATE_PATTERNS:
                m = re.search(pattern, data)
                if m:
                    date_str = m.group(0)
                    # Normalize date to YYYY-MM-DD if needed
                    try:
                        if "_" in date_str:
                            date_str = date_str.replace("_", "-")
                        elif len(date_str) == 8 and "-" not in date_str:  # YYYYMMDD
                            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                        elif len(date_str) == 6:  # YYMMDD or MMDDYY
                            # Try both formats
                            try:
                                dt = datetime.strptime(date_str, "%y%m%d")
                            except ValueError:
                                dt = datetime.strptime(date_str, "%m%d%y")
                            date_str = dt.strftime("%Y-%m-%d")
                        elif re.match(r"\d{2}-\d{2}-\d{4}", date_str):  # MM-DD-YYYY
                            dt = datetime.strptime(date_str, "%m-%d-%Y")
                            date_str = dt.strftime("%Y-%m-%d")
                        
                        # Validate the date
                        datetime.strptime(date_str, "%Y-%m-%d")
                        self.items[self._last_media_index]["date"] = date_str
                        break
                    except ValueError:
                        continue

# ---------------- helpers ----------------
def normalize_src(src: str) -> str:
    s = src.strip()
    if s.startswith(".//"):
        s = s[3:]
    elif s.startswith("./"):
        s = s[2:]
    return s.split("?", 1)[0]

def date_from_name(path: Path) -> str:
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, path.name)
        if m:
            date_str = m.group(0)
            # Normalize date to YYYY-MM-DD if needed
            try:
                if "_" in date_str:
                    date_str = date_str.replace("_", "-")
                elif len(date_str) == 8 and "-" not in date_str:  # YYYYMMDD
                    date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                elif len(date_str) == 6:  # YYMMDD or MMDDYY
                    # Try both formats
                    try:
                        dt = datetime.strptime(date_str, "%y%m%d")
                    except ValueError:
                        dt = datetime.strptime(date_str, "%m%d%y")
                    date_str = dt.strftime("%Y-%m-%d")
                elif re.match(r"\d{2}-\d{2}-\d{4}", date_str):  # MM-DD-YYYY
                    dt = datetime.strptime(date_str, "%m-%d-%Y")
                    date_str = dt.strftime("%Y-%m-%d")
                
                # Validate the date
                datetime.strptime(date_str, "%Y-%m-%d")
                return date_str
            except ValueError:
                continue
    return ""

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
        self.last_update = 0

    def run(self):
        # Try multiple encodings for the HTML file
        encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
        text = None
        
        for encoding in encodings:
            try:
                text = self.html_path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
            except Exception as e:
                self.ui_q.put(("error", f"Could not read HTML file with {encoding}:\n{e}"))
                return
        
        if text is None:
            self.ui_q.put(("error", "Could not read HTML file with any supported encoding."))
            return

        parser = MemoriesHTML()
        parser.feed(text)
        items = parser.items

        if not items:
            self.ui_q.put(("error", "No media entries found in this HTML file."))
            return

        self.ui_q.put(("meta", len(items)))
        base_dir = self.html_path.parent
        ok = fail = 0
        
        # Calculate update interval to throttle UI updates
        update_interval = max(1, len(items) // 100)  # Update at most 100 times

        for i, it in enumerate(items, 1):
            if self.cancel.is_set():
                self.ui_q.put(("log", f"Cancelled. Success: {ok}, Failed: {fail}"))
                return

            raw_src = it.get("src") or ""
            rel = normalize_src(raw_src)
            src_path = (base_dir / rel).resolve()

            # fallback: try basename only
            if not src_path.exists():
                alt = base_dir / Path(rel).name
                if alt.exists():
                    src_path = alt

            if not src_path.exists():
                fail += 1
                # Throttle UI updates for missing files
                if i % update_interval == 0 or i == len(items) or i == 1:
                    self.ui_q.put(("progress", i, f"[{i}/{len(items)}] MISSING: {raw_src}"))
                continue

            # determine date
            date_str = it.get("date") or date_from_name(src_path) or fallback_date_from_fs(src_path)
            
            # Validate date format
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                # Invalid date format, use fallback
                date_str = fallback_date_from_fs(src_path)
            
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
                # Throttle UI updates
                if i % update_interval == 0 or i == len(items) or i == 1:
                    self.ui_q.put(("progress", i, f"[{i}/{len(items)}] OK → {target.name}"))
            except IOError as e:
                fail += 1
                self.ui_q.put(("progress", i, f"[{i}/{len(items)}] IO ERROR: {e}"))
            except Exception as e:
                fail += 1
                self.ui_q.put(("progress", i, f"[{i}/{len(items)}] ERROR: {e}"))

        self.ui_q.put(("done", f"Finished. Success: {ok}, Failed: {fail}\nOutput: {self.out_dir}"))

# ---------------- GUI ----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Organize Snapchat Memories (HTML)")
        self.geometry("720x520")
        self.resizable(True, False)

        self.html_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        self.cancel_flag = threading.Event()
        self.ui_q = queue.Queue()
        self.worker = None

        self._build_ui()
        self.after(100, self._pump)

    def _pick_html(self):
        p = filedialog.askopenfilename(
            title="Select your memories.html",
            filetypes=[("HTML files","*.html;*.htm"), ("All files","*.*")]
        )
        if p: self.html_var.set(p)

    def _pick_out(self):
        p = filedialog.askdirectory(title="Select the output folder")
        if p: self.out_var.set(p)

    def _build_ui(self):
        pad = 10
        frm = ttk.Frame(self); frm.pack(fill="x", padx=pad, pady=pad)

        ttk.Label(frm, text="Memories HTML:").grid(row=0, column=0, sticky="w")
        row1 = ttk.Frame(frm); row1.grid(row=1, column=0, sticky="we"); row1.columnconfigure(0, weight=1)
        ttk.Entry(row1, textvariable=self.html_var).grid(row=0, column=0, sticky="we")
        ttk.Button(row1, text="Browse…", command=self._pick_html).grid(row=0, column=1, padx=6)

        ttk.Label(frm, text="Output folder:").grid(row=2, column=0, sticky="w", pady=(8,0))
        row2 = ttk.Frame(frm); row2.grid(row=3, column=0, sticky="we"); row2.columnconfigure(0, weight=1)
        ttk.Entry(row2, textvariable=self.out_var).grid(row=0, column=0, sticky="we")
        ttk.Button(row2, text="Browse…", command=self._pick_out).grid(row=0, column=1, padx=6)

        ttk.Label(frm, text="Progress:").grid(row=4, column=0, sticky="w", pady=(8,0))
        self.pb = ttk.Progressbar(frm, orient="horizontal", mode="determinate", maximum=100)
        self.pb.grid(row=5, column=0, sticky="we", pady=2)
        ttk.Label(frm, textvariable=self.status_var).grid(row=6, column=0, sticky="w")

        ttk.Label(frm, text="Log:").grid(row=7, column=0, sticky="w", pady=(8,2))
        self.txt = tk.Text(frm, height=12, wrap="word"); self.txt.grid(row=8, column=0, sticky="we")
        frm.columnconfigure(0, weight=1)

        rowb = ttk.Frame(self); rowb.pack(fill="x", padx=pad, pady=(0,pad))
        self.btn_start = ttk.Button(rowb, text="Start", command=self._on_start); self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(rowb, text="Cancel", command=self._on_cancel, state="disabled"); self.btn_cancel.pack(side="left", padx=6)

    def _on_start(self):
        html = self.html_var.get().strip()
        out = self.out_var.get().strip()
        if not html or not Path(html).exists():
            messagebox.showerror("Error", "Please select a valid memories.html")
            return
        if not out:
            messagebox.showerror("Error", "Please select an output folder")
            return
        self.cancel_flag.clear()
        self.worker = Worker(Path(html), Path(out), self.ui_q, self.cancel_flag)
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.txt.delete("1.0","end")
        self.status_var.set("Working...")
        self.pb.configure(value=0, maximum=1)
        self.worker.start()

    def _on_cancel(self):
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self.status_var.set("Cancelling...")

    def _pump(self):
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "meta":
                    total = msg[1]
                    self.pb.configure(value=0, maximum=max(total, 1))
                elif kind == "progress":
                    idx, line = msg[1], msg[2]
                    self.pb.configure(value=idx)
                    self.status_var.set(line)
                    self.txt.insert("end", line + "\n"); self.txt.see("end")
                elif kind == "log":
                    line = msg[1]
                    self.txt.insert("end", line + "\n"); self.txt.see("end")
                elif kind == "done":
                    line = msg[1]
                    self.status_var.set("Done")
                    self.txt.insert("end", line + "\n"); self.txt.see("end")
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
                elif kind == "error":
                    line = msg[1]
                    messagebox.showerror("Error", line)
                    self.txt.insert("end", "ERROR: " + line + "\n")
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
                    self.status_var.set("Error.")
                self.ui_q.task_done()
        except queue.Empty:
            pass
        self.after(100, self._pump)

if __name__ == "__main__":
    App().mainloop()