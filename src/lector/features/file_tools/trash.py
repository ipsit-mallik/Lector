"""Moving an original to the Recycle Bin (Windows) or the Trash (Mac), the
one thing a file tool may do to an original, and only when the reader asks:
Split's "Move the original to the Recycle Bin afterwards" (docs/PRD.md §
"Post-v1: file tools" → "Rules every tool follows").

Never a permanent delete. On Windows the shell's own file operation
(`IFileOperation`, through ctypes COM: no new dependency) is asked to
recycle, and a progress sink stops it before the delete whenever the shell
says the item can't go to the Recycle Bin (a network drive, a drive without
one, a file too big for it), where the shell would otherwise delete it for
good without a word. On a Mac it is `NSFileManager`'s `trashItemAtURL:`,
through the Objective-C runtime with ctypes (no PyObjC, and no Finder
automation prompt, which `osascript` would bring).

`move_to_trash(path)` answers None when the file is in the Recycle Bin,
or one of the reasons below; `reason_sentence` says them. No PyMuPDF.
"""
import ctypes
import ctypes.util
import logging
import os
import stat
import sys

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# What the place is called, in the dialog's checkbox and its result.
TRASH_NAME = "Trash" if IS_MAC else "Recycle Bin"

IN_USE = "in_use"
READ_ONLY = "read_only"
NO_PERMISSION = "no_permission"
MISSING = "missing"
NO_BIN = "no_bin"
UNKNOWN = "unknown"
# Not the bin's answers: Split kept the original without asking it.
STOPPED = "stopped"
CHANGED = "changed"

# "… The original couldn't be moved to the Recycle Bin: <reason>"
REASONS = {
    IN_USE: "it's open in another program.",
    READ_ONLY: "it's read-only.",
    NO_PERMISSION: "Lector isn't allowed to move it.",
    MISSING: "it's no longer there.",
    NO_BIN: f"its drive has no {TRASH_NAME}.",
    UNKNOWN: "something went wrong.",
    STOPPED: "you pressed Stop first.",
    CHANGED: "it changed while Lector was splitting it.",
}


def reason_sentence(reason: str) -> str:
    return REASONS.get(reason, REASONS[UNKNOWN])


def move_to_trash(path: str) -> str | None:
    """Move `path` to the Recycle Bin (the Trash on a Mac). None when it is
    there; otherwise why not, with the file left exactly where it was."""
    if not isinstance(path, str) or not os.path.isfile(path):
        return MISSING
    try:
        if IS_WINDOWS:
            return _windows_recycle(path)
        if IS_MAC:
            return _mac_trash(path)
    except Exception:
        log.exception("Couldn't move a file to the %s", TRASH_NAME)
        return UNKNOWN
    # Lector runs on Windows and Mac only (docs/PRD.md § "Hard constraints").
    return NO_BIN


# ---------------------------------------------------------------------- #
# Windows                                                                  #
# ---------------------------------------------------------------------- #

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33

_DELETE = 0x00010000
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_FILE_ATTRIBUTE_READONLY = 0x1
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

# IFileOperation's flags: recycle, never ask or show anything, and stop at
# the first failure.
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400
_FOFX_RECYCLEONDELETE = 0x00080000
_FOFX_EARLYFAILURE = 0x00100000
OPERATION_FLAGS = (
    _FOF_SILENT | _FOF_NOCONFIRMATION | _FOF_ALLOWUNDO | _FOF_NOERRORUI | _FOFX_RECYCLEONDELETE | _FOFX_EARLYFAILURE
)
# PreDeleteItem's flag saying the item will go to the Recycle Bin; without
# it the shell is about to delete it for good.
TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x80

_S_OK = 0
_E_ABORT = ctypes.c_long(0x80004004).value
_E_NOINTERFACE = ctypes.c_long(0x80004002).value
_RPC_E_CHANGED_MODE = 0x80010106
_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_ALL = 0x17

# HRESULTs PerformOperations reports for a file another program holds, or
# one Lector may not move.
_HR_IN_USE = {0x80070020, 0x80070021, 0x80270022}
_HR_DENIED = {0x80070005, 0x80270021}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        hexes = text.strip("{}").replace("-", "")
        tail = bytes.fromhex(hexes[16:])
        return cls(int(hexes[0:8], 16), int(hexes[8:12], 16), int(hexes[12:16], 16), (ctypes.c_ubyte * 8)(*tail))


_CLSID_FILE_OPERATION = "{3ad05575-8857-4850-9277-11b85bdb8e09}"
_IID_FILE_OPERATION = "{947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8}"
_IID_SHELL_ITEM = "{43826d1e-e718-42ee-bc55-a1e261c37bfe}"
_IID_UNKNOWN = "{00000000-0000-0000-c000-000000000046}"
_IID_PROGRESS_SINK = "{04b0f1a7-9490-44bc-96e1-4296a31252e2}"

# IFileOperation's methods, by their place in its vtable.
_RELEASE = 2
_FO_ADVISE = 3
_FO_SET_OPERATION_FLAGS = 5
_FO_DELETE_ITEM = 18
_FO_PERFORM_OPERATIONS = 21
_FO_GET_ANY_OPERATIONS_ABORTED = 22


