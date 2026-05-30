import os
import shutil
import threading
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import yt_dlp

class CancelDownloadException(Exception):
    pass

class YouTubeDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("YouTube Playlist Downloader")
        self.geometry("700x700")
        self.minsize(600, 650)
        
        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        
        # UI Elements Setup
        self.setup_ui()
        
        # State variables
        self.cancel_event = threading.Event()
        self.download_thread = None
        self.total_videos = 0
        self.current_video_index = 0
        self.playlist_estimated_size = 0

    def setup_ui(self):
        # 1. Header
        header = ctk.CTkLabel(self, text="YouTube Playlist Downloader", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, pady=(20, 20))

        # 2. URL Input
        url_frame = ctk.CTkFrame(self, fg_color="transparent")
        url_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")
        url_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(url_frame, text="Playlist URL:").grid(row=0, column=0, sticky="w")
        
        url_input_frame = ctk.CTkFrame(url_frame, fg_color="transparent")
        url_input_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        url_input_frame.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(url_input_frame, placeholder_text="https://www.youtube.com/playlist?list=...")
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.paste_btn = ctk.CTkButton(url_input_frame, text="Paste", width=60, command=self.paste_url)
        self.paste_btn.grid(row=0, column=1)

        # 3. Directory Selector
        dir_frame = ctk.CTkFrame(self, fg_color="transparent")
        dir_frame.grid(row=2, column=0, padx=20, pady=15, sticky="ew")
        dir_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(dir_frame, text="Save to Directory:").grid(row=0, column=0, sticky="w")
        
        dir_input_frame = ctk.CTkFrame(dir_frame, fg_color="transparent")
        dir_input_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        dir_input_frame.grid_columnconfigure(0, weight=1)
        self.dir_entry = ctk.CTkEntry(dir_input_frame, state="readonly")
        self.dir_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.browse_btn = ctk.CTkButton(dir_input_frame, text="Browse", width=80, command=self.browse_directory)
        self.browse_btn.grid(row=0, column=1)

        # 4. Quality Selector
        quality_frame = ctk.CTkFrame(self, fg_color="transparent")
        quality_frame.grid(row=3, column=0, padx=20, pady=5, sticky="ew")
        ctk.CTkLabel(quality_frame, text="Quality:").grid(row=0, column=0, sticky="w", padx=(0, 10))
        
        self.quality_var = ctk.StringVar(value="720p")
        self.quality_menu = ctk.CTkOptionMenu(quality_frame, variable=self.quality_var, 
                                              values=["1080p (Requires FFmpeg)", "720p", "480p", "Audio Only (MP3)"])
        self.quality_menu.grid(row=0, column=1, sticky="w")

        # 5. Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=20, pady=20)
        
        self.download_btn = ctk.CTkButton(btn_frame, text="Download", command=self.start_download)
        self.download_btn.grid(row=0, column=0, padx=10)
        
        self.cancel_btn = ctk.CTkButton(btn_frame, text="Cancel", command=self.cancel_download, state="disabled", fg_color="red", hover_color="#8b0000")
        self.cancel_btn.grid(row=0, column=1, padx=10)

        # 6. Size Information
        self.size_label = ctk.CTkLabel(self, text="Est. Size: 0 MB / Playlist: Unknown")
        self.size_label.grid(row=5, column=0, pady=5)

        # 7. Current Video Info
        self.video_info_label = ctk.CTkLabel(self, text="Ready", font=ctk.CTkFont(weight="bold"))
        self.video_info_label.grid(row=6, column=0, pady=(10, 5))

        # 8 & 9. Progress Bars and Labels
        prog_frame = ctk.CTkFrame(self, fg_color="transparent")
        prog_frame.grid(row=7, column=0, padx=20, sticky="ew")
        prog_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(prog_frame, text="Video:").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.video_prog_bar = ctk.CTkProgressBar(prog_frame)
        self.video_prog_bar.grid(row=0, column=1, sticky="ew")
        self.video_prog_bar.set(0)
        self.video_pct_label = ctk.CTkLabel(prog_frame, text="0%", width=40)
        self.video_pct_label.grid(row=0, column=2, padx=(10, 0))

        ctk.CTkLabel(prog_frame, text="Playlist:").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        self.playlist_prog_bar = ctk.CTkProgressBar(prog_frame)
        self.playlist_prog_bar.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        self.playlist_prog_bar.set(0)
        self.playlist_pct_label = ctk.CTkLabel(prog_frame, text="0%", width=40)
        self.playlist_pct_label.grid(row=1, column=2, padx=(10, 0), pady=(10, 0))

        # 10. Log Area
        self.log_area = ctk.CTkTextbox(self, height=150)
        self.log_area.grid(row=8, column=0, padx=20, pady=20, sticky="nsew")
        self.log_area.configure(state="disabled")
        self.grid_rowconfigure(8, weight=1)

    def browse_directory(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.dir_entry.configure(state="normal")
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, dir_path)
            self.dir_entry.configure(state="readonly")

    def paste_url(self):
        try:
            clipboard_content = self.clipboard_get()
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, clipboard_content)
        except tk.TclError:
            pass # Clipboard empty or unsupported format

    def log_message(self, message):
        def _log():
            self.log_area.configure(state="normal")
            self.log_area.insert("end", message + "\n")
            self.log_area.see("end")
            self.log_area.configure(state="disabled")
        self.after(0, _log)

    def update_ui_safe(self, widget, **kwargs):
        self.after(0, lambda: widget.configure(**kwargs))
        
    def update_progress_safe(self, bar, value, label, text):
        def _update():
            bar.set(value)
            label.configure(text=text)
        self.after(0, _update)

    def start_download(self):
        url = self.url_entry.get().strip()
        save_dir = self.dir_entry.get().strip()
        
        if not url:
            messagebox.showerror("Error", "Please enter a valid YouTube URL.")
            return
        if not save_dir:
            messagebox.showerror("Error", "Please select a save directory.")
            return

        # UI state updates
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.cancel_event.clear()
        
        self.video_prog_bar.set(0)
        self.playlist_prog_bar.set(0)
        self.video_pct_label.configure(text="0%")
        self.playlist_pct_label.configure(text="0%")
        
        self.log_area.configure(state="normal")
        self.log_area.delete('1.0', tk.END)
        self.log_area.configure(state="disabled")
        
        quality = self.quality_var.get()
        
        # Start background thread
        self.download_thread = threading.Thread(target=self.download_process, args=(url, save_dir, quality), daemon=True)
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
            'ignoreerrors': True, # Skip private/deleted videos without crashing
            'quiet': True,
            'no_warnings': True,
        }

        if "1080p" in quality:
            opts['format'] = 'bestvideo[height<=1080]+bestaudio/best'
            opts['merge_output_format'] = 'mkv'
        elif "720p" in quality:
            # Using /best ensures it downloads a pre-merged file if ffmpeg is missing
            opts['format'] = 'best[height<=720]/best'
        elif "480p" in quality:
            opts['format'] = 'best[height<=480]/best'
        elif "Audio Only" in quality:
            opts['format'] = 'bestaudio/best'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
            opts['outtmpl'] = os.path.join(save_dir, f'{index_str} - %(title)s.mp3')
            
        return opts

    def format_bytes(self, bytes_val):
        if not bytes_val: return "Unknown"
        mb = bytes_val / (1024 * 1024)
        return f"{mb:.2f} MB"

    def ydl_progress_hook(self, d):
        if self.cancel_event.is_set():
            raise CancelDownloadException("Download cancelled by user.")

        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            
            if total_bytes > 0:
                percent = downloaded / total_bytes
                self.update_progress_safe(self.video_prog_bar, percent, self.video_pct_label, f"{percent*100:.1f}%")
                
                # Update size estimation safely
                def update_size():
                    self.size_label.configure(text=f"Est. Size: {self.format_bytes(total_bytes)} / Playlist: {self.playlist_estimated_size}")
                self.after(0, update_size)

        elif d['status'] == 'finished':
            self.log_message(f"Finished downloading: {d.get('filename', 'Video')}")
            self.update_progress_safe(self.video_prog_bar, 1.0, self.video_pct_label, "100%")

    def download_process(self, url, save_dir, quality):
        try:
            self.log_message(f"Extracting playlist info for: {url}")
            self.update_ui_safe(self.video_info_label, text="Extracting Playlist Metadata...")
            
            # 1. First, extract info to get the total number of videos
            ydl_opts_info = {'extract_flat': 'in_playlist', 'quiet': True, 'no_warnings': True}
            
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    self.after(0, lambda: messagebox.showerror("Error", "Could not extract info. Check the URL or if the playlist is private."))
                    self.reset_ui()
                    return
                
                if 'entries' in info:
                    entries = list(info['entries'])
                    self.total_videos = len(entries)
                    self.log_message(f"Found {self.total_videos} videos in playlist.")
                else:
                    self.total_videos = 1
                    self.log_message("Found 1 single video.")
                    entries = [info]
            
            if self.total_videos == 0:
                self.after(0, lambda: messagebox.showerror("Error", "Playlist is empty or private."))
                self.reset_ui()
                return

            self.playlist_estimated_size = "Unknown" # Calculating full size takes too long, stick to Unknown

            # Check for FFmpeg if required
            if ("1080p" in quality or "Audio Only" in quality) and shutil.which("ffmpeg") is None:
                err = ("FFmpeg is not installed or not in PATH!\n\n"
                       "1080p requires FFmpeg to merge video and audio streams.\n"
                       "Audio Only (MP3) requires FFmpeg to convert the downloaded audio.\n\n"
                       "Please install FFmpeg or select '720p' or '480p' instead.")
                self.after(0, lambda: messagebox.showerror("FFmpeg Missing", err))
                self.reset_ui()
                return

            # 3. Iterate over playlist manually to track overall progress and cancel flag better
            for index, entry in enumerate(entries):
                if self.cancel_event.is_set():
                    self.log_message("Download process cancelled.")
                    break
                    
                if not entry: continue
                
                video_url = entry.get('url') or entry.get('webpage_url')
                if not video_url: continue
                
                self.current_video_index = index + 1
                title = entry.get('title', f'Video {self.current_video_index}')
                
                self.update_ui_safe(self.video_info_label, text=f"Downloading: {title} ({self.current_video_index} / {self.total_videos})")
                self.log_message(f"Starting: {title}")
                
                self.update_progress_safe(self.video_prog_bar, 0, self.video_pct_label, "0%")
                
                ydl_opts = self.get_ydl_opts(quality, save_dir, self.current_video_index)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        ydl.download([video_url])
                    except CancelDownloadException:
                        self.log_message("Cancelled during video download.")
                        break
                    except Exception as e:
                        err_msg = str(e)
                        self.log_message(f"Error downloading {title}: {err_msg}")
                        if "No space left on device" in err_msg or "disk full" in err_msg.lower():
                            self.after(0, lambda: messagebox.showerror("Disk Error", "Insufficient disk space to continue."))
                            break
                        continue # Continue to next video if it's just a network/video error

                # Update overall progress
                playlist_percent = self.current_video_index / self.total_videos
                self.update_progress_safe(self.playlist_prog_bar, playlist_percent, self.playlist_pct_label, f"{playlist_percent*100:.1f}%")

            if not self.cancel_event.is_set():
                self.log_message("All downloads completed successfully!")
                self.update_ui_safe(self.video_info_label, text="Completed!")
                self.after(0, lambda: messagebox.showinfo("Success", "Download process finished!"))

        except Exception as e:
            self.log_message(f"A fatal error occurred: {str(e)}")
            self.after(0, lambda: messagebox.showerror("Error", f"A fatal error occurred:\n{str(e)}"))
        finally:
            self.reset_ui()

    def reset_ui(self):
        self.after(0, lambda: self.download_btn.configure(state="normal"))
        self.after(0, lambda: self.cancel_btn.configure(state="disabled"))
        if self.cancel_event.is_set():
             self.after(0, lambda: self.video_info_label.configure(text="Cancelled"))


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    app = YouTubeDownloaderApp()
    app.mainloop()
