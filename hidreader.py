"""Minimal Windows HID reader for the 3Dconnexion SpaceBall 5000 USB (no external packages).

Uses only ctypes + Windows APIs (setupapi.dll, hid.dll, kernel32.dll), so it runs
inside Fusion's bundled Python without installing anything.
"""
import ctypes
import ctypes.wintypes as wt
import struct

VID = 0x046D
PIDS = (0xC621,)  # SpaceBall 5000 USB

setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
hid = ctypes.WinDLL('hid', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [('Data1', wt.DWORD), ('Data2', wt.WORD), ('Data3', wt.WORD), ('Data4', ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('InterfaceClassGuid', GUID), ('Flags', wt.DWORD), ('Reserved', ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Size', wt.ULONG), ('VendorID', wt.USHORT), ('ProductID', wt.USHORT), ('VersionNumber', wt.USHORT)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_void_p), ('InternalHigh', ctypes.c_void_p),
                ('Offset', wt.DWORD), ('OffsetHigh', wt.DWORD), ('hEvent', wt.HANDLE)]


DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_TIMEOUT = 0x102

setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.CancelIo.argtypes = [wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ResetEvent.argtypes = [wt.HANDLE]
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]


def find_device_paths():
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    paths = []
    try:
        i = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), i, ctypes.byref(ifd)):
                break
            i += 1
            size = wt.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(ifd), None, 0, ctypes.byref(size), None)
            buf = ctypes.create_string_buffer(size.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on 64-bit
            ctypes.memmove(buf, struct.pack('I', 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6), 4)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(ifd), buf, size, None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            low = path.lower()
            if 'vid_%04x' % VID in low and any('pid_%04x' % p in low for p in PIDS):
                paths.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info)
    return paths


class SpaceBall:
    """Opens the device and returns raw HID reports via read(timeout_ms)."""

    def __init__(self, path=None):
        if path is None:
            paths = find_device_paths()
            if not paths:
                raise IOError('SpaceBall nicht gefunden (VID 046D / PID C621). Eingesteckt?')
            path = paths[0]
        self.path = path
        self.handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                           None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if self.handle in (None, INVALID_HANDLE_VALUE):
            self.handle = kernel32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                               None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if self.handle in (None, INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        self.event = kernel32.CreateEventW(None, True, False, None)
        self.buf = ctypes.create_string_buffer(64)
        self.pending = False
        self.ov = OVERLAPPED()

    def read(self, timeout_ms=50):
        """Returns bytes of one report, or None on timeout."""
        n = wt.DWORD()
        if not self.pending:
            kernel32.ResetEvent(self.event)
            self.ov = OVERLAPPED()
            self.ov.hEvent = self.event
            ok = kernel32.ReadFile(self.handle, self.buf, len(self.buf), ctypes.byref(n), ctypes.byref(self.ov))
            if ok:
                return self.buf.raw[:n.value]
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise ctypes.WinError(err)
            self.pending = True
        if kernel32.WaitForSingleObject(self.event, timeout_ms) == WAIT_TIMEOUT:
            return None
        self.pending = False
        if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(self.ov), ctypes.byref(n), False):
            raise ctypes.WinError(ctypes.get_last_error())
        return self.buf.raw[:n.value]

    def close(self):
        if self.handle:
            kernel32.CancelIo(self.handle)
            kernel32.CloseHandle(self.handle)
            kernel32.CloseHandle(self.event)
            self.handle = None


if __name__ == '__main__':
    import time
    print('Gefundene Pfade:', find_device_paths())
    sb = SpaceBall()
    print('Geöffnet. Bewege die Kugel / drücke Tasten (15 s) ...')
    end = time.time() + 15
    while time.time() < end:
        r = sb.read(100)
        if r:
            print(r.hex(' '))
    sb.close()
