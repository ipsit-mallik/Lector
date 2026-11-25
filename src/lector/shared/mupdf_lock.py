"""One lock for Home's PyMuPDF work in the main process.

pywebview runs each bridge call on a thread of its own, and PyMuPDF must not
be used from two threads at once. Home's page list and thumbnails
(`features/home/recent.py`) and a tool dialog's quick look at a PDF
(`features/file_tools/quick_look.py`) are asked for together by the Tools
page's "choose a PDF" list, so they take turns on this one lock rather than
each holding a lock of its own.
"""
import threading

HOME_PYMUPDF_LOCK = threading.Lock()
