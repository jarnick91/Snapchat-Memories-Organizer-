# 📸 Snapchat Memories Organizer (HTML)

Organize your **Snapchat Memories** export (when you have a `memories.html` file with local JPG/MP4 files) into a clean **year/month** folder structure.  
Runs completely **offline** on your computer with an easy-to-use **Python GUI**.

---

## ✨ Features
- Parses `memories.html` from a Snapchat export.
- Extracts dates from:
  -. the HTML (`<div class="text-line">YYYY-MM-DD`),
  -. the filename,
  -. or the file’s timestamp (fallback).
- Copies media into structured folders:

---

Output/
├─ 2017/
│ ├─ 2017-03/
│ │ ├─ 2017-03-31_original.jpg
│ │ └─ 2017-03-31_clip.mp4
│ └─ 2017-04/
└─ 2018/



- GUI with:
  - Browse for `memories.html`
  - Browse for output folder
  - Start / Cancel buttons
  - Progress bar + log window

---

## 🚀 Getting Started

### Requirements
- Python **3.8+**
- Tkinter (included on Windows/macOS; on Linux: `sudo apt install python3-tk`)

### Installation
1. Download or clone this repo:
   ```bash
   git clone https://github.com/<your-username>/snapchat-memories-organizer-html.git
   cd snapchat-memories-organizer-html



