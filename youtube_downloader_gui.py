import os
import sys
import glob
import shutil
import threading
import json
import re
import urllib.request
import urllib.parse
import subprocess
import time
from collections import deque
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import yt_dlp

# Support both PyInstaller frozen executable and standard Python script
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(SCRIPT_DIR, 'settings.json')

def ensure_ffmpeg_in_path():
    """Auto-detect ffmpeg in script dir, WinGet packages, or standard paths and append to PATH."""
    if shutil.which("ffmpeg"):
        return True
        
    candidate_dirs = [
        SCRIPT_DIR,
        os.path.join(SCRIPT_DIR, 'ffmpeg'),
        os.path.join(SCRIPT_DIR, 'bin'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Links'),
        r"C:\Program Files\FFmpeg\bin",
        r"C:\ffmpeg\bin"
    ]
    
    # Also search user LocalAppData WinGet packages
    winget_pkgs = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages')
    if os.path.exists(winget_pkgs):
        try:
            for root, dirs, files in os.walk(winget_pkgs):
                if 'ffmpeg.exe' in files:
                    candidate_dirs.append(root)
                    break
        except Exception:
            pass
                
    for d in candidate_dirs:
        if d and os.path.exists(os.path.join(d, 'ffmpeg.exe')):
            os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
            if shutil.which("ffmpeg"):
                return True
    return False

ensure_ffmpeg_in_path()

# Side files yt-dlp downloads besides the video/audio streams
AUX_FILE_EXTS = {'.srt', '.vtt', '.json3', '.srv1', '.srv2', '.srv3', '.ttml', '.ass', '.lrc'}

def parse_srt(srt_text):
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    subtitles = []
    for b in blocks:
        lines = [l.strip() for l in b.strip().split('\n') if l.strip()]
        if len(lines) >= 3:
            idx = lines[0]
            timing = lines[1]
            text = ' '.join(lines[2:])
            subtitles.append({'idx': idx, 'timing': timing, 'text': text})
    return subtitles

def translate_texts(texts, target='ar'):
    combined = ' ||| '.join(texts)
    try:
        url = f'https://translate.googleapis.com/translate_a/single?client=dict-chrome-ex&sl=auto&tl={target}&dt=t&q=' + urllib.parse.quote(combined)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            full_res = ''.join([part[0] for part in data[0] if part[0]])
            parts = [p.strip() for p in full_res.split('|||')]
            if len(parts) == len(texts):
                return parts
    except Exception:
        pass

    # Fallback line by line
    results = []
    for t in texts:
        try:
            url = f'https://translate.googleapis.com/translate_a/single?client=dict-chrome-ex&sl=auto&tl={target}&dt=t&q=' + urllib.parse.quote(t)
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                results.append(''.join([part[0] for part in data[0] if part[0]]))
        except Exception:
            results.append(t)
    return results

def translate_srt_file(input_srt_path, output_srt_path, target='ar', progress_callback=None):
    try:
        with open(input_srt_path, 'r', encoding='utf-8', errors='ignore') as f:
            srt_content = f.read()

        subs = parse_srt(srt_content)
        if not subs:
            return False

        chunk_size = 30
        total_subs = len(subs)
        translated_subs = []

        for i in range(0, total_subs, chunk_size):
            chunk = subs[i:i+chunk_size]
            texts = [s['text'] for s in chunk]
            trans_texts = translate_texts(texts, target)
            for s, trans in zip(chunk, trans_texts):
                translated_subs.append({'idx': s['idx'], 'timing': s['timing'], 'text': trans})
            
            if progress_callback:
                progress_callback(min(i + chunk_size, total_subs), total_subs)
            time.sleep(0.08)

        output_lines = []
        for s in translated_subs:
            output_lines.append(f"{s['idx']}\n{s['timing']}\n{s['text']}\n")

        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines))

        return True
    except Exception:
        return False

def extract_subtitle_from_video(video_file, output_srt):
    try:
        cmd = ['ffmpeg', '-y', '-i', video_file, '-map', '0:s:0', '-c:s', 'srt', output_srt]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return res.returncode == 0 and os.path.exists(output_srt) and os.path.getsize(output_srt) > 0
    except Exception:
        return False