def windows_precheck(path: str) -> str | None:
    """Why the file can't be moved, asked before the shell is: missing,
    read-only, held by another program (opened without letting others
    delete it), or not Lector's to move. None when it may be."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileAttributesW.restype = ctypes.c_uint32
    kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    attributes = kernel32.GetFileAttributesW(path)
    if attributes == _INVALID_FILE_ATTRIBUTES:
        error = ctypes.get_last_error()
        return MISSING if error in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND) else NO_PERMISSION
    if attributes & _FILE_ATTRIBUTE_READONLY:
        return READ_ONLY
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateFileW(path, _DELETE, _FILE_SHARE_ALL, None, _OPEN_EXISTING, 0, None)
    if handle in (None, _INVALID_HANDLE):
        error = ctypes.get_last_error()
        if error in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION):
            return IN_USE
        if error in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            return MISSING
        return NO_PERMISSION
    kernel32.CloseHandle(handle)
    return None


def _method(obj, index: int, *argtypes):
    """A COM method of `obj` (an interface pointer), by vtable index."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj) -> None:
    if obj:
        _method(obj, _RELEASE)(obj)


class RecycleGuard:
    """An IFileOperationProgressSink written with ctypes. It refuses any
    delete the shell says won't go to the Recycle Bin, so nothing is ever
    deleted for good, and notes whether a Recycle Bin item was made."""

    def __init__(self):
        self.refused = False
        self.recycled = False
        self._iids = [bytes(_GUID.parse(_IID_UNKNOWN)), bytes(_GUID.parse(_IID_PROGRESS_SINK))]
        hr, ptr, dword = ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32
        proto = ctypes.WINFUNCTYPE

        def query_interface(this, riid, out):
            slot = ctypes.cast(out, ctypes.POINTER(ptr))
            if ctypes.string_at(riid, ctypes.sizeof(_GUID)) in self._iids:
                slot[0] = this
                return _S_OK
            slot[0] = None
            return _E_NOINTERFACE

        def counted(_this):
            return 1

        def ok(*_args):
            return _S_OK

        slots = [
            proto(hr, ptr, ptr, ptr)(query_interface),
            proto(ctypes.c_ulong, ptr)(counted),  # AddRef
            proto(ctypes.c_ulong, ptr)(counted),  # Release
            proto(hr, ptr)(ok),  # StartOperations
            proto(hr, ptr, hr)(ok),  # FinishOperations
            proto(hr, ptr, dword, ptr, ptr)(ok),  # PreRenameItem
            proto(hr, ptr, dword, ptr, ptr, hr, ptr)(ok),  # PostRenameItem
            proto(hr, ptr, dword, ptr, ptr, ptr)(ok),  # PreMoveItem
            proto(hr, ptr, dword, ptr, ptr, ptr, hr, ptr)(ok),  # PostMoveItem
            proto(hr, ptr, dword, ptr, ptr, ptr)(ok),  # PreCopyItem
            proto(hr, ptr, dword, ptr, ptr, ptr, hr, ptr)(ok),  # PostCopyItem
            proto(hr, ptr, dword, ptr)(self._pre_delete),  # PreDeleteItem
            proto(hr, ptr, dword, ptr, hr, ptr)(self._post_delete),  # PostDeleteItem
            proto(hr, ptr, dword, ptr, ptr)(ok),  # PreNewItem
            proto(hr, ptr, dword, ptr, ptr, ptr, dword, hr, ptr)(ok),  # PostNewItem
            proto(hr, ptr, ctypes.c_uint, ctypes.c_uint)(ok),  # UpdateProgress
            proto(hr, ptr)(ok),  # ResetTimer
            proto(hr, ptr)(ok),  # PauseTimer
            proto(hr, ptr)(ok),  # ResumeTimer
        ]
        # Kept on self: the shell calls these for as long as the operation runs.
        self._slots = slots
        self._vtable = (ctypes.c_void_p * len(slots))(*[ctypes.cast(s, ctypes.c_void_p).value for s in slots])
        self._object = ctypes.c_void_p(ctypes.addressof(self._vtable))

    def _pre_delete(self, _this, flags, _item):
        # An exception here would reach the shell as S_OK (ctypes answers 0
        # for a callback that raised), so anything unexpected refuses.
        try:
            recycles = bool(flags & TSF_DELETE_RECYCLE_IF_POSSIBLE)
        except Exception:
            recycles = False
        if not recycles:
            self.refused = True
            return _E_ABORT
        return _S_OK

    def _post_delete(self, _this, _flags, _item, result, created):
        self.recycled = bool(created) and result >= 0
        return _S_OK

    @property
    def pointer(self) -> ctypes.c_void_p:
        return ctypes.c_void_p(ctypes.addressof(self._object))


