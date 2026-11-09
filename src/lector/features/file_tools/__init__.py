"""File tools (docs/PRD.md § "Post-v1: file tools"): Compress, Make
searchable, Export as images and Convert to PDF.

`engine` is the PyMuPDF side and runs in the OCR worker process; `jobs` runs
one job at a time from the main process and reports its progress; `tools`
holds each tool's sequence of steps; `naming` and `messages` are pure.
"""
