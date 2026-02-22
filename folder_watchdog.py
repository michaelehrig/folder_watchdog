#!/usr/bin/env python3
"""
ToSort folder watchdog:

Watches a folder called ~/ToSort in the home directory.
If a file is moved into this folder, moves files into subfolders by extension.
Images all get collected into a images/ subfolder instead.
If filename suggests an LLM generator (ChatGPT / Claude / etc),
move into images/<LLM>/.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ----------------------------
# Configuration
# ----------------------------

# Setting home directory
HOME = Path.home()

# This has to be changed if a different folder needs to be watched
WATCH_DIR = HOME / "ToSort"

# list of common image extensions
IMAGE_EXTS = {
    "png", "jpg", "jpeg", "webp", "gif",
    "bmp", "tiff", "tif", "heic", "heif", "avif"
}

# list of extensions for temporary files
TEMP_EXTS = {
    ".crdownload", ".part", ".download", ".tmp"
}

# LLM patterns that are being looked out for
# Currently only checks for keyword hints, as most LLMs do not save any extra metadata 
LLM_PATTERNS = [
    ("ChatGPT", re.compile(r"\b(chatgpt|openai|gpt[-_ ]?\w*)\b", re.IGNORECASE)),
    ("Claude", re.compile(r"\b(claude|anthropic)\b", re.IGNORECASE)),
    ("Gemini", re.compile(r"\b(gemini|bard)\b", re.IGNORECASE)),
    ("DALL-E", re.compile(r"\b(dall[-_ ]?e|dalle)\b", re.IGNORECASE)),
    ("Midjourney", re.compile(r"\b(midjourney|mj)\b", re.IGNORECASE)),
    ("StableDiffusion", re.compile(r"\b(stable[-_ ]?diffusion|sdxl)\b", re.IGNORECASE)),
]


# ----------------------------
# Helpers
# ----------------------------

def normalize_ext(p: Path) -> str:
    """Normalizes extension of file

    Args:
        p (Path): file to normalize extension of

    Returns:
        str: lower caps, period removed, extension of the file
    """
    ext = p.suffix.lower().lstrip(".")
    return ext or "noext"

def is_temp_file(p: Path) -> bool:
    """Checks whether file is temporary

    Args:
        p (Path): file to check

    Returns:
        bool: whether it is detected as temporary or not
    """
    name = p.name.lower()
    return any(name.endswith(x) for x in TEMP_EXTS)

def wait_until_stable(path: Path, timeout: float = 20) -> bool:
    """Waits until a file is not changed anymore

    Args:
        path (Path): file to be observed
        timeout (float, optional): How long should be waited. Defaults to 20.

    Returns:
        bool: whether file is stable or not
    """

    end = time.time() + timeout
    last_size = -1

    while time.time() < end:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            # If we cannot get a size, we stop
            return False

        # If size stops changing every 0.3 seconds we return with a stable file
        if size == last_size:
            return True

        last_size = size
        time.sleep(0.3)

    return False


def detect_llm(filename: str) -> Optional[str]:
    """Detect whether an LLM marker can be found in the file name

    Args:
        filename (str): name of the file that needs checking

    Returns:
        Optional[str]: If an LLM marker is found return the label of it
    """
    for label, pattern in LLM_PATTERNS:
        if pattern.search(filename):
            return label
    return None


def unique_destination(dest: Path) -> Path:
    """Creates a unique destination if a file with the same name already exists

    Args:
        dest (Path): original destination

    Raises:
        RuntimeError: if no alternative can be found

    Returns:
        Path: new destination
    """

    # if file does not exist, the original destination is fine
    if not dest.exists():
        return dest

    # Otherwise split file path into components
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent

    # Add running number from 1 to 10_000 in between stem and suffix
    for i in range(1, 10000):
        candidate = parent / f"{stem}_{i}{suffix}"
        # if it works return new name
        if not candidate.exists():
            return candidate

    # If none of these numbers work, raise a RuntimeError
    raise RuntimeError("Could not create unique filename")


def move_file(src: Path, target_dir: Path) -> Path:
    """Moves file

    Args:
        src (Path): source file
        target_dir (Path): target directory

    Returns:
        Path: full path of the target file
    """
    
    # create target directory if needed
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # create target file path with possible unique name extension
    dest = unique_destination(target_dir / src.name)
    
    # move file
    shutil.move(str(src), str(dest))

    # return target full file path
    return dest


# ----------------------------
# Sorting Logic
# ----------------------------

def decide_target(base: Path, file_path: Path) -> Path:
    """Decide where to move a file

    Args:
        base (Path): base path of the destination
        file_path (Path): path of the file to be moved

    Returns:
        Path: path of the target directory
    """
    # isolate and normalize the extension of the file
    ext = normalize_ext(file_path)

    # Check if it is an image extension
    if ext in IMAGE_EXTS:
        # check if it comes from an LLM
        llm = detect_llm(file_path.name)
        
        # return the appropriate subfolder for either case
        if llm:
            return base / "images" / llm
        return base / "images"

    # return the subfolder based on extension
    return base / ext


# ----------------------------
# Watchdog Handler
# ----------------------------

class SortHandler(FileSystemEventHandler):

    def __init__(self, base_dir: Path):
        # store the full path
        self.base_dir = base_dir.resolve()

    def process(self, path: Path):
        # if directory do nothing
        if path.is_dir():
            return

        # if temporary file do nothing
        if is_temp_file(path):
            return

        # if the parent directory is not the one we monitor, do nothing
        if path.parent.resolve() != self.base_dir:
            return

        # we wait until the file is stable, if it is not stable in 20 seconds, do nothing
        if not wait_until_stable(path):
            return

        # retrieve correct target destination
        target_dir = decide_target(self.base_dir, path)

        # if for some reason the target destination is the parent directory, do nothing
        if target_dir == path.parent:
            return

        # try to move the file from its current location to the target directory
        try:
            new_path = move_file(path, target_dir)
            print(f"[MOVED] {path.name} -> {new_path.relative_to(self.base_dir)}")
        except Exception as e:
            print(f"[ERROR] {e}")

    # if a file is created process it
    def on_created(self, event):
        self.process(Path(event.src_path))

    # if a file is moved in the directory process it
    def on_moved(self, event):
        self.process(Path(event.dest_path))


# ----------------------------
# Main
# ----------------------------

def main():

    # Create the watched directory if it does not exist
    WATCH_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[WATCHING] {WATCH_DIR}")
    print("Drop files into this folder. Ctrl+C to stop.\n")

    # Initialize the sorting Handler
    handler = SortHandler(WATCH_DIR)

    # Initialize an observer that watches the directory and uses our handler
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()

    # setup the execution loop, 
    # until a KeyboardInterrupt happens and
    # finally stop the observer and exit the process
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
