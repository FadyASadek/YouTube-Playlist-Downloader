import os
import sys
import glob
import shutil
import threading
import json
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import yt_dlp

# Settings file is always next to the script
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, 'settings.json')

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
        self.current_video_index = 0
        self.playlist_estimated_size = "Unknown"
        self.video_list_labels = {}
        self.video_list_titles = {}
        self.video_list_vars = {}

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
        ctk.CTkLabel(self.sidebar_frame, text="Playlist URL:").grid(row=1, column=0, sticky="w", padx=20)
        self.url_entry = ctk.CTkEntry(self.sidebar_frame, placeholder_text="https://...")
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
        self.subs_check = ctk.CTkCheckBox(self.sidebar_frame, text="Download Subtitles", variable=self.subtitles_var, command=self.save_settings)
        self.subs_check.grid(row=9, column=0, sticky="w", padx=20, pady=5)
        
        self.thumbnail_var = ctk.BooleanVar(value=False)
        self.thumb_check = ctk.CTkCheckBox(self.sidebar_frame, text="Embed Thumbnail", variable=self.thumbnail_var, command=self.save_settings)
        self.thumb_check.grid(row=10, column=0, sticky="w", padx=20, pady=(5, 20))

        self.sidebar_frame.grid_rowconfigure(11, weight=1) # Spacer
        
        # 5. Open Folder Button
        self.open_folder_btn = ctk.CTkButton(self.sidebar_frame, text="📂 Open Folder", command=self.open_folder, fg_color="#2fa572", hover_color="#23855a")
        self.open_folder_btn.grid(row=12, column=0, sticky="ew", padx=20, pady=(0, 20))

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

        ctk.CTkLabel(self.prog_area_frame, text="Current:").grid(row=1, column=0, sticky="w", padx=15)
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

    def save_settings(self):
        settings = {
            'save_dir': self.dir_entry.get(),
            'quality': self.quality_var.get(),
            'subtitles': self.subtitles_var.get(),
            'thumbnails': self.thumbnail_var.get()
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
                    if 'thumbnails' in settings:
                        self.thumbnail_var.set(settings['thumbnails'])
        except:
            pass

    def cleanup_temp_files(self, save_dir):
        """Remove leftover .part files and orphaned separate streams from failed downloads."""
        patterns = ['*.part', '*.ytdl']
        removed = 0
        for pattern in patterns:
            for f in glob.glob(os.path.join(save_dir, pattern)):
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
            clipboard_content = self.clipboard_get()
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, clipboard_content)
        except tk.TclError:
            pass

    def log_message(self, message):
        def _log():
            self.log_area.configure(state="normal")
            self.log_area.insert("end", message + "\n")
            self.log_area.see("end")
            self.log_area.configure(state="disabled")
        self.after(0, _log)

    def populate_video_list(self, entries):
        for widget in self.video_list_frame.winfo_children():
            widget.destroy()
        self.video_list_labels.clear()
        self.video_list_titles.clear()
        self.video_list_vars.clear()
        
        for index_tuple, entry in entries:
            title = entry.get('title', f'Video {index_tuple}')
            if len(title) > 75: title = title[:72] + "..."
            
            base_text = f"{index_tuple:02d} - {title}"
            
            row_frame = ctk.CTkFrame(self.video_list_frame, fg_color="transparent")
            row_frame.grid(row=index_tuple, column=0, sticky="ew", pady=2, padx=5)
            
            var = ctk.BooleanVar(value=True)
            self.video_list_vars[index_tuple] = var
            
            chk = ctk.CTkCheckBox(row_frame, text="", variable=var, width=24)
            chk.pack(side="left")
            
            lbl = ctk.CTkLabel(row_frame, text=f"⏳ {base_text}", anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            
            self.video_list_labels[index_tuple] = lbl
            self.video_list_titles[index_tuple] = base_text

    def update_video_status(self, index, status_icon, color=None):
        def _update():
            if index in self.video_list_labels:
                lbl = self.video_list_labels[index]
                base_text = self.video_list_titles.get(index, "")
                lbl.configure(text=f"{status_icon} {base_text}")
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
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter a valid YouTube URL.")
            return

        self.fetch_btn.configure(state="disabled")
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        
        self.log_area.configure(state="normal")
        self.log_area.delete('1.0', tk.END)
        self.log_area.configure(state="disabled")

        threading.Thread(target=self.fetch_process, args=(url,), daemon=True).start()

    def fetch_process(self, url):
        self.log_message(f"Extracting playlist info for: {url}")
        self.update_ui_safe(self.video_info_label, text="Extracting Playlist Metadata...")
        
        ydl_opts_info = {'extract_flat': 'in_playlist', 'quiet': True, 'no_warnings': True}
        try:
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    self.after(0, lambda: messagebox.showerror("Error", "Could not extract info. Check the URL or if the playlist is private."))
                    self.reset_ui()
                    return
                
                if 'entries' in info:
                    self.fetched_entries = list(info['entries'])
                else:
                    self.fetched_entries = [info]

            self.total_videos = len(self.fetched_entries)
            if self.total_videos == 0:
                self.after(0, lambda: messagebox.showerror("Error", "Playlist is empty or private."))
                self.reset_ui()
                return

            self.log_message(f"Found {self.total_videos} videos.")
            self.update_ui_safe(self.video_info_label, text=f"Ready to download. {self.total_videos} videos found. Please select videos and click Download.")
            
            entries_to_display = [(i+1, entry) for i, entry in enumerate(self.fetched_entries)]
            self.after(0, lambda: self.populate_video_list(entries_to_display))
            
            self.after(0, lambda: self.download_btn.configure(state="normal"))
            self.after(0, lambda: self.fetch_btn.configure(state="normal"))

        except Exception as e:
            self.log_message(f"Error fetching info: {str(e)}")
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to fetch info:\n{str(e)}"))
            self.reset_ui()

    def start_download(self, specific_entries=None):
        url = self.url_entry.get().strip()
        save_dir = self.dir_entry.get().strip()
        
        if not save_dir:
            messagebox.showerror("Error", "Please select a save directory.")
            return

        entries_to_download = []
        if specific_entries is None:
            if not self.fetched_entries:
                messagebox.showerror("Error", "No metadata fetched. Please click 'Fetch Info' first.")
                return
                
            for i, entry in enumerate(self.fetched_entries):
                idx = i + 1
                if self.video_list_vars.get(idx) and self.video_list_vars[idx].get():
                    entries_to_download.append((idx, entry))
            
            if not entries_to_download:
                messagebox.showerror("Error", "No videos selected for download.")
                return
        else:
            entries_to_download = specific_entries

        # UI state updates
        self.fetch_btn.configure(state="disabled")
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.cancel_event.clear()
        
        self.video_prog_bar.set(0)
        self.playlist_prog_bar.set(0)
        self.update_ui_safe(self.video_prog_bar, progress_color="#1f6aa5")
        
        self.video_pct_label.configure(text="0%")
        self.playlist_pct_label.configure(text="0%")
        self.stats_label.configure(text="Size: -- | Speed: -- | ETA: --")
        
        quality = self.quality_var.get()
        
        self.download_thread = threading.Thread(target=self.download_process, args=(save_dir, quality, entries_to_download), daemon=True)
        self.download_thread.start()

    def cancel_download(self):
        self.cancel_event.set()
        self.cancel_btn.configure(state="disabled")
        self.log_message("Cancelling download... Please wait.")

    def get_ydl_opts(self, quality, save_dir, video_index):
        index_str = str(video_index).zfill(2)
        opts = {
            'outtmpl': os.path.join(save_dir, f'{index_str} - %(title)s.%(ext)s'),
            'progress_hooks': [self.ydl_progress_hook],
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
            opts['subtitleslangs'] = ['ar', 'en']
            
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

    def ydl_progress_hook(self, d):
        if self.cancel_event.is_set():
            raise CancelDownloadException("Download cancelled by user.")

        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            
            if total_bytes > 0:
                raw_percent = downloaded / total_bytes
                
                # For bestvideo+bestaudio, yt-dlp downloads two streams sequentially.
                # Weight video stream as 80% and audio as 20% to prevent the
                # progress bar from jumping backwards when the audio stream starts.
                if self._is_multi_stream:
                    if self._stream_count == 0:
                        percent = raw_percent * 0.8
                        stream_label = "Video"
                    else:
                        percent = 0.8 + raw_percent * 0.2
                        stream_label = "Audio"
                else:
                    percent = raw_percent
                    stream_label = "Size"
                
                self.update_progress_safe(self.video_prog_bar, percent, self.video_pct_label, f"{percent*100:.1f}%")
                
                # Proportional Total Progress
                if hasattr(self, 'total_selected') and hasattr(self, 'current_loop_index') and self.total_selected > 0:
                    total_percent = (self.current_loop_index + percent) / self.total_selected
                    self.update_progress_safe(self.playlist_prog_bar, total_percent, self.playlist_pct_label, f"{total_percent*100:.1f}%")
                
                speed = self.format_speed(d.get('speed'))
                eta = self.format_eta(d.get('eta'))
                
                def update_stats(sl=stream_label, tb=total_bytes, sp=speed, et=eta):
                    self.stats_label.configure(text=f"{sl}: {self.format_bytes(tb)} | Speed: {sp} | ETA: {et}")
                self.after(0, update_stats)

        elif d['status'] == 'finished':
            self._stream_count += 1
            filename = d.get('filename', 'Video')
            
            if self._is_multi_stream and self._stream_count == 1:
                # First stream (video) done — audio stream coming next
                self.log_message(f"Video stream downloaded, fetching audio...")
                self.update_progress_safe(self.video_prog_bar, 0.8, self.video_pct_label, "80%")
            else:
                # Single stream finished, or final stream of multi-stream
                self.log_message(f"Finished downloading: {os.path.basename(filename)}")
                self.update_progress_safe(self.video_prog_bar, 1.0, self.video_pct_label, "100%")
                
                if hasattr(self, 'total_selected') and hasattr(self, 'current_loop_index') and self.total_selected > 0:
                    total_percent = (self.current_loop_index + 1.0) / self.total_selected
                    self.update_progress_safe(self.playlist_prog_bar, total_percent, self.playlist_pct_label, f"{total_percent*100:.1f}%")
                
                if self._is_multi_stream:
                    self.after(0, lambda: self.stats_label.configure(text="Merging audio & video..."))

        elif d['status'] == 'postprocessing' or d['status'] == 'processing':
            self.after(0, lambda: self.stats_label.configure(text="Post-processing (merging)..."))

    def download_process(self, save_dir, quality, entries_to_dl):
        try:
            if shutil.which("ffmpeg") is None:
                err = ("FFmpeg is not installed or not in PATH!\n\n"
                       "FFmpeg is required for downloading and merging video+audio streams.\n"
                       "Please install FFmpeg and make sure it's in your system PATH.")
                self.after(0, lambda: messagebox.showerror("FFmpeg Missing", err))
                self.reset_ui()
                return

            # --- Quality Pre-Check ---
            # Before downloading, check if the requested quality actually exists
            requested_height = None
            if "1080p" in quality:
                requested_height = 1080
            elif "720p" in quality:
                requested_height = 720
            elif "480p" in quality:
                requested_height = 480

            if requested_height and "Audio Only" not in quality:
                self.update_ui_safe(self.video_info_label, text="Checking available quality...")
                self.log_message("Checking available video quality...")
                
                # Pick the first valid video to check
                check_url = None
                for _, e in entries_to_dl:
                    if e:
                        check_url = e.get('url') or e.get('webpage_url')
                        if check_url:
                            break
                
                if check_url:
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
                                self.reset_ui()
                                return
                            else:
                                self.log_message(f"✅ Quality check passed: {max_height}p available.")
                    except Exception as e:
                        self.log_message(f"Could not verify quality: {str(e)}. Proceeding anyway...")

            failed_videos = []
            self.total_selected = len(entries_to_dl)

            for idx, e in entries_to_dl:
                self.update_video_status(idx, "⏳")

            for loop_index, (video_index, entry) in enumerate(entries_to_dl):
                self.current_loop_index = loop_index
                if self.cancel_event.is_set():
                    self.log_message("Download process cancelled.")
                    break
                    
                if not entry: continue
                
                video_url = entry.get('url') or entry.get('webpage_url')
                if not video_url: continue
                
                self.current_video_index = video_index
                title = entry.get('title', f'Video {self.current_video_index}')
                
                self.update_ui_safe(self.video_info_label, text=f"Downloading: {title} ({loop_index + 1} / {self.total_selected})")
                self.log_message(f"Starting: {title}")
                
                self.update_video_status(self.current_video_index, "🔽", "white")
                self.update_ui_safe(self.video_prog_bar, progress_color="#1f6aa5")
                self.update_progress_safe(self.video_prog_bar, 0, self.video_pct_label, "0%")
                
                ydl_opts = self.get_ydl_opts(quality, save_dir, self.current_video_index)
                self._stream_count = 0
                self._is_multi_stream = '+' in ydl_opts.get('format', '')
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        ydl.download([video_url])
                        self.update_video_status(self.current_video_index, "✅", "#2fa572")
                        self.update_ui_safe(self.video_prog_bar, progress_color="#2fa572")
                    except CancelDownloadException:
                        self.update_video_status(self.current_video_index, "❌", "#d35b58")
                        self.log_message("Cancelled during video download.")
                        break
                    except Exception as e:
                        err_msg = str(e)
                        self.log_message(f"Error downloading {title}: {err_msg}")
                        self.update_video_status(self.current_video_index, "❌", "#d35b58")
                        self.update_ui_safe(self.video_prog_bar, progress_color="#d35b58")
                        if "No space left on device" in err_msg or "disk full" in err_msg.lower():
                            self.after(0, lambda: messagebox.showerror("Disk Error", "Insufficient disk space to continue."))
                            break
                        failed_videos.append((video_index, entry))
                        continue

                # Ensure playlist progress reflects the completed video
                playlist_percent = (loop_index + 1) / self.total_selected
                self.update_progress_safe(self.playlist_prog_bar, playlist_percent, self.playlist_pct_label, f"{playlist_percent*100:.1f}%")

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
                    self.cleanup_temp_files(save_dir)
                    self.after(0, lambda: messagebox.showinfo("Success", "Download process finished!"))

        except Exception as e:
            self.log_message(f"A fatal error occurred: {str(e)}")
            self.after(0, lambda: messagebox.showerror("Error", f"A fatal error occurred:\n{str(e)}"))
        finally:
            self.reset_ui()

    def reset_ui(self):
        self.after(0, lambda: self.fetch_btn.configure(state="normal"))
        if self.fetched_entries:
            self.after(0, lambda: self.download_btn.configure(state="normal"))
        self.after(0, lambda: self.cancel_btn.configure(state="disabled"))
        if self.cancel_event.is_set():
             self.after(0, lambda: self.video_info_label.configure(text="Cancelled"))


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    app = YouTubeDownloaderApp()
    app.mainloop()
