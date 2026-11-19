"""The in-app Open and Save-As screens' file browser (Milestone 8.7;
docs/ARCHITECTURE.md § "Bridge layout")."""
from lector.features.dialogs import browser as dialog_browser


class DialogsController:
    def browse_directory(self, path: str | None = None, kind: str = dialog_browser.PDF_FILTER) -> dict:
        """Lists a directory for the in-app Open/Save-As screens (Milestone
        8.7), replacing the native `create_file_dialog()` the router could
        not see. `path=None` starts at `dialog_browser.default_open_dir()` —
        the Open dialog's own default; the Save-As dialog always passes an
        explicit starting path from `get_save_dialog_start()` instead, since
        its default is the current document's folder, not the most recent
        file's. `kind` "convert" lists the images and e-books Convert to PDF
        takes instead of PDFs; "insert" (Organise's Insert…) lists both;
        "folder" (the file tools' folder picker) lists folders only."""
        folder = path or dialog_browser.default_open_dir()
        if kind in (dialog_browser.CONVERT_FILTER, dialog_browser.INSERT_FILTER, dialog_browser.FOLDER_FILTER):
            return dialog_browser.list_directory(folder, kind)
        return dialog_browser.list_directory(folder)

    def list_drives(self) -> dict:
        """Top-level nodes of the Open/Save-As directory tree — see
        `dialog_browser.list_drives()` for the platform-specific listing
        logic."""
        return dialog_browser.list_drives()

    def list_quick_access(self) -> dict:
        """Home/Desktop/Documents/Downloads for the Open/Save-As dialogs'
        Quick Access chips — see `dialog_browser.list_quick_access()`."""
        return dialog_browser.list_quick_access()
