"""Convert all Word documents in ``уроки`` to Markdown in ``уроки_markdown``.

Run from any directory::

    python convert_to_markdown.py

Pandoc is used because it handles headings, lists, tables, footnotes, and
other common Word document structures more faithfully than a plain-text
extractor. Existing output files are replaced on each run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = BASE_DIR / "уроки"
OUTPUT_DIR = BASE_DIR / "уроки_markdown"


def main() -> None:
    if not SOURCE_DIR.is_dir():
        raise SystemExit(f"Source directory not found: {SOURCE_DIR}")

    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise SystemExit("Pandoc is required but was not found on PATH.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    documents = sorted(SOURCE_DIR.glob("*.docx"), key=lambda p: p.name.casefold())
    if not documents:
        print(f"No .docx files found in {SOURCE_DIR}")
        return

    for source in documents:
        destination = OUTPUT_DIR / f"{source.stem}.md"
        command = [
            pandoc,
            str(source),
            "--from=docx",
            "--to=gfm",
            "--wrap=none",
            "--output",
            str(destination),
        ]
        subprocess.run(command, check=True)
        print(f"Converted: {source.name} -> {destination.name}")

    print(f"Converted {len(documents)} document(s) into {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