def make_folder_name(title):
    """Turn a playlist title into a valid Windows folder name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip().rstrip('. ')
    if len(name) > 100:
        name = name[:100].rstrip('. ')
    return name or "Playlist"

def find_subtitle_files(save_dir, index_str, lang):
    """Find subtitle files yt-dlp wrote for a language, e.g. '.ar.srt', '.ar-orig.srt', '.ar-SA.vtt'.
    Returns paths ordered by preference: exact code, then '-orig', then other variants; SRT before VTT."""
    pattern = re.compile(rf'\.{lang}(-[\w-]+)?\.(srt|vtt)$', re.IGNORECASE)
    matches = [f for f in glob.glob(os.path.join(glob.escape(save_dir), f"{index_str} - *.*")) if pattern.search(f)]

    def rank(path):
        m = pattern.search(path)
        variant = (m.group(1) or '').lower()
        return (0 if not variant else 1 if variant == '-orig' else 2, 0 if m.group(2).lower() == 'srt' else 1)
    return sorted(matches, key=rank)

def embed_subtitles_to_video(video_file, subtitle_tracks):
    if not os.path.exists(video_file) or not subtitle_tracks:
        return False

    valid_tracks = [t for t in subtitle_tracks if os.path.exists(t[0])]
    if not valid_tracks:
        return False

    temp_out = video_file + ".temp_sub.mkv"
    cmd = ['ffmpeg', '-y', '-i', video_file]
    for srt_path, _, _ in valid_tracks:
        cmd.extend(['-i', srt_path])

    # Without explicit -map ffmpeg keeps only one subtitle stream, so map every track.
    # Existing subtitle streams are dropped to avoid duplicates of the tracks we add.
    cmd.extend(['-map', '0', '-map', '-0:s'])
    for idx in range(len(valid_tracks)):
        cmd.extend(['-map', f'{idx + 1}:0'])

    cmd.extend(['-c', 'copy', '-c:s', 'srt'])
    for idx, (_, lang, title) in enumerate(valid_tracks):
        cmd.extend([
            f'-metadata:s:s:{idx}', f'language={lang}',
            f'-metadata:s:s:{idx}', f'title={title}',
            # First track (Arabic when available) is shown by default in players
            f'-disposition:s:{idx}', 'default' if idx == 0 else '0',
        ])
    cmd.append(temp_out)
    
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if res.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
            os.replace(temp_out, video_file)
            return True
    except Exception:
        pass
    finally:
        if os.path.exists(temp_out):
            try: os.remove(temp_out)
            except: pass
    return False

class CancelDownloadException(Exception):
    pass

class YouTubeDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("YouTube Playlist Dashboard")
        self.geometry("1000x750")
        self.minsize(900, 650)
        
        # Grid Configuration
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # State variables
        self.cancel_event = threading.Event()
        self.download_thread = None
        self.fetched_entries = []
        self.total_videos = 0
        self.playlist_estimated_size = "Unknown"
        self.video_list_labels = {}
        self.video_list_titles = {}
        self.video_list_vars = {}

        # Parallel download queue (shared with worker threads, guarded by dl_cond)
        self.dl_cond = threading.Condition()
        self.is_downloading = False
        self.workers_enabled = False
        self.job_queue = deque()
        self.queued_keys = set()
        self.active_progress = {}
        self.active_speed = {}
        self.failed_videos = []
        self.used_dirs = set()
        self.total_selected = 0
        self.done_count = 0
        self.worker_count = 0
        self.max_parallel = 3

        # Build UI
        self.setup_ui()
        self.create_context_menu()
        
        # Load Settings
        self.load_settings()

    def setup_ui(self):
        # --- Sidebar Frame (Left) ---
        self.sidebar_frame = ctk.CTkFrame(self, width=280, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_columnconfigure(0, weight=1)
        
        # 1. Header
        header_lbl = ctk.CTkLabel(self.sidebar_frame, text="YT Downloader", font=ctk.CTkFont(size=24, weight="bold"))
        header_lbl.grid(row=0, column=0, padx=20, pady=(30, 20))

        # 2. URL Input
        ctk.CTkLabel(self.sidebar_frame, text="Playlist URLs (one per line):").grid(row=1, column=0, sticky="w", padx=20)
        self.url_entry = ctk.CTkTextbox(self.sidebar_frame, height=70, wrap="none")
        self.url_entry.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 5))
        
        self.paste_btn = ctk.CTkButton(self.sidebar_frame, text="Paste URL", command=self.paste_url)
        self.paste_btn.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))

        # 3. Directory
        ctk.CTkLabel(self.sidebar_frame, text="Save Directory:").grid(row=4, column=0, sticky="w", padx=20)
        self.dir_entry = ctk.CTkEntry(self.sidebar_frame, state="readonly")
        self.dir_entry.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 5))
        
        self.browse_btn = ctk.CTkButton(self.sidebar_frame, text="Browse Folder", command=self.browse_directory)
        self.browse_btn.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 20))

        # 4. Settings
        ctk.CTkLabel(self.sidebar_frame, text="Settings", font=ctk.CTkFont(weight="bold")).grid(row=7, column=0, sticky="w", padx=20, pady=(10, 5))
        
        self.quality_var = ctk.StringVar(value="720p")
        self.quality_menu = ctk.CTkOptionMenu(self.sidebar_frame, variable=self.quality_var, 
                                              values=["1080p (FFmpeg)", "720p (FFmpeg)", "480p (FFmpeg)", "Audio Only (MP3)"],
                                              command=lambda _: self.save_settings())
        self.quality_menu.grid(row=8, column=0, sticky="ew", padx=20, pady=(0, 10))
        
        self.subtitles_var = ctk.BooleanVar(value=False)
        self.subs_check = ctk.CTkCheckBox(self.sidebar_frame, text="Download Subtitles (CC)", variable=self.subtitles_var, command=self.on_subs_toggle)
        self.subs_check.grid(row=9, column=0, sticky="w", padx=20, pady=(5, 2))
        
        self.auto_subtitles_var = ctk.BooleanVar(value=True)
        self.auto_subs_check = ctk.CTkCheckBox(self.sidebar_frame, text="↳ Auto CC (تلقائي)", variable=self.auto_subtitles_var, 
                                               font=ctk.CTkFont(size=12), text_color="gray80",
                                               command=self.save_settings)
        self.auto_subs_check.grid(row=10, column=0, sticky="w", padx=(35, 20), pady=2)
        
        self.embed_subtitles_var = ctk.BooleanVar(value=True)
        self.embed_subs_check = ctk.CTkCheckBox(self.sidebar_frame, text="↳ Embed CC in Video (دمج)", variable=self.embed_subtitles_var, 
                                                font=ctk.CTkFont(size=12), text_color="gray80",
                                                command=self.save_settings)
        self.embed_subs_check.grid(row=11, column=0, sticky="w", padx=(35, 20), pady=2)

        self.sub_lang_var = ctk.StringVar(value="Arabic + English")
        self.sub_lang_menu = ctk.CTkOptionMenu(self.sidebar_frame, variable=self.sub_lang_var,
                                               values=["Arabic + English", "Arabic Only", "English Only", "All Languages"],
                                               height=26, font=ctk.CTkFont(size=12),
                                               command=lambda _: self.save_settings())
        self.sub_lang_menu.grid(row=12, column=0, sticky="ew", padx=(35, 20), pady=(2, 8))
        
        self.thumbnail_var = ctk.BooleanVar(value=False)
        self.thumb_check = ctk.CTkCheckBox(self.sidebar_frame, text="Embed Thumbnail", variable=self.thumbnail_var, command=self.save_settings)
        self.thumb_check.grid(row=13, column=0, sticky="w", padx=20, pady=(5, 10))

        parallel_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        parallel_frame.grid(row=14, column=0, sticky="ew", padx=20, pady=(0, 15))
        ctk.CTkLabel(parallel_frame, text="Parallel downloads:").pack(side="left")
        self.parallel_var = ctk.StringVar(value="3")
        self.parallel_menu = ctk.CTkOptionMenu(parallel_frame, variable=self.parallel_var, values=["1", "2", "3", "4", "5"],
                                               width=60, command=lambda _: self.save_settings())
        self.parallel_menu.pack(side="right")

        self.sidebar_frame.grid_rowconfigure(15, weight=1) # Spacer
        
        # 5. Open Folder Button
        self.open_folder_btn = ctk.CTkButton(self.sidebar_frame, text="📂 Open Folder", command=self.open_folder, fg_color="#2fa572", hover_color="#23855a")
        self.open_folder_btn.grid(row=16, column=0, sticky="ew", padx=20, pady=(0, 20))

        # --- Main Workspace Frame (Right) ---
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_rowconfigure(2, weight=1) # Expand video list
        self.main_frame.grid_columnconfigure(0, weight=1)

        # Top Action Bar
        self.action_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.action_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        self.fetch_btn = ctk.CTkButton(self.action_frame, text="1. Fetch Info", command=self.start_fetch, width=120)
        self.fetch_btn.pack(side="left", padx=(0, 10))
        
        self.download_btn = ctk.CTkButton(self.action_frame, text="2. Download Selected", command=lambda: self.start_download(), state="disabled", width=150)
        self.download_btn.pack(side="left", padx=10)
        
        self.cancel_btn = ctk.CTkButton(self.action_frame, text="Cancel", command=self.cancel_download, state="disabled", fg_color="#d35b58", hover_color="#8b0000", width=100)
        self.cancel_btn.pack(side="left", padx=10)

        # Video List Header & Select All
        self.list_header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.list_header_frame.grid(row=1, column=0, sticky="ew", pady=(10, 5))
        
        ctk.CTkLabel(self.list_header_frame, text="Playlist Videos", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        
        self.deselect_all_btn = ctk.CTkButton(self.list_header_frame, text="Deselect All", command=self.deselect_all, width=90, height=28, fg_color="#444", hover_color="#333")
        self.deselect_all_btn.pack(side="right", padx=(10, 0))
        self.select_all_btn = ctk.CTkButton(self.list_header_frame, text="Select All", command=self.select_all, width=90, height=28)
        self.select_all_btn.pack(side="right")

        # Scrollable Video List
        self.video_list_frame = ctk.CTkScrollableFrame(self.main_frame)
        self.video_list_frame.grid(row=2, column=0, sticky="nsew", pady=5)

        # Progress Area
        self.prog_area_frame = ctk.CTkFrame(self.main_frame)
        self.prog_area_frame.grid(row=3, column=0, sticky="ew", pady=(15, 0), ipady=10)
        self.prog_area_frame.grid_columnconfigure(1, weight=1)

        self.video_info_label = ctk.CTkLabel(self.prog_area_frame, text="Ready. Please Paste URL and Fetch Info.", font=ctk.CTkFont(weight="bold"))
        self.video_info_label.grid(row=0, column=0, columnspan=3, pady=(10, 10))

        ctk.CTkLabel(self.prog_area_frame, text="Active:").grid(row=1, column=0, sticky="w", padx=15)
        self.video_prog_bar = ctk.CTkProgressBar(self.prog_area_frame)
        self.video_prog_bar.grid(row=1, column=1, sticky="ew", padx=10)
        self.video_prog_bar.set(0)
        self.video_pct_label = ctk.CTkLabel(self.prog_area_frame, text="0%", width=40)
        self.video_pct_label.grid(row=1, column=2, padx=(0, 15))

        ctk.CTkLabel(self.prog_area_frame, text="Total:").grid(row=2, column=0, sticky="w", padx=15, pady=10)
        self.playlist_prog_bar = ctk.CTkProgressBar(self.prog_area_frame)
        self.playlist_prog_bar.grid(row=2, column=1, sticky="ew", padx=10, pady=10)
        self.playlist_prog_bar.set(0)
        self.playlist_pct_label = ctk.CTkLabel(self.prog_area_frame, text="0%", width=40)
        self.playlist_pct_label.grid(row=2, column=2, padx=(0, 15), pady=10)

        self.stats_label = ctk.CTkLabel(self.prog_area_frame, text="Size: -- | Speed: -- | ETA: --", text_color="gray")
        self.stats_label.grid(row=3, column=1, pady=(0, 5))

        # Log Area
        self.log_area = ctk.CTkTextbox(self.main_frame, height=80)
        self.log_area.grid(row=4, column=0, sticky="ew", pady=(15, 0))
        self.log_area.configure(state="disabled")

    def create_context_menu(self):
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Paste", command=self.paste_url)
        self.url_entry.bind("<Button-3>", self.show_context_menu)

    def show_context_menu(self, event):
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def on_subs_toggle(self):
        is_enabled = self.subtitles_var.get()
        state = "normal" if is_enabled else "disabled"
        text_color = "gray80" if is_enabled else "gray40"
        
        self.auto_subs_check.configure(state=state, text_color=text_color)
        self.embed_subs_check.configure(state=state, text_color=text_color)
        self.sub_lang_menu.configure(state=state)
        self.save_settings()

    def save_settings(self):
        settings = {
            'save_dir': self.dir_entry.get(),
            'quality': self.quality_var.get(),
            'subtitles': self.subtitles_var.get(),
            'auto_subtitles': self.auto_subtitles_var.get(),
            'embed_subtitles': self.embed_subtitles_var.get(),
            'sub_lang': self.sub_lang_var.get(),
            'thumbnails': self.thumbnail_var.get(),
            'parallel_downloads': self.parallel_var.get()
        }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f)
        except:
            pass

    def load_settings(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    settings = json.load(f)
                    if 'save_dir' in settings and os.path.exists(settings['save_dir']):
                        self.dir_entry.configure(state="normal")
                        self.dir_entry.insert(0, settings['save_dir'])
                        self.dir_entry.configure(state="readonly")
                    if 'quality' in settings:
                        # Migrate old quality names to new labels
                        quality_migration = {
                            '1080p (Requires FFmpeg)': '1080p (FFmpeg)',
                            '1080p': '1080p (FFmpeg)',
                            '720p': '720p (FFmpeg)',
                            '480p': '480p (FFmpeg)',
                        }
                        q = settings['quality']
                        self.quality_var.set(quality_migration.get(q, q))
                    if 'subtitles' in settings:
                        self.subtitles_var.set(settings['subtitles'])
                    if 'auto_subtitles' in settings:
                        self.auto_subtitles_var.set(settings['auto_subtitles'])
                    if 'embed_subtitles' in settings:
                        self.embed_subtitles_var.set(settings['embed_subtitles'])
                    if 'sub_lang' in settings:
                        self.sub_lang_var.set(settings['sub_lang'])
                    if 'thumbnails' in settings:
                        self.thumbnail_var.set(settings['thumbnails'])
                    if settings.get('parallel_downloads') in ("1", "2", "3", "4", "5"):
                        self.parallel_var.set(settings['parallel_downloads'])
        except:
            pass
        self.on_subs_toggle()

    def cleanup_temp_files(self, save_dir):
        """Remove leftover .part files and orphaned separate streams from failed downloads."""
        patterns = ['*.part', '*.ytdl']
        removed = 0
        for pattern in patterns:
            for f in glob.glob(os.path.join(glob.escape(save_dir), pattern)):
                try:
                    os.remove(f)
                    removed += 1
                except:
                    pass
        if removed > 0:
            self.log_message(f"Cleaned up {removed} temporary file(s).")

    def open_folder(self):
        save_dir = self.dir_entry.get().strip()
        if save_dir and os.path.exists(save_dir):
            os.startfile(save_dir)
        else:
            messagebox.showerror("Error", "Save directory not set or does not exist.")

    def select_all(self):
        for var in self.video_list_vars.values():
            var.set(True)

    def deselect_all(self):
        for var in self.video_list_vars.values():
            var.set(False)

    def browse_directory(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.dir_entry.configure(state="normal")
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, dir_path)
            self.dir_entry.configure(state="readonly")
            self.save_settings()

    def paste_url(self):
        try:
            clipboard_content = self.clipboard_get().strip()
            if not clipboard_content:
                return
            # Append on a new line so several playlists can be queued
            existing = self.url_entry.get('1.0', 'end').strip()
            self.url_entry.delete('1.0', 'end')
            self.url_entry.insert('1.0', f"{existing}\n{clipboard_content}" if existing else clipboard_content)
        except tk.TclError:
            pass

    def get_urls(self):
        urls = []
        for line in self.url_entry.get('1.0', 'end').splitlines():
            line = line.strip()
            if line and line not in urls:
                urls.append(line)
        return urls

    def log_message(self, message):
        def _log():
            self.log_area.configure(state="normal")
            self.log_area.insert("end", message + "\n")
            self.log_area.see("end")
            self.log_area.configure(state="disabled")
        self.after(0, _log)

    def populate_video_list(self, items, append=False):
        if not append:
            for widget in self.video_list_frame.winfo_children():
                widget.destroy()
            self.video_list_labels.clear()
            self.video_list_titles.clear()
            self.video_list_vars.clear()
            self._list_grid_row = 0
            self._list_last_playlist = None

        # Group videos under a header per playlist when several are listed
        show_headers = append or len({item['playlist'] for item in items}) > 1
        for item in items:
            key, index, entry = item['key'], item['index'], item['entry']

            if show_headers and item['playlist'] != self._list_last_playlist:
                self._list_last_playlist = item['playlist']
                ctk.CTkLabel(self.video_list_frame, text=f"📁 {item['playlist']}", anchor="w",
                             font=ctk.CTkFont(weight="bold")).grid(row=self._list_grid_row, column=0, sticky="ew", pady=(8, 2), padx=5)
                self._list_grid_row += 1

            title = entry.get('title', f'Video {index}')
            if len(title) > 75: title = title[:72] + "..."

            base_text = f"{index:02d} - {title}"

            row_frame = ctk.CTkFrame(self.video_list_frame, fg_color="transparent")
            row_frame.grid(row=self._list_grid_row, column=0, sticky="ew", pady=2, padx=5)
            self._list_grid_row += 1

            var = ctk.BooleanVar(value=True)
            self.video_list_vars[key] = var

            chk = ctk.CTkCheckBox(row_frame, text="", variable=var, width=24)
            chk.pack(side="left")

            lbl = ctk.CTkLabel(row_frame, text=f"⏳ {base_text}", anchor="w")
            lbl.pack(side="left", fill="x", expand=True)

            self.video_list_labels[key] = lbl
            self.video_list_titles[key] = base_text

    def update_video_status(self, key, status_icon, color=None, extra=""):
        def _update():
            if key in self.video_list_labels:
                lbl = self.video_list_labels[key]
                base_text = self.video_list_titles.get(key, "")
                extra_text = f"{extra}  " if extra else ""
                lbl.configure(text=f"{status_icon} {extra_text}{base_text}")
                if color:
                    lbl.configure(text_color=color)
        self.after(0, _update)

    def update_ui_safe(self, widget, **kwargs):
        self.after(0, lambda: widget.configure(**kwargs))
        
    def update_progress_safe(self, bar, value, label, text):
        def _update():
            bar.set(value)
            label.configure(text=text)
        self.after(0, _update)

    def start_fetch(self):
        urls = self.get_urls()
        if not urls:
            messagebox.showerror("Error", "Please enter a valid YouTube URL.")
            return

        # While downloading, fetched videos are appended to the list so they can be added to the queue
        append = self.is_downloading and bool(self.fetched_entries)
        self.fetch_btn.configure(state="disabled")
        self.download_btn.configure(state="disabled")
        if not append:
            self.cancel_btn.configure(state="disabled")
            self.log_area.configure(state="normal")
            self.log_area.delete('1.0', tk.END)
            self.log_area.configure(state="disabled")

        threading.Thread(target=self.fetch_process, args=(urls, append), daemon=True).start()

    def fetch_process(self, urls, append=False):
        if not append:
            self.update_ui_safe(self.video_info_label, text="Extracting Playlist Metadata...")

        ydl_opts_info = {'extract_flat': 'in_playlist', 'quiet': True, 'no_warnings': True}
        existing = list(self.fetched_entries) if append else []
        # With several URLs (or when adding to an existing list) each playlist gets its own subfolder;
        # a single URL saves directly into the chosen folder
        use_subdirs = append or len(urls) > 1
        items = []
        used_subdirs = {it['subdir'].lower() for it in existing if it['subdir']}
        failed_urls = []
        try:
            for url_num, url in enumerate(urls, start=1):
                self.log_message(f"Extracting playlist info ({url_num}/{len(urls)}): {url}")
                if not append:
                    self.update_ui_safe(self.video_info_label, text=f"Extracting Playlist Metadata ({url_num}/{len(urls)})...")
                try:
                    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                        info = ydl.extract_info(url, download=False)
                except Exception as e:
                    self.log_message(f"Error fetching info for {url}: {str(e)}")
                    failed_urls.append(url)
                    continue

                if not info:
                    self.log_message(f"Could not extract info for {url}. Check the URL or if the playlist is private.")
                    failed_urls.append(url)
                    continue

                entries = [e for e in info['entries'] if e] if 'entries' in info else [info]
                if not entries:
                    self.log_message(f"Playlist is empty or private: {url}")
                    failed_urls.append(url)
                    continue

                playlist_title = info.get('title') or f"Playlist {url_num}"
                subdir = None
                if use_subdirs:
                    subdir = make_folder_name(playlist_title)
                    base_subdir, n = subdir, 2
                    while subdir.lower() in used_subdirs:
                        subdir = f"{base_subdir} ({n})"
                        n += 1
                    used_subdirs.add(subdir.lower())

                for i, entry in enumerate(entries):
                    items.append({'key': len(existing) + len(items), 'index': i + 1, 'entry': entry,
                                  'playlist': playlist_title, 'subdir': subdir})
                self.log_message(f"Found {len(entries)} videos in: {playlist_title}")

            if not items:
                self.after(0, lambda: messagebox.showerror("Error", "Could not extract any videos. Check the URLs or if the playlists are private."))
                self.reset_ui()
                return

            self.fetched_entries = existing + items
            self.total_videos = len(self.fetched_entries)

            if failed_urls:
                msg = f"{len(failed_urls)} URL(s) could not be fetched and were skipped:\n\n" + "\n".join(failed_urls)
                self.after(0, lambda: messagebox.showwarning("Some URLs Failed", msg))

            playlist_count = len(urls) - len(failed_urls)
            if append:
                self.log_message(f"Added {len(items)} videos from {playlist_count} playlist(s). Click 'Add to Queue' to download them.")
            else:
                self.log_message(f"Found {len(items)} videos in {playlist_count} playlist(s).")
                self.update_ui_safe(self.video_info_label, text=f"Ready to download. {len(items)} videos found in {playlist_count} playlist(s). Please select videos and click Download.")

            self.after(0, lambda: self.populate_video_list(items, append=append))
            self.reset_ui()

        except Exception as e:
            self.log_message(f"Error fetching info: {str(e)}")
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to fetch info:\n{str(e)}"))
            self.reset_ui()

    def start_download(self, specific_entries=None):
        save_dir = self.dir_entry.get().strip()

        if not save_dir:
            messagebox.showerror("Error", "Please select a save directory.")
            return

        entries_to_download = []
        if specific_entries is None:
            if not self.fetched_entries:
                messagebox.showerror("Error", "No metadata fetched. Please click 'Fetch Info' first.")
                return

            for item in self.fetched_entries:
                var = self.video_list_vars.get(item['key'])
                # Videos already in the running queue are not added twice
                if var and var.get() and not (self.is_downloading and item['key'] in self.queued_keys):
                    entries_to_download.append((item['key'], item))

            if not entries_to_download:
                messagebox.showerror("Error", "No new videos selected for download.")
                return
        else:
            entries_to_download = specific_entries

        quality = self.quality_var.get()

        # A download session is already running: just add to its queue
        with self.dl_cond:
            if self.is_downloading:
                self.enqueue_jobs_locked(entries_to_download, save_dir, quality)
                added = True
            else:
                self.is_downloading = True
                self.workers_enabled = False
                self.job_queue.clear()
                self.queued_keys = set()
                self.active_progress = {}
                self.active_speed = {}
                self.failed_videos = []
                self.used_dirs = set()
                self.total_selected = 0
                self.done_count = 0
                self.worker_count = 0
                self.max_parallel = int(self.parallel_var.get())
                self.enqueue_jobs_locked(entries_to_download, save_dir, quality)
                added = False

        for key, _ in entries_to_download:
            self.update_video_status(key, "⏳")

        if added:
            self.log_message(f"Added {len(entries_to_download)} video(s) to the download queue.")
            self.refresh_progress()
            return

        # UI state updates
        self.cancel_event.clear()
        self.reset_ui()

        self.video_prog_bar.set(0)
        self.playlist_prog_bar.set(0)
        self.video_pct_label.configure(text="0%")
        self.playlist_pct_label.configure(text="0%")
        self.stats_label.configure(text="Size: -- | Speed: -- | ETA: --")

        self.download_thread = threading.Thread(target=self.download_process, args=(quality, entries_to_download), daemon=True)
        self.download_thread.start()

    def enqueue_jobs_locked(self, entries, save_dir, quality):
        """Add jobs to the shared queue. Caller must hold self.dl_cond."""
        for key, item in entries:
            self.job_queue.append((key, item, save_dir, quality))
            self.queued_keys.add(key)
            self.total_selected += 1
        if self.workers_enabled:
            self.spawn_workers_locked()

    def spawn_workers_locked(self):
        """Start workers up to the parallel limit. Caller must hold self.dl_cond."""
        needed = min(self.max_parallel - self.worker_count, len(self.job_queue))
        for _ in range(max(0, needed)):
            self.worker_count += 1
            threading.Thread(target=self.download_worker, daemon=True).start()

    def cancel_download(self):
        self.cancel_event.set()
        with self.dl_cond:
            self.job_queue.clear()
        self.cancel_btn.configure(state="disabled")
        self.log_message("Cancelling download... Please wait.")

    def get_ydl_opts(self, quality, save_dir, video_index):
        index_str = str(video_index).zfill(2)
        opts = {
            'outtmpl': os.path.join(save_dir, f'{index_str} - %(title)s.%(ext)s'),
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'continuedl': True,
            'quiet': True,
            'no_warnings': True,
        }

        if "1080p" in quality:
            opts['format'] = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
            opts['merge_output_format'] = 'mkv'
        elif "720p" in quality:
            opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
            opts['merge_output_format'] = 'mkv'
        elif "480p" in quality:
            opts['format'] = 'bestvideo[height<=480]+bestaudio/best[height<=480]/best'
            opts['merge_output_format'] = 'mkv'
        elif "Audio Only" in quality:
            opts['format'] = 'bestaudio/best'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
            opts['outtmpl'] = os.path.join(save_dir, f'{index_str} - %(title)s.mp3')
            
        if self.subtitles_var.get():
            opts['writesubtitles'] = True
            if self.auto_subtitles_var.get():
                opts['writeautomaticsub'] = True
            
            lang_val = self.sub_lang_var.get()
            if "Arabic Only" in lang_val:
                # Request Arabic and English fallback so we can translate if Arabic isn't uploaded
                opts['subtitleslangs'] = ['ar', 'ar.*', 'en', 'en.*']
            elif "English Only" in lang_val:
                opts['subtitleslangs'] = ['en', 'en.*']
            elif "All" in lang_val:
                opts['subtitleslangs'] = ['all']
            else: # Default: Arabic + English
                opts['subtitleslangs'] = ['ar', 'ar.*', 'en', 'en.*']
                
            opts['subtitlesformat'] = 'srt/best'
            
            if 'postprocessors' not in opts:
                opts['postprocessors'] = []
                
            # Convert downloaded subtitles to SRT for universal compatibility
            opts['postprocessors'].append({'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'})
            
        if self.thumbnail_var.get():
            opts['writethumbnail'] = True
            if 'postprocessors' not in opts:
                opts['postprocessors'] = []
            opts['postprocessors'].append({'key': 'EmbedThumbnail'})
            opts['postprocessors'].append({'key': 'FFmpegMetadata'})

        return opts

    def format_bytes(self, bytes_val):
        if not bytes_val: return "Unknown"
        mb = bytes_val / (1024 * 1024)
        return f"{mb:.2f} MB"

    def format_speed(self, speed):
        if not speed: return "--"
        if speed < 1024: return f"{speed:.1f} B/s"
        elif speed < 1024**2: return f"{speed/1024:.1f} KB/s"
        else: return f"{speed/(1024**2):.1f} MB/s"

    def format_eta(self, eta):
        if not eta: return "--"
        m, s = divmod(int(eta), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def make_progress_hook(self, key, is_multi_stream):
        """Build a progress hook with its own state, so parallel downloads don't share counters."""
        state = {'streams': 0, 'last_ui': 0.0}

        def hook(d):
            if self.cancel_event.is_set():
                raise CancelDownloadException("Download cancelled by user.")

            filename = d.get('filename') or ''
            # Subtitle downloads also report progress; they must not count as video/audio streams
            if os.path.splitext(filename)[1].lower() in AUX_FILE_EXTS:
                return

            if d['status'] == 'downloading':
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                if total_bytes <= 0:
                    return
                raw_percent = downloaded / total_bytes

                # For bestvideo+bestaudio, yt-dlp downloads two streams sequentially.
                # Weight video stream as 80% and audio as 20% so progress never jumps backwards.
                if is_multi_stream:
                    percent = raw_percent * 0.8 if state['streams'] == 0 else 0.8 + raw_percent * 0.2
                else:
                    percent = raw_percent

                with self.dl_cond:
                    self.active_progress[key] = percent
                    self.active_speed[key] = d.get('speed') or 0

                # Throttle UI updates; several downloads report progress many times per second
                now = time.monotonic()
                if now - state['last_ui'] >= 0.3:
                    state['last_ui'] = now
                    self.update_video_status(key, "🔽", "white", extra=f"{percent*100:.0f}%")
                    self.refresh_progress()

            elif d['status'] == 'finished':
                state['streams'] += 1
                if is_multi_stream and state['streams'] == 1:
                    return  # video stream done, audio stream comes next
                with self.dl_cond:
                    self.active_progress[key] = 1.0
                    self.active_speed[key] = 0
                self.log_message(f"Finished downloading: {os.path.basename(filename)}")
                self.update_video_status(key, "⚙️", "white", extra="Processing")
                self.refresh_progress()

        return hook

    def refresh_progress(self):
        with self.dl_cond:
            active = dict(self.active_progress)
            speed = sum(self.active_speed.values())
            total = self.total_selected
            done = self.done_count
            queued = len(self.job_queue)

        current = sum(active.values()) / len(active) if active else 0
        overall = (done + sum(active.values())) / total if total else 0

        def _update():
            self.video_prog_bar.set(current)
            self.video_pct_label.configure(text=f"{current*100:.0f}%")
            self.playlist_prog_bar.set(overall)
            self.playlist_pct_label.configure(text=f"{overall*100:.1f}%")
            self.stats_label.configure(text=f"Active: {len(active)} | Queued: {queued} | Done: {done}/{total} | Speed: {self.format_speed(speed)}")
            if self.is_downloading and not self.cancel_event.is_set():
                self.video_info_label.configure(text=f"Downloading {len(active)} video(s) at once — {done} of {total} finished")
        self.after(0, _update)

    def download_process(self, quality, entries_to_dl):
        """Session controller: runs the pre-checks, then lets worker threads drain the queue."""
        failed_videos = []
        used_dirs = set()
        try:
            if not ensure_ffmpeg_in_path():
                err = ("FFmpeg is not installed or not in PATH!\n\n"
                       "FFmpeg is required for downloading and merging video+audio streams.\n"
                       "Please install FFmpeg or place ffmpeg.exe next to the program.")
                self.after(0, lambda: messagebox.showerror("FFmpeg Missing", err))
                return

            if not self.check_quality_available(quality, entries_to_dl):
                return

            with self.dl_cond:
                self.workers_enabled = True
                self.spawn_workers_locked()
                # Wait until the queue is drained; jobs may keep being added while we wait
                while self.worker_count > 0 or (self.job_queue and not self.cancel_event.is_set()):
                    if self.job_queue and self.worker_count == 0:
                        self.spawn_workers_locked()
                    self.dl_cond.wait(timeout=0.5)
                # Ending the session under the lock means nothing can be queued into a finished session
                self.is_downloading = False
                self.workers_enabled = False
                failed_videos = list(self.failed_videos)
                used_dirs = set(self.used_dirs)

            self.refresh_progress()

            if not self.cancel_event.is_set():
                if failed_videos:
                    self.log_message(f"Completed with {len(failed_videos)} errors.")
                    self.update_ui_safe(self.video_info_label, text=f"Finished with errors.")

                    def prompt_retry():
                        if messagebox.askyesno("Retry Failed", f"{len(failed_videos)} video(s) failed to download.\nDo you want to retry downloading them?"):
                            self.total_videos = len(failed_videos)
                            self.start_download(specific_entries=failed_videos)
                        else:
                            self.reset_ui()
                    self.after(0, prompt_retry)
                    return
                else:
                    self.log_message("All downloads completed successfully!")
                    self.update_ui_safe(self.video_info_label, text="Completed!")
                    for d in used_dirs:
                        self.cleanup_temp_files(d)
                    self.after(0, lambda: messagebox.showinfo("Success", "Download process finished!"))

        except Exception as e:
            self.log_message(f"A fatal error occurred: {str(e)}")
            self.after(0, lambda: messagebox.showerror("Error", f"A fatal error occurred:\n{str(e)}"))
        finally:
            with self.dl_cond:
                self.is_downloading = False
                self.workers_enabled = False
                self.job_queue.clear()
            self.reset_ui()

    def check_quality_available(self, quality, entries_to_dl):
        """Before downloading, check if the requested quality actually exists. Returns False to stop."""
        requested_height = None
        if "1080p" in quality:
            requested_height = 1080
        elif "720p" in quality:
            requested_height = 720
        elif "480p" in quality:
            requested_height = 480

        if not requested_height or "Audio Only" in quality:
            return True

        self.update_ui_safe(self.video_info_label, text="Checking available quality...")
        self.log_message("Checking available video quality...")

        # Pick the first valid video to check
        check_url = None
        for _, item in entries_to_dl:
            e = item['entry']
            if e:
                check_url = e.get('url') or e.get('webpage_url')
                if check_url:
                    break

        if not check_url:
            return True

        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(check_url, download=False)
                formats = info.get('formats', [])

                # Find max available video height
                max_height = 0
                available_heights = set()
                for f in formats:
                    h = f.get('height')
                    if h and h > 0:
                        available_heights.add(h)
                        if h > max_height:
                            max_height = h

                # Use a tolerance (10%) because YouTube often encodes at
                # non-standard heights (e.g. 1020p instead of 1080p).
                # yt-dlp's format selector uses <=, so 1020p satisfies height<=1080.
                tolerance = requested_height * 0.10
                if max_height > 0 and max_height < (requested_height - tolerance):
                    sorted_heights = sorted(available_heights, reverse=True)
                    available_str = ", ".join([f"{h}p" for h in sorted_heights[:5]])
                    err_msg = (
                        f"The quality you selected ({requested_height}p) is NOT available for this playlist.\n\n"
                        f"Maximum available quality: {max_height}p\n"
                        f"Available qualities: {available_str}\n\n"
                        f"Please go back and select a different quality."
                    )
                    self.after(0, lambda msg=err_msg: messagebox.showwarning("Quality Not Available", msg))
                    self.log_message(f"⚠️ {requested_height}p not available. Max: {max_height}p. Download stopped.")
                    return False
                self.log_message(f"✅ Quality check passed: {max_height}p available.")
        except Exception as e:
            self.log_message(f"Could not verify quality: {str(e)}. Proceeding anyway...")
        return True

    def download_worker(self):
        """Take jobs from the shared queue until it is empty or the session is cancelled."""
        try:
            while True:
                with self.dl_cond:
                    if self.cancel_event.is_set() or not self.job_queue:
                        break
                    key, item, save_dir, quality = self.job_queue.popleft()
                    self.active_progress[key] = 0.0

                result = self.download_one(key, item, save_dir, quality)

                with self.dl_cond:
                    self.active_progress.pop(key, None)
                    self.active_speed.pop(key, None)
                    self.done_count += 1
                    if result == 'failed':
                        self.failed_videos.append((key, item))
                if result == 'disk_full':
                    self.after(0, lambda: messagebox.showerror("Disk Error", "Insufficient disk space to continue."))
                    self.cancel_event.set()
                    with self.dl_cond:
                        self.job_queue.clear()
                self.refresh_progress()
        finally:
            with self.dl_cond:
                self.worker_count -= 1
                self.dl_cond.notify_all()

    def download_one(self, key, item, save_dir, quality):
        """Download and post-process one video. Returns 'ok', 'failed', 'cancelled' or 'disk_full'."""
        entry = item['entry']
        video_url = (entry.get('url') or entry.get('webpage_url')) if entry else None
        if not video_url:
            self.update_video_status(key, "❌", "#d35b58")
            return 'failed'

        # Each queued playlist downloads into its own subfolder
        video_dir = os.path.join(save_dir, item['subdir']) if item['subdir'] else save_dir
        os.makedirs(video_dir, exist_ok=True)
        with self.dl_cond:
            self.used_dirs.add(video_dir)

        video_index = item['index']
        title = entry.get('title', f'Video {video_index}')
        self.log_message(f"Starting: {title}")
        self.update_video_status(key, "🔽", "white", extra="0%")

        index_str = str(video_index).zfill(2)
        ydl_opts = self.get_ydl_opts(quality, video_dir, video_index)
        ydl_opts['progress_hooks'] = [self.make_progress_hook(key, '+' in ydl_opts.get('format', ''))]
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            escaped_dir = glob.escape(video_dir)

            # ignoreerrors hides download failures, so confirm the output actually exists
            if "Audio Only" in quality:
                output_matches = glob.glob(os.path.join(escaped_dir, f"{index_str} - *.mp3"))
            else:
                output_matches = [f for ext in ('mkv', 'mp4', 'webm')
                                  for f in glob.glob(os.path.join(escaped_dir, f"{index_str} - *.{ext}"))
                                  if not f.endswith('.temp_sub.mkv') and not re.search(r'\.f\d+(-\d+)?\.\w+$', f)]
            if not output_matches:
                raise Exception("Video file was not created (download failed).")

            # Process subtitles & auto-translate to Arabic if requested
            if self.subtitles_var.get():
                video_matches = [f for f in output_matches if f.endswith(('.mkv', '.mp4'))]

                if video_matches:
                    video_file = video_matches[0]
                    base_no_ext = os.path.splitext(video_file)[0]

                    ar_subs = find_subtitle_files(video_dir, index_str, 'ar')
                    en_subs = find_subtitle_files(video_dir, index_str, 'en')

                    # If no en_subs file on disk, extract from video stream if already embedded
                    if not en_subs and not ar_subs:
                        temp_extracted = f"{base_no_ext}.en.srt"
                        if extract_subtitle_from_video(video_file, temp_extracted):
                            en_subs = [temp_extracted]

                    # Auto-translate to Arabic if requested and no native Arabic
                    target_ar_srt = f"{base_no_ext}.ar.srt"
                    if ("Arabic" in self.sub_lang_var.get()) and not ar_subs and en_subs:
                        self.log_message(f"Auto-translating subtitles to Arabic for {title}...")
                        self.update_video_status(key, "⚙️", "white", extra="Translating")

                        if translate_srt_file(en_subs[0], target_ar_srt, target='ar'):
                            self.log_message(f"✅ Arabic subtitles created: {os.path.basename(target_ar_srt)}")
                            ar_subs = [target_ar_srt]
                            # Also keep a default .srt copy matching video name
                            try:
                                shutil.copyfile(target_ar_srt, f"{base_no_ext}.srt")
                            except Exception:
                                pass
                        else:
                            self.log_message(f"⚠️ Arabic translation failed for {title}.")
                    if "Arabic" in self.sub_lang_var.get() and not ar_subs:
                        self.log_message(f"⚠️ No Arabic subtitles available for {title} (no Arabic or English captions found).")

                    # Embed subtitles into the video container
                    if self.embed_subtitles_var.get() and "Audio Only" not in quality:
                        tracks = []
                        if ar_subs:
                            tracks.append((ar_subs[0], 'ara', 'Arabic (ترجمة عربية)'))
                        if "Arabic Only" not in self.sub_lang_var.get() and en_subs:
                            tracks.append((en_subs[0], 'eng', 'English (Original)'))

                        if tracks:
                            self.update_video_status(key, "⚙️", "white", extra="Embedding")
                            if embed_subtitles_to_video(video_file, tracks):
                                self.log_message(f"✅ Subtitles embedded into: {os.path.basename(video_file)}")
                                # Tracks now live inside the video, so drop the separate subtitle files
                                for sub_file in glob.glob(os.path.join(escaped_dir, f"{index_str} - *.*")):
                                    if sub_file.lower().endswith(('.srt', '.vtt')):
                                        try: os.remove(sub_file)
                                        except OSError: pass
                            else:
                                self.log_message(f"⚠️ Failed to embed subtitles for {title}; keeping separate subtitle files.")

            self.update_video_status(key, "✅", "#2fa572")
            return 'ok'
        except CancelDownloadException:
            self.update_video_status(key, "❌", "#d35b58")
            self.log_message(f"Cancelled: {title}")
            return 'cancelled'
        except Exception as e:
            err_msg = str(e)
            self.log_message(f"Error downloading {title}: {err_msg}")
            self.update_video_status(key, "❌", "#d35b58")
            if "No space left on device" in err_msg or "disk full" in err_msg.lower():
                return 'disk_full'
            return 'failed'

    def reset_ui(self):
        def _reset():
            self.fetch_btn.configure(state="normal")
            if self.fetched_entries:
                self.download_btn.configure(state="normal")
            self.download_btn.configure(text="2. Add to Queue" if self.is_downloading else "2. Download Selected")
            self.cancel_btn.configure(state="normal" if self.is_downloading and not self.cancel_event.is_set() else "disabled")
            if self.cancel_event.is_set() and not self.is_downloading:
                self.video_info_label.configure(text="Cancelled")
        self.after(0, _reset)


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    app = YouTubeDownloaderApp()
    app.mainloop()
