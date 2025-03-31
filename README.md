# WebP Image Optimizer GUI

**Author:** Lewis John Villamor

A simple and modern desktop application using Python, CustomTkinter, and Pillow to convert JPG and PNG images into optimized WEBP format for better web performance.

![Screenshot Placeholder](screenshot.png)
*(Add a screenshot of your application here named `screenshot.png`)*

## Purpose

The primary goal of this tool is to help reduce the file size of common web image formats (JPG, PNG) by converting them to the modern and efficient WEBP format. Optimized images lead to faster website load times and reduced bandwidth consumption.

## Features

*   **Modern GUI:** Clean interface built with CustomTkinter, supporting system light/dark modes.
*   **Batch Conversion:** Processes all supported images (JPG, JPEG, PNG) within a selected input folder.
*   **WEBP Optimization:**
    *   **Lossy Quality Control:** Adjust the quality level for lossy WEBP compression using a slider.
    *   **Lossless Option:** Choose lossless WEBP compression for pixel-perfect conversion (generally larger file size).
    *   **Metadata Stripping:** Option to remove EXIF and ICC profile data to further reduce file size.
*   **Folder Selection:** Easy-to-use browse buttons for selecting input and output directories.
*   **Progress Monitoring:** Real-time progress bar and detailed logs displayed within the application.
*   **Size Reduction Logs:** Logs show the original size, new size, and percentage reduction for each successfully converted image.
*   **Responsive UI:** Background threading prevents the GUI from freezing during conversion.
*   **Cross-Platform (Potential):** Built with Python libraries aiming for compatibility across Windows, macOS, and Linux (ensure dependencies are met).

## Prerequisites

*   **Python:** Version 3.7 or higher recommended. Download from [python.org](https://www.python.org/).
*   **pip:** Python's package installer (usually included with Python).

## Installation

1.  **Clone or Download:**
    *   Clone the repository (if hosted on Git):
        ```bash
        git clone <your-repository-url>
        cd <repository-folder-name>
        ```
    *   Or download the source code files (`image_optimizer_gui.py`, `requirements.txt`, etc.) into a directory.

2.  **Set up a Virtual Environment (Recommended):**
    ```bash
    # Navigate to the project directory
    python -m venv venv
    # Activate the virtual environment
    # Windows:
    venv\Scripts\activate
    # macOS/Linux:
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    With your virtual environment activated, run:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

1.  **Run the Application:**
    Make sure your virtual environment is activated. Execute the script:
    ```bash
    python image_optimizer_gui.py
    ```

2.  **Using the Interface:**
    *   **Input Folder:** Click "Browse..." to select the folder containing your source JPG and PNG images.
    *   **Output Folder:** Click "Browse..." to choose where the converted WEBP images will be saved. A default suggestion based on the input folder may appear.
    *   **Quality (Lossy):** Adjust the slider (0-100) if using lossy compression. Lower values mean smaller files but lower visual quality. (Disabled if "Lossless" is checked).
    *   **Lossless:** Check this box for lossless conversion (preserves exact pixel data, generally larger than lossy).
    *   **Strip Metadata:** Check this box (recommended for web) to remove extra data like camera settings from the image files.
    *   **Start Optimization:** Click the button to begin converting all supported images in the input folder.
    *   **Logs & Progress:** Monitor the conversion details, including size reductions, and overall progress in the lower sections of the window.

## Limitations

*   **Input Formats:** Only processes `.jpg`, `.jpeg`, and `.png` files. Other formats (GIF, TIFF, BMP, SVG etc.) are ignored.
*   **Output Format:** Only outputs to WEBP.
*   **Corrupted Files:** May fail or produce errors if input images are corrupted or malformed.
*   **Large Files/Batches:** Processing very large images or a huge number of files can consume significant memory and time.
*   **Error Handling:** Basic error logging is provided, but the tool doesn't attempt complex recovery from individual file errors.

## Contributing

Contributions, issues, and feature requests are welcome! Feel free to:

1.  Fork the repository (if applicable).
2.  Create a feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

Or simply open an issue with the tag "bug" or "enhancement".

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This software is provided "AS IS", without warranty of any kind, express or implied. The author, Lewis John Villamor, is not liable for any claim, damages, or other liability arising from, out of, or in connection with the software or the use or other dealings in the software. Use this tool responsibly and consider backing up original images before processing, especially when setting input and output folders to the same location.