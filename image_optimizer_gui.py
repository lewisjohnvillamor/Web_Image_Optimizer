import os
import logging
import time
import threading
import queue
from io import BytesIO

# --- Image Processing Libs ---
from PIL import Image, UnidentifiedImageError

# --- GUI Lib ---
import customtkinter as ctk
from tkinter import filedialog, messagebox

# --- Constants ---
SUPPORTED_INPUT_FORMATS = ('.jpg', '.jpeg', '.png')
OUTPUT_FORMAT = 'WEBP'

# --- Setup Logging ---
log_queue = queue.Queue()
logger = logging.getLogger('image_optimizer')
logger.setLevel(logging.INFO) # Use INFO for standard, DEBUG for more detail

# --- Helper Function for Formatting Bytes ---
def format_bytes(size_bytes):
    """Converts bytes into a human-readable format (KiB, MiB, etc.)."""
    if size_bytes is None or size_bytes < 0: return "N/A"
    if size_bytes == 0: return "0 B"
    power = 1024
    n = 0
    power_labels = {0 : ' B', 1: ' KiB', 2: ' MiB', 3: ' GiB', 4: ' TiB'}
    while size_bytes >= power and n < len(power_labels) - 1 :
        size_bytes /= power
        n += 1
    return f"{size_bytes:.1f}{power_labels[n]}"


# --- Core Image Processing Function (Unchanged from No-SVG version) ---
def optimize_image_to_webp(input_path, output_path, quality=80, lossless=False, strip_metadata=True, method=4):
    """
    Opens a raster image (JPG, PNG), converts it to optimized WEBP, and saves it.
    (Function body remains the same as the previous No-SVG version)
    """
    logger.debug(f"Processing '{os.path.basename(input_path)}' -> '{os.path.basename(output_path)}'")
    img = None
    try:
        img = Image.open(input_path)
        save_options = {
            'format': OUTPUT_FORMAT,
            'lossless': lossless,
            'method': method
        }
        if not lossless:
            save_options['quality'] = quality
        original_icc = img.info.get('icc_profile')
        original_exif = img.info.get('exif')
        if strip_metadata:
            save_options['icc_profile'] = None
            save_options['exif'] = b''
        else:
             save_options['icc_profile'] = original_icc
             save_options['exif'] = original_exif
        img.save(output_path, **save_options)
        logger.debug(f"Successfully saved WEBP: {output_path}")
        return True
    except FileNotFoundError:
        logger.error(f"Input image not found: {input_path}")
        return False
    except UnidentifiedImageError:
         logger.error(f"Cannot identify image file (possibly corrupt or unsupported format): {input_path}")
         return False
    except Exception as e:
        logger.error(f"Error processing image '{input_path}': {e}", exc_info=False)
        return False
    finally:
        if img:
            img.close()