def _windows_recycle(path: str) -> str | None:
    problem = windows_precheck(path)
    if problem:
        return problem
    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.OleDLL("shell32")
    initialised = True
    try:
        ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    except OSError as exc:
        # Already set up differently on this thread: use it as it is.
        if ctypes.c_ulong(exc.winerror or 0).value != _RPC_E_CHANGED_MODE:
            raise
        initialised = False
    operation = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(
            ctypes.byref(_GUID.parse(_CLSID_FILE_OPERATION)), None, _CLSCTX_ALL,
            ctypes.byref(_GUID.parse(_IID_FILE_OPERATION)), ctypes.byref(operation),
        )
        shell32.SHCreateItemFromParsingName(
            ctypes.c_wchar_p(os.path.abspath(path)), None,
            ctypes.byref(_GUID.parse(_IID_SHELL_ITEM)), ctypes.byref(item),
        )
        guard = RecycleGuard()
        cookie = ctypes.c_uint32()
        # Each of these must succeed before anything is done: without the
        # guard attached, a file the shell can't recycle would be deleted for
        # good.
        set_up = (
            _method(operation, _FO_ADVISE, ctypes.c_void_p, ctypes.c_void_p)(operation, guard.pointer, ctypes.byref(cookie)),
            _method(operation, _FO_SET_OPERATION_FLAGS, ctypes.c_uint32)(operation, OPERATION_FLAGS),
            _method(operation, _FO_DELETE_ITEM, ctypes.c_void_p, ctypes.c_void_p)(operation, item, None),
        )
        if any(hr < 0 for hr in set_up):
            log.warning("Couldn't set up the Recycle Bin move (0x%08x)", ctypes.c_ulong(min(set_up)).value)
            return UNKNOWN
        result = ctypes.c_ulong(_method(operation, _FO_PERFORM_OPERATIONS)(operation)).value
        aborted = ctypes.c_int()
        _method(operation, _FO_GET_ANY_OPERATIONS_ABORTED, ctypes.c_void_p)(operation, ctypes.byref(aborted))
        return windows_outcome(path, result, bool(aborted.value), guard.refused, guard.recycled)
    finally:
        _release(item)
        _release(operation)
        if initialised:
            ole32.CoUninitialize()


def windows_outcome(path: str, result: int, aborted: bool, refused: bool, recycled: bool) -> str | None:
    """What the shell's answer means for the reader."""
    if refused:
        return NO_BIN
    if not os.path.lexists(path):
        if not recycled:
            log.warning("The shell removed a file without reporting a Recycle Bin item")
        return None
    if result in _HR_IN_USE:
        return IN_USE
    if result in _HR_DENIED:
        return NO_PERMISSION
    log.warning("Moving a file to the Recycle Bin failed with 0x%08x (aborted: %s)", result, aborted)
    return UNKNOWN


# ---------------------------------------------------------------------- #
# Mac                                                                      #
# ---------------------------------------------------------------------- #

_NS_FILE_NO_SUCH_FILE = 4
_NS_FILE_WRITE_NO_PERMISSION = 513
_NS_FILE_WRITE_VOLUME_READ_ONLY = 642


def _mac_trash(path: str) -> str | None:
    """`[[NSFileManager defaultManager] trashItemAtURL:resultingItemURL:error:]`
    through the Objective-C runtime. Written and reviewed on Windows only:
    not yet run on a Mac."""
    flags = getattr(os.stat(path), "st_flags", 0)
    if flags & (stat.UF_IMMUTABLE | stat.SF_IMMUTABLE) or not os.access(path, os.W_OK):
        return READ_ONLY
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = objc.objc_msgSend

    def call(receiver, selector: bytes, restype, *args):
        # Each call gets its exact prototype (objc_msgSend isn't variadic on arm64).
        send.restype = restype
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [type(a) for a in args]
        return send(receiver, objc.sel_registerName(selector), *args)

    def cls(name: bytes):
        return objc.objc_getClass(name)

    pool = call(call(cls(b"NSAutoreleasePool"), b"alloc", ctypes.c_void_p), b"init", ctypes.c_void_p)
    try:
        text = call(cls(b"NSString"), b"stringWithUTF8String:", ctypes.c_void_p,
                    ctypes.c_char_p(os.path.abspath(path).encode("utf-8")))
        url = call(cls(b"NSURL"), b"fileURLWithPath:", ctypes.c_void_p, ctypes.c_void_p(text))
        manager = call(cls(b"NSFileManager"), b"defaultManager", ctypes.c_void_p)
        error = ctypes.c_void_p()
        moved = call(
            manager, b"trashItemAtURL:resultingItemURL:error:", ctypes.c_bool,
            ctypes.c_void_p(url), ctypes.c_void_p(None), ctypes.c_void_p(ctypes.addressof(error)),
        )
        if moved:
            return None
        code = call(ctypes.c_void_p(error.value), b"code", ctypes.c_long) if error.value else 0
        if code == _NS_FILE_NO_SUCH_FILE:
            return MISSING
        if code in (_NS_FILE_WRITE_NO_PERMISSION, _NS_FILE_WRITE_VOLUME_READ_ONLY):
            return NO_PERMISSION
        log.warning("Moving a file to the Trash failed (NSError %d)", code)
        return UNKNOWN
    finally:
        call(ctypes.c_void_p(pool), b"drain", None)