# --- GUI Application Class (Mostly unchanged, task function modified) ---
class ImageOptimizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        # --- (All GUI widget initialization code remains the same) ---
        self.title("WebP Image Optimizer (JPG/PNG)") # Title updated slightly
        self.geometry("800x700")
        self.minsize(700, 600)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1) # Log area expansion

        # --- Input Folder ---
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        self.input_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.input_frame, text="Input Folder (JPG/PNG):").grid(row=0, column=0, padx=(10, 5), pady=10, sticky="w") # Text updated
        self.input_path_var = ctk.StringVar()
        self.input_entry = ctk.CTkEntry(self.input_frame, textvariable=self.input_path_var)
        self.input_entry.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
        ctk.CTkButton(self.input_frame, text="Browse...", command=self.browse_input, width=100).grid(row=0, column=2, padx=(5, 10), pady=10)

        # --- Output Folder ---
        self.output_frame = ctk.CTkFrame(self)
        self.output_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        self.output_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.output_frame, text="Output Folder (WebP):").grid(row=0, column=0, padx=(10, 5), pady=10, sticky="w")
        self.output_path_var = ctk.StringVar()
        self.output_entry = ctk.CTkEntry(self.output_frame, textvariable=self.output_path_var)
        self.output_entry.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
        ctk.CTkButton(self.output_frame, text="Browse...", command=self.browse_output, width=100).grid(row=0, column=2, padx=(5, 10), pady=10)

        # --- Settings ---
        self.settings_frame = ctk.CTkFrame(self)
        self.settings_frame.grid(row=2, column=0, padx=20, pady=10, sticky="ew")
        # Quality Slider
        self.quality_var = ctk.IntVar(value=80)
        ctk.CTkLabel(self.settings_frame, text="Quality (Lossy):").grid(row=0, column=0, padx=(10, 5), pady=10, sticky="w")
        self.quality_slider = ctk.CTkSlider(self.settings_frame, from_=0, to=100, number_of_steps=100, variable=self.quality_var, command=self.update_quality_label)
        self.quality_slider.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
        self.quality_label = ctk.CTkLabel(self.settings_frame, text=f"{self.quality_var.get()}", width=35)
        self.quality_label.grid(row=0, column=2, padx=(5, 20), pady=10, sticky="w")
        # Lossless Checkbox
        self.lossless_var = ctk.BooleanVar(value=False)
        self.lossless_check = ctk.CTkCheckBox(self.settings_frame, text="Lossless", variable=self.lossless_var, command=self.toggle_quality_slider)
        self.lossless_check.grid(row=0, column=3, padx=10, pady=10, sticky="w")
        # Strip Metadata Checkbox
        self.strip_metadata_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.settings_frame, text="Strip Metadata", variable=self.strip_metadata_var).grid(row=0, column=4, padx=10, pady=10, sticky="w")
        self.settings_frame.grid_columnconfigure(1, weight=1) # Allow slider to expand

        # --- Progress Bar ---
        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.grid(row=4, column=0, padx=20, pady=(5, 0), sticky="ew")
        self.progress_frame.grid_columnconfigure(0, weight=1)
        self.progressbar = ctk.CTkProgressBar(self.progress_frame, orientation="horizontal", mode='determinate')
        self.progressbar.grid(row=0, column=0, padx=0, pady=0, sticky="ew")
        self.progressbar.set(0)
        self.progress_label = ctk.CTkLabel(self.progress_frame, text="0/0 (0%)", anchor="w")
        self.progress_label.grid(row=1, column=0, padx=0, pady=(0,5), sticky="w")

        # --- Start Button ---
        self.start_button = ctk.CTkButton(self, text="Start Optimization", command=self.start_optimization_thread, height=40, font=("Arial", 14, "bold"))
        self.start_button.grid(row=5, column=0, padx=20, pady=15, sticky="ew")

        # --- Log Display ---
        self.log_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.log_frame.grid(row=3, column=0, padx=20, pady=(10, 10), sticky="nsew")
        self.log_frame.grid_rowconfigure(0, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_textbox = ctk.CTkTextbox(self.log_frame, state='disabled', wrap='word', activate_scrollbars=True)
        self.log_textbox.grid(row=0, column=0, sticky="nsew")

        # --- Logging Setup ---
        self.queue_handler = QueueHandler(log_queue)
        log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
        self.queue_handler.setFormatter(log_formatter)
        logger.addHandler(self.queue_handler)
        self.after(100, self.process_log_queue) # Start checking queue

        # Initial UI state
        self.toggle_quality_slider()


    def update_quality_label(self, value):
        self.quality_label.configure(text=f"{int(value)}")

    def toggle_quality_slider(self):
        if self.lossless_var.get():
            self.quality_slider.configure(state='disabled')
            self.quality_label.configure(state='disabled')
        else:
            self.quality_slider.configure(state='normal')
            self.quality_label.configure(state='normal')

    def browse_input(self):
        dir_selected = filedialog.askdirectory(title="Select Input Folder Containing Images")
        if dir_selected:
            self.input_path_var.set(dir_selected)
            if not self.output_path_var.get():
                self.output_path_var.set(os.path.join(dir_selected, "WEBP_Optimized"))

    def browse_output(self):
        dir_selected = filedialog.askdirectory(title="Select Output Folder for WebP Images")
        if dir_selected:
            self.output_path_var.set(dir_selected)

    def process_log_queue(self):
        try:
            while True:
                record = log_queue.get_nowait()
                msg = self.queue_handler.format(record)
                self.log_textbox.configure(state='normal')
                self.log_textbox.insert('end', msg + '\n')
                self.log_textbox.configure(state='disabled')
                self.log_textbox.see('end')
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_log_queue)

    def update_progress(self, current, total):
        if total > 0:
            progress_float = current / total
            self.progressbar.set(progress_float)
            percent = int(progress_float * 100)
            self.progress_label.configure(text=f"{current}/{total} ({percent}%)")
        else:
            self.progressbar.set(0)
            self.progress_label.configure(text="0/0 (0%)")
        self.update_idletasks()

    def start_optimization_thread(self):
        # --- (Validation code remains the same) ---
        input_dir = self.input_path_var.get()
        output_dir = self.output_path_var.get()
        quality = self.quality_var.get()
        lossless = self.lossless_var.get()
        strip_meta = self.strip_metadata_var.get()

        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showerror("Error", "Please select a valid input folder.", parent=self)
            return
        if not output_dir:
            messagebox.showerror("Error", "Please select an output folder.", parent=self)
            return
        if input_dir == output_dir:
             if not messagebox.askyesno("Warning", "Input and output folders are the same. This will overwrite original files if they have the same base name (with .webp extension). Continue?", parent=self):
                 return

        self.start_button.configure(state='disabled', text="Optimizing...")
        self.log_textbox.configure(state='normal')
        self.log_textbox.delete("1.0", 'end')
        self.log_textbox.configure(state='disabled')
        self.progressbar.set(0)
        self.progress_label.configure(text="Starting...")

        self.conversion_thread = threading.Thread(
            target=self._run_optimization_task,
            args=(input_dir, output_dir, quality, lossless, strip_meta),
            daemon=True
        )
        self.conversion_thread.start()


    # --- MODIFIED TASK FUNCTION ---
    def _run_optimization_task(self, input_dir, output_dir, quality, lossless, strip_meta):
        """Worker function executed in a separate thread."""
        logger.info("=" * 60)
        logger.info("Starting Image Optimization Process...")
        # (Initial logging messages remain the same)
        logger.info(f"Input Folder: {input_dir}")
        logger.info(f"Output Folder: {output_dir}")
        logger.info(f"Lossless: {lossless}")
        if not lossless: logger.info(f"Quality (Lossy): {quality}")
        logger.info(f"Strip Metadata: {strip_meta}")
        logger.info(f"Target Format: {OUTPUT_FORMAT}")
        logger.info(f"Supported Input: {', '.join(SUPPORTED_INPUT_FORMATS)}")
        logger.info("-" * 60)

        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"Ensured output directory exists: {output_dir}")
        except OSError as e:
            logger.error(f"Failed to create output directory: {e}")
            self.after(0, lambda: self.start_button.configure(state='normal', text="Start Optimization"))
            self.after(0, lambda: self.progress_label.configure(text="Error!"))
            return

        start_time = time.time()
        files_to_process = []
        for fname in os.listdir(input_dir):
            fpath = os.path.join(input_dir, fname)
            ext = os.path.splitext(fname)[1].lower()
            if os.path.isfile(fpath) and ext in SUPPORTED_INPUT_FORMATS:
                files_to_process.append(fname)

        total_files = len(files_to_process)
        processed_count = 0
        success_count = 0
        fail_count = 0
        total_original_size = 0
        total_new_size = 0
        self.after(0, self.update_progress, 0, total_files)

        if total_files == 0:
            logger.warning(f"No supported image files ({', '.join(SUPPORTED_INPUT_FORMATS)}) found in the input folder.")
        else:
            logger.info(f"Found {total_files} supported images to process.")

            for i, filename in enumerate(files_to_process):
                input_filepath = os.path.join(input_dir, filename)
                base_name = os.path.splitext(filename)[0]
                output_filename = f"{base_name}.webp"
                output_filepath = os.path.join(output_dir, output_filename)

                logger.info(f"--- Processing [{i+1}/{total_files}]: {filename} ---")

                # --- Get Original Size ---
                original_size = -1 # Default if error
                try:
                    original_size = os.path.getsize(input_filepath)
                except FileNotFoundError:
                    logger.error(f"  Input file disappeared before size check: {filename}")
                    fail_count += 1
                    processed_count += 1
                    self.after(0, self.update_progress, processed_count, total_files)
                    continue # Skip to next file
                except Exception as size_e:
                     logger.warning(f"  Could not get original size for {filename}: {size_e}")


                # --- Perform Conversion ---
                success = optimize_image_to_webp(
                    input_filepath,
                    output_filepath,
                    quality,
                    lossless,
                    strip_meta
                )

                # --- Log Size Reduction on Success ---
                processed_count += 1
                if success:
                    success_count += 1
                    new_size = -1 # Default if error
                    try:
                        new_size = os.path.getsize(output_filepath)
                        if original_size >= 0: # Check if original size was obtained
                            total_original_size += original_size
                            total_new_size += new_size
                            reduction_bytes = original_size - new_size
                            if original_size > 0:
                                percentage_reduction = (reduction_bytes / original_size) * 100
                                sign = "-" if percentage_reduction < 0 else "+" # Indicate increase or decrease
                                logger.info(f"  Reduction: {format_bytes(original_size)} -> {format_bytes(new_size)} ({sign}{abs(percentage_reduction):.1f}%)")
                            else: # Original size was 0
                                logger.info(f"  Converted: {format_bytes(original_size)} -> {format_bytes(new_size)}")
                        else:
                            # Log success but mention unknown original size
                            logger.info(f"  Successfully converted. New size: {format_bytes(new_size)} (Original size unknown)")

                    except FileNotFoundError:
                         logger.warning(f"  Successfully converted, but couldn't get size of output file: {output_filename}")
                         # Still count towards total_new_size if original was known? Maybe not.
                    except Exception as size_e:
                         logger.warning(f"  Successfully converted, but error getting output file size: {size_e}")
                else:
                    fail_count += 1

                # Update progress bar via main thread
                self.after(0, self.update_progress, processed_count, total_files)


        # --- Final Summary (with overall size reduction) ---
        end_time = time.time()
        duration = end_time - start_time
        logger.info("=" * 60)
        logger.info("Optimization Process Finished")
        logger.info(f"Total time: {duration:.2f} seconds")
        logger.info(f"Files Found: {total_files}")
        logger.info(f"Successfully Optimized: {success_count}")
        logger.info(f"Failed: {fail_count}")
        # Calculate and log overall reduction
        if total_original_size > 0 and success_count > 0: # Avoid division by zero and only show if successful conversions happened
            overall_reduction_bytes = total_original_size - total_new_size
            overall_percentage = (overall_reduction_bytes / total_original_size) * 100
            sign = "-" if overall_percentage < 0 else "+"
            logger.info(f"Overall Size Reduction: {format_bytes(total_original_size)} -> {format_bytes(total_new_size)} ({sign}{abs(overall_percentage):.1f}%)")
        elif success_count > 0:
             logger.info(f"Overall New Size (Successful): {format_bytes(total_new_size)}")
        logger.info("=" * 60)

        # --- Update UI Post-Processing ---
        final_msg = "Finished!" if fail_count == 0 else f"Finished with {fail_count} error(s)."
        self.after(0, lambda: self.progress_label.configure(text=final_msg))
        self.after(0, lambda: self.start_button.configure(state='normal', text="Start Optimization"))


# --- Custom Logging Handler ---
class QueueHandler(logging.Handler):
    # (Handler code remains the same)
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(record)

# --- Main Execution ---
if __name__ == "__main__":
    # (Main execution block remains the same)
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
        logger.addHandler(console_handler)

    app = ImageOptimizerApp()
    app.mainloop()