"""Windows write/delete sandbox runner (Win32 ACL restricted token).

Trusted runner process: creates a WRITE_RESTRICTED token whose restricting
SIDs carry the workspace (and extra writable root) plus private-temp write
capabilities, lowers the token to Low integrity, grants the matching DACLs,
then spawns the wrapped argv with the runner's inherited stdio inside a
kill-on-close Job Object. The child's exit code is mirrored; any runner-side
failure prints a windows-acl-run: diagnostic and exits 127 so the caller
can tell "sandbox broken/unavailable" apart from a denied command.

The host backend invokes this script as:

    python windows_acl_native.py --workspace <dir> --mode workspace|read-only \
        --temp <private dir> [--writable <dir> ...] -- <exe> [args...]

The module is intentionally self-contained (no AstrBot imports) because it
runs as a plain script in the child interpreter.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import shutil
import struct
import subprocess
import sys

RUNNER_SIGNATURE = "windows-acl-run"
RUNNER_FAILURE_EXIT = 127

HANDLE = wt.HANDLE
DWORD = wt.DWORD
BOOL = wt.BOOL
LPVOID = ctypes.c_void_p
LPCWSTR = wt.LPCWSTR

TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_DEFAULT = 0x0080
SE_GROUP_LOGON_ID = 0xC0000000
SE_GROUP_INTEGRITY = 0x00000020
TokenGroups = 2
TokenUser = 1
TokenDefaultDacl = 6
TokenIntegrityLevel = 25
DISABLE_MAX_PRIVILEGE = 0x1
WRITE_RESTRICTED = 0x8
WinWorldSid = 1
WinLowLabelSid = 66
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
LABEL_SECURITY_INFORMATION = 0x00000010
GRANT_ACCESS = 1
DENY_ACCESS = 3
REVOKE_ACCESS = 4
TRUSTEE_IS_SID = 0
TRUSTEE_IS_UNKNOWN = 0
SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
CONTAINER_INHERIT_ACE = 0x2
SYSTEM_MANDATORY_LABEL_NO_WRITE_UP = 0x1
SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11
ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1
ACL_REVISION = 2
LPTR = 0x0040
SECURITY_MAX_SID_SIZE = 68
FILE_GENERIC_WRITE = 0x00120116
DELETE = 0x00010000
FILE_DELETE_CHILD = 0x0040
STANDARD_RIGHTS_WRITE = 0x00020000
GRANT_MASK = (FILE_GENERIC_WRITE | DELETE | FILE_DELETE_CHILD) & ~STANDARD_RIGHTS_WRITE
FILE_ALL_ACCESS = 0x1F01FF
HANDLE_FLAG_INHERIT = 0x1
STARTF_USESTDHANDLES = 0x100
STARTF_USESHOWWINDOW = 0x1
SW_HIDE = 0
STD_INPUT_HANDLE = 0xFFFFFFF6
STD_OUTPUT_HANDLE = 0xFFFFFFF5
STD_ERROR_HANDLE = 0xFFFFFFF4
CREATE_SUSPENDED = 0x4
CREATE_UNICODE_ENVIRONMENT = 0x400
INFINITE = 0xFFFFFFFF
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
ERROR_SUCCESS = 0

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)


def _bind(lib, name, restype, *argtypes):
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


GetCurrentProcess = _bind(k32, "GetCurrentProcess", HANDLE)
CloseHandle = _bind(k32, "CloseHandle", BOOL, HANDLE)
CreateJobObjectW = _bind(k32, "CreateJobObjectW", HANDLE, LPVOID, LPCWSTR)
SetInformationJobObject = _bind(
    k32, "SetInformationJobObject", BOOL, HANDLE, ctypes.c_int, LPVOID, DWORD
)
AssignProcessToJobObject = _bind(k32, "AssignProcessToJobObject", BOOL, HANDLE, HANDLE)
ResumeThread = _bind(k32, "ResumeThread", DWORD, HANDLE)
WaitForSingleObject = _bind(k32, "WaitForSingleObject", DWORD, HANDLE, DWORD)
GetExitCodeProcess = _bind(
    k32, "GetExitCodeProcess", BOOL, HANDLE, ctypes.POINTER(DWORD)
)
GetStdHandle = _bind(k32, "GetStdHandle", HANDLE, DWORD)
SetHandleInformation = _bind(k32, "SetHandleInformation", BOOL, HANDLE, DWORD, DWORD)
LocalAlloc = _bind(k32, "LocalAlloc", LPVOID, DWORD, ctypes.c_size_t)
LocalFree = _bind(k32, "LocalFree", LPVOID, LPVOID)
FormatMessageW = _bind(
    k32,
    "FormatMessageW",
    DWORD,
    DWORD,
    LPVOID,
    DWORD,
    DWORD,
    ctypes.c_void_p,
    DWORD,
    LPVOID,
)
SetConsoleCtrlHandler = _bind(k32, "SetConsoleCtrlHandler", BOOL, LPVOID, BOOL)

OpenProcessToken = _bind(
    adv, "OpenProcessToken", BOOL, HANDLE, DWORD, ctypes.POINTER(HANDLE)
)
GetTokenInformation = _bind(
    adv,
    "GetTokenInformation",
    BOOL,
    HANDLE,
    ctypes.c_int,
    LPVOID,
    DWORD,
    ctypes.POINTER(DWORD),
)
SetTokenInformation = _bind(
    adv, "SetTokenInformation", BOOL, HANDLE, ctypes.c_int, LPVOID, DWORD
)
CreateWellKnownSid = _bind(
    adv, "CreateWellKnownSid", BOOL, ctypes.c_int, LPVOID, LPVOID, ctypes.POINTER(DWORD)
)
CopySid = _bind(adv, "CopySid", BOOL, DWORD, LPVOID, LPVOID)
GetLengthSid = _bind(adv, "GetLengthSid", DWORD, LPVOID)
ConvertStringSidToSidW = _bind(
    adv, "ConvertStringSidToSidW", BOOL, LPCWSTR, ctypes.POINTER(LPVOID)
)
ConvertSidToStringSidW = _bind(
    adv, "ConvertSidToStringSidW", BOOL, LPVOID, ctypes.POINTER(LPCWSTR)
)
CreateRestrictedToken = _bind(
    adv,
    "CreateRestrictedToken",
    BOOL,
    HANDLE,
    DWORD,
    DWORD,
    LPVOID,
    DWORD,
    LPVOID,
    DWORD,
    LPVOID,
    ctypes.POINTER(HANDLE),
)
CreateProcessAsUserW = _bind(
    adv,
    "CreateProcessAsUserW",
    BOOL,
    HANDLE,
    LPCWSTR,
    wt.LPWSTR,
    LPVOID,
    LPVOID,
    BOOL,
    DWORD,
    LPVOID,
    LPCWSTR,
    LPVOID,
    LPVOID,
)
GetNamedSecurityInfoW = _bind(
    adv,
    "GetNamedSecurityInfoW",
    DWORD,
    LPCWSTR,
    ctypes.c_int,
    DWORD,
    ctypes.POINTER(LPVOID),
    ctypes.POINTER(LPVOID),
    ctypes.POINTER(LPVOID),
    ctypes.POINTER(LPVOID),
    ctypes.POINTER(LPVOID),
)
SetNamedSecurityInfoW = _bind(
    adv,
    "SetNamedSecurityInfoW",
    DWORD,
    LPCWSTR,
    ctypes.c_int,
    DWORD,
    LPVOID,
    LPVOID,
    LPVOID,
    LPVOID,
)
SetEntriesInAclW = _bind(
    adv,
    "SetEntriesInAclW",
    DWORD,
    ctypes.c_ulong,
    LPVOID,
    LPVOID,
    ctypes.POINTER(LPVOID),
)
InitializeAcl = _bind(adv, "InitializeAcl", BOOL, LPVOID, DWORD, DWORD)
AddMandatoryAce = _bind(
    adv, "AddMandatoryAce", BOOL, LPVOID, DWORD, DWORD, DWORD, LPVOID
)


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", LPVOID), ("Attributes", DWORD)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", LPVOID),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


class TRUSTEE_W(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", LPVOID),
        ("MultipleTrusteeOperation", DWORD),
        ("TrusteeForm", DWORD),
        ("TrusteeType", DWORD),
        ("ptstrName", LPVOID),
    ]


class EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [
        ("grfAccessPermissions", DWORD),
        ("grfAccessMode", DWORD),
        ("grfInheritance", DWORD),
        ("Trustee", TRUSTEE_W),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class Win32Error(OSError):
    def __init__(self, api: str, code: int, detail: str = ""):
        self.api = api
        self.code = code
        super().__init__(code, f"{api} failed: {win32_text(code)} {detail}".strip())


def win32_text(code: int) -> str:
    if code == 0:
        return "success"
    buf = ctypes.create_unicode_buffer(512)
    n = FormatMessageW(0x1000, None, code, 0, buf, 512, None)
    text = buf.value.strip() if n else "?"
    return f"Win32 {code} ({text})"


def _check(ok, api: str, detail: str = "") -> None:
    if not ok:
        raise Win32Error(api, ctypes.get_last_error(), detail)


def workspace_write_sid(path: str) -> str:
    digest = hashlib.sha256(os.path.abspath(path).encode("utf-8")).digest()
    first = struct.unpack_from("<I", digest, 0)[0] % (2**30 - 1) + 1
    second = struct.unpack_from("<I", digest, 4)[0] % (2**30 - 1) + 1
    return f"S-1-4-{first}-{second}"


def temp_write_sid(path: str) -> str:
    digest = hashlib.sha256(b"temp\0" + os.path.abspath(path).encode("utf-8")).digest()
    first = struct.unpack_from("<I", digest, 0)[0] % (2**30 - 1) + 1
    second = struct.unpack_from("<I", digest, 4)[0] % (2**30 - 1) + 1
    return f"S-1-4-{first}-{second}-1"


def current_user_sid_string() -> str:
    token = HANDLE()
    _check(
        OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)),
        "OpenProcessToken",
    )
    try:
        needed = DWORD()
        GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise Win32Error(
                "GetTokenInformation", ctypes.get_last_error(), "TokenUser size"
            )
        buf = ctypes.create_string_buffer(needed.value)
        _check(
            GetTokenInformation(token, TokenUser, buf, len(buf), ctypes.byref(needed)),
            "GetTokenInformation",
            "TokenUser",
        )
        sid = struct.unpack_from("<Q", buf.raw, 0)[0]
        text = LPCWSTR()
        _check(
            ConvertSidToStringSidW(sid, ctypes.byref(text)), "ConvertSidToStringSidW"
        )
        value = text.value
        LocalFree(ctypes.cast(text, LPVOID))
        return value
    finally:
        CloseHandle(token)


def build_environment_block(env: dict) -> ctypes.Array:
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    data = "\0".join(f"{key}={value}" for key, value in items) + "\0\0"
    raw = data.encode("utf-16le")
    return ctypes.create_string_buffer(raw, len(raw))


class Sandbox:
    def __init__(
        self, workspace: str, mode: str, temp_dir: str | None, writable_roots: list[str]
    ):
        self.workspace = os.path.abspath(workspace)
        self.mode = mode
        self.temp_dir = os.path.abspath(temp_dir) if temp_dir else None
        self.writable_roots = [os.path.abspath(path) for path in writable_roots]
        self.current_token = HANDLE()
        self.restricted_token = HANDLE()
        self.sid_storage = []
        self.granted = []
        self.low_sid = 0
        self.world_sid = 0
        self.workspace_sids: list[int] = []
        self.temp_sid = None
        self.temp_sid_str = None

    def _well_known(self, kind: int) -> int:
        buf = ctypes.create_string_buffer(SECURITY_MAX_SID_SIZE)
        size = DWORD(SECURITY_MAX_SID_SIZE)
        _check(
            CreateWellKnownSid(kind, None, buf, ctypes.byref(size)),
            "CreateWellKnownSid",
            str(kind),
        )
        self.sid_storage.append(buf)
        return ctypes.addressof(buf)

    def _parse_sid(self, value: str) -> int:
        out = LPVOID()
        _check(
            ConvertStringSidToSidW(value, ctypes.byref(out)),
            "ConvertStringSidToSidW",
            value,
        )
        self.sid_storage.append(out)
        return out.value

    def _logon_sid(self) -> int:
        needed = DWORD()
        GetTokenInformation(
            self.current_token, TokenGroups, None, 0, ctypes.byref(needed)
        )
        if not needed.value:
            raise Win32Error(
                "GetTokenInformation", ctypes.get_last_error(), "TokenGroups size"
            )
        buf = ctypes.create_string_buffer(needed.value)
        _check(
            GetTokenInformation(
                self.current_token, TokenGroups, buf, needed.value, ctypes.byref(needed)
            ),
            "GetTokenInformation",
            "TokenGroups",
        )
        count = struct.unpack_from("<I", buf.raw, 0)[0]
        for index in range(count):
            sid_ptr, attrs = struct.unpack_from("<QI", buf.raw, 8 + index * 16)
            if attrs & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID:
                length = GetLengthSid(sid_ptr)
                dst = ctypes.create_string_buffer(length)
                _check(
                    CopySid(length, dst, sid_ptr), "CopySid", f"logon SID group {index}"
                )
                self.sid_storage.append(dst)
                return ctypes.addressof(dst)
        raise RuntimeError("no logon SID found in token groups")

    @staticmethod
    def _explicit(
        sid_addr: int, access_mode: int, mask: int, inheritance: int
    ) -> EXPLICIT_ACCESS_W:
        entry = EXPLICIT_ACCESS_W()
        entry.grfAccessPermissions = mask
        entry.grfAccessMode = access_mode
        entry.grfInheritance = inheritance
        entry.Trustee.pMultipleTrustee = None
        entry.Trustee.MultipleTrusteeOperation = 0
        entry.Trustee.TrusteeForm = TRUSTEE_IS_SID
        entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN
        entry.Trustee.ptstrName = sid_addr
        return entry

    @staticmethod
    def _read_security(path: str, security_info: int):
        dacl, sacl, descriptor = LPVOID(), LPVOID(), LPVOID()
        rc = GetNamedSecurityInfoW(
            path,
            SE_FILE_OBJECT,
            security_info,
            None,
            None,
            ctypes.byref(dacl),
            ctypes.byref(sacl),
            ctypes.byref(descriptor),
        )
        if rc != ERROR_SUCCESS:
            raise Win32Error("GetNamedSecurityInfoW", rc, path)
        return dacl, sacl, descriptor

    def _acl_has_entry(self, acl: int, ace_type: int, mask: int, sid_addr: int) -> bool:
        if not acl:
            return False
        acl_size = ctypes.c_uint16.from_address(acl + 2).value
        ace_count = ctypes.c_uint16.from_address(acl + 4).value
        if acl_size < 8 or acl_size > 1_048_576:
            return False
        sid_len = GetLengthSid(sid_addr)
        sid_bytes = ctypes.string_at(sid_addr, sid_len)
        offset = 8
        for _ in range(ace_count):
            if offset + 8 > acl_size:
                return False
            current_type = ctypes.c_uint8.from_address(acl + offset).value
            ace_size = ctypes.c_uint16.from_address(acl + offset + 2).value
            if ace_size < 8 or offset + ace_size > acl_size:
                return False
            if current_type == ace_type and ace_size - 8 >= sid_len:
                current_mask = ctypes.c_uint32.from_address(acl + offset + 4).value
                if (
                    current_mask == mask
                    and ctypes.string_at(acl + offset + 8, sid_len) == sid_bytes
                ):
                    return True
            offset += ace_size
        return False

    def ensure_owner_full_control(self, path: str) -> None:
        user_sid = self._parse_sid(current_user_sid_string())
        dacl, _sacl, descriptor = self._read_security(path, DACL_SECURITY_INFORMATION)
        try:
            if self._acl_has_entry(
                dacl.value, ACCESS_ALLOWED_ACE_TYPE, FILE_ALL_ACCESS, user_sid
            ):
                return
            entry = self._explicit(
                user_sid,
                GRANT_ACCESS,
                FILE_ALL_ACCESS,
                SUB_CONTAINERS_AND_OBJECTS_INHERIT,
            )
            new_acl = LPVOID()
            rc = SetEntriesInAclW(
                1, ctypes.cast(ctypes.byref(entry), LPVOID), dacl, ctypes.byref(new_acl)
            )
            if rc != ERROR_SUCCESS:
                raise Win32Error("SetEntriesInAclW", rc, path)
            try:
                rc = SetNamedSecurityInfoW(
                    path,
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    new_acl,
                    None,
                )
                if rc != ERROR_SUCCESS:
                    raise Win32Error("SetNamedSecurityInfoW", rc, path)
            finally:
                if new_acl.value:
                    LocalFree(new_acl)
        finally:
            if descriptor.value:
                LocalFree(descriptor)

    def _build_low_label_acl(self, low_sid_addr: int) -> int:
        sid_len = GetLengthSid(low_sid_addr)
        size = 8 + 8 + sid_len
        acl = LocalAlloc(LPTR, size)
        if not acl:
            raise Win32Error("LocalAlloc", ctypes.get_last_error(), "label ACL")
        try:
            _check(InitializeAcl(acl, size, ACL_REVISION), "InitializeAcl", "label ACL")
            _check(
                AddMandatoryAce(
                    acl,
                    ACL_REVISION,
                    SUB_CONTAINERS_AND_OBJECTS_INHERIT,
                    SYSTEM_MANDATORY_LABEL_NO_WRITE_UP,
                    low_sid_addr,
                ),
                "AddMandatoryAce",
                "label ACL",
            )
        except OSError:
            LocalFree(acl)
            raise
        return acl

    def _sacl_has_low_label(self, sacl: int, low_sid_addr: int) -> bool:
        if not sacl:
            return False
        try:
            return self._acl_has_entry(
                sacl,
                SYSTEM_MANDATORY_LABEL_ACE_TYPE,
                SYSTEM_MANDATORY_LABEL_NO_WRITE_UP,
                low_sid_addr,
            )
        except Exception:
            return False

    def grant_write(self, path: str, cap_sid_addr: int) -> None:
        self.ensure_owner_full_control(path)
        dacl, sacl, descriptor = self._read_security(
            path, DACL_SECURITY_INFORMATION | LABEL_SECURITY_INFORMATION
        )
        try:
            has_grant = self._acl_has_entry(
                dacl.value, ACCESS_ALLOWED_ACE_TYPE, GRANT_MASK, cap_sid_addr
            )
            has_deny = self._acl_has_entry(
                dacl.value, ACCESS_DENIED_ACE_TYPE, FILE_DELETE_CHILD, self.world_sid
            )
            has_label = self._sacl_has_low_label(sacl.value, self.low_sid)
            if has_grant and has_deny and has_label:
                return
            entries = (EXPLICIT_ACCESS_W * 2)()
            entries[0] = self._explicit(
                self.world_sid, DENY_ACCESS, FILE_DELETE_CHILD, CONTAINER_INHERIT_ACE
            )
            entries[1] = self._explicit(
                cap_sid_addr,
                GRANT_ACCESS,
                GRANT_MASK,
                SUB_CONTAINERS_AND_OBJECTS_INHERIT,
            )
            new_acl = LPVOID()
            rc = SetEntriesInAclW(
                2, ctypes.cast(entries, LPVOID), dacl, ctypes.byref(new_acl)
            )
            if rc != ERROR_SUCCESS:
                raise Win32Error("SetEntriesInAclW", rc, path)
            label_acl = self._build_low_label_acl(self.low_sid)
            try:
                if descriptor.value:
                    LocalFree(descriptor)
                    descriptor = LPVOID()
                rc = SetNamedSecurityInfoW(
                    path,
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION | LABEL_SECURITY_INFORMATION,
                    None,
                    None,
                    new_acl,
                    label_acl,
                )
                if rc != ERROR_SUCCESS:
                    raise Win32Error("SetNamedSecurityInfoW", rc, path)
            finally:
                if new_acl.value:
                    LocalFree(new_acl)
                LocalFree(label_acl)
            self.granted.append((path, cap_sid_addr))
        finally:
            if descriptor.value:
                LocalFree(descriptor)

    def revoke_write(self, path: str, cap_sid_addr: int) -> None:
        dacl, _sacl, descriptor = self._read_security(path, DACL_SECURITY_INFORMATION)
        try:
            if not dacl.value:
                return
            entry = self._explicit(
                cap_sid_addr, REVOKE_ACCESS, 0, SUB_CONTAINERS_AND_OBJECTS_INHERIT
            )
            new_acl = LPVOID()
            rc = SetEntriesInAclW(
                1, ctypes.cast(ctypes.byref(entry), LPVOID), dacl, ctypes.byref(new_acl)
            )
            if rc != ERROR_SUCCESS:
                raise Win32Error("SetEntriesInAclW", rc, path)
            try:
                if descriptor.value:
                    LocalFree(descriptor)
                    descriptor = LPVOID()
                rc = SetNamedSecurityInfoW(
                    path,
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    new_acl,
                    None,
                )
                if rc != ERROR_SUCCESS:
                    raise Win32Error("SetNamedSecurityInfoW", rc, path)
            finally:
                if new_acl.value:
                    LocalFree(new_acl)
        finally:
            if descriptor.value:
                LocalFree(descriptor)

    def init(self) -> None:
        _check(
            OpenProcessToken(
                GetCurrentProcess(),
                TOKEN_QUERY
                | TOKEN_DUPLICATE
                | TOKEN_ADJUST_DEFAULT
                | TOKEN_ASSIGN_PRIMARY,
                ctypes.byref(self.current_token),
            ),
            "OpenProcessToken",
        )
        try:
            logon_sid = self._logon_sid()
            self.world_sid = self._well_known(WinWorldSid)
            self.low_sid = self._well_known(WinLowLabelSid)

            write_sids: list[int] = []
            if self.mode == "workspace":
                workspace_sid = self._parse_sid(workspace_write_sid(self.workspace))
                self.workspace_sids.append(workspace_sid)
                write_sids.append(workspace_sid)
                self.grant_write(self.workspace, workspace_sid)
                for root in self.writable_roots:
                    if not os.path.isdir(root):
                        raise RuntimeError(
                            f"writable root is not an existing directory: {root}"
                        )
                    root_sid = self._parse_sid(workspace_write_sid(root))
                    self.workspace_sids.append(root_sid)
                    write_sids.append(root_sid)
                    self.grant_write(root, root_sid)
                if self.temp_dir is not None:
                    self.temp_sid_str = temp_write_sid(self.temp_dir)
                    self.temp_sid = self._parse_sid(self.temp_sid_str)
                    write_sids.append(self.temp_sid)
                    self.grant_write(self.temp_dir, self.temp_sid)

            restricting = [logon_sid, self.world_sid, *write_sids]
            arr = (SID_AND_ATTRIBUTES * len(restricting))()
            for index, sid in enumerate(restricting):
                arr[index].Sid = sid
                arr[index].Attributes = 0
            _check(
                CreateRestrictedToken(
                    self.current_token,
                    DISABLE_MAX_PRIVILEGE | WRITE_RESTRICTED,
                    0,
                    None,
                    0,
                    None,
                    len(restricting),
                    ctypes.cast(arr, LPVOID),
                    ctypes.byref(self.restricted_token),
                ),
                "CreateRestrictedToken",
                f"restricting SIDs: {len(restricting)}",
            )
            self._set_low_integrity()
            default_sid = self.temp_sid or (
                self.workspace_sids[0] if self.workspace_sids else self.world_sid
            )
            self._grant_default_dacl(default_sid)
        except OSError:
            self.close()
            raise

    def _set_low_integrity(self) -> None:
        size = 16 + GetLengthSid(self.low_sid)
        buf = ctypes.create_string_buffer(size)
        struct.pack_into("<Q", buf, 0, self.low_sid)
        struct.pack_into("<I", buf, 8, SE_GROUP_INTEGRITY)
        _check(
            SetTokenInformation(self.restricted_token, TokenIntegrityLevel, buf, size),
            "SetTokenInformation",
            "TokenIntegrityLevel",
        )

    def _grant_default_dacl(self, sid_addr: int) -> None:
        needed = DWORD()
        GetTokenInformation(
            self.restricted_token, TokenDefaultDacl, None, 0, ctypes.byref(needed)
        )
        if not needed.value:
            raise Win32Error(
                "GetTokenInformation", ctypes.get_last_error(), "TokenDefaultDacl size"
            )
        buf = ctypes.create_string_buffer(max(needed.value, 8))
        _check(
            GetTokenInformation(
                self.restricted_token,
                TokenDefaultDacl,
                buf,
                len(buf),
                ctypes.byref(needed),
            ),
            "GetTokenInformation",
            "TokenDefaultDacl",
        )
        old_dacl = struct.unpack_from("<Q", buf.raw, 0)[0]
        entry = self._explicit(
            sid_addr, GRANT_ACCESS, FILE_ALL_ACCESS, SUB_CONTAINERS_AND_OBJECTS_INHERIT
        )
        new_dacl = LPVOID()
        rc = SetEntriesInAclW(
            1,
            ctypes.cast(ctypes.byref(entry), LPVOID),
            old_dacl,
            ctypes.byref(new_dacl),
        )
        if rc != ERROR_SUCCESS:
            raise Win32Error("SetEntriesInAclW", rc, "default DACL")
        try:
            info = ctypes.create_string_buffer(8)
            struct.pack_into("<Q", info, 0, new_dacl.value)
            _check(
                SetTokenInformation(self.restricted_token, TokenDefaultDacl, info, 8),
                "SetTokenInformation",
                "TokenDefaultDacl",
            )
        finally:
            LocalFree(new_dacl)

    def _create_job(
        self, max_processes: int | None, max_memory_bytes: int | None
    ) -> int:
        job = CreateJobObjectW(None, None)
        if not job:
            raise Win32Error("CreateJobObjectW", ctypes.get_last_error())
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if max_processes and max_processes > 0:
            info.BasicLimitInformation.ActiveProcessLimit = max_processes
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        if max_memory_bytes and max_memory_bytes > 0:
            info.ProcessMemoryLimit = max_memory_bytes
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
        if not SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            code = ctypes.get_last_error()
            CloseHandle(job)
            raise Win32Error("SetInformationJobObject", code)
        return job

    def spawn_inherited(
        self,
        exe: str,
        args: list[str],
        max_processes: int | None,
        max_memory_bytes: int | None,
    ) -> int:
        job = self._create_job(max_processes, max_memory_bytes)
        process = HANDLE()
        thread = HANDLE()
        try:
            stdin_h = GetStdHandle(STD_INPUT_HANDLE)
            stdout_h = GetStdHandle(STD_OUTPUT_HANDLE)
            stderr_h = GetStdHandle(STD_ERROR_HANDLE)
            inherited = [h for h in (stdin_h, stdout_h, stderr_h) if h]
            for handle in inherited:
                _check(
                    SetHandleInformation(
                        handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT
                    ),
                    "SetHandleInformation",
                )
            try:
                si = STARTUPINFOW()
                si.cb = ctypes.sizeof(STARTUPINFOW)
                si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
                si.wShowWindow = SW_HIDE
                si.hStdInput = stdin_h
                si.hStdOutput = stdout_h
                si.hStdError = stderr_h
                pi = PROCESS_INFORMATION()
                command_line = subprocess.list2cmdline([exe, *args])
                command_buf = ctypes.create_unicode_buffer(command_line)
                child_env = dict(os.environ)
                if self.temp_dir is not None:
                    child_env["TEMP"] = self.temp_dir
                    child_env["TMP"] = self.temp_dir
                env_block = build_environment_block(child_env)
                ok = CreateProcessAsUserW(
                    self.restricted_token,
                    exe,
                    command_buf,
                    None,
                    None,
                    True,
                    CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                    ctypes.cast(env_block, LPVOID),
                    self.workspace,
                    ctypes.byref(si),
                    ctypes.byref(pi),
                )
                if not ok:
                    raise Win32Error(
                        "CreateProcessAsUserW", ctypes.get_last_error(), f"exe={exe}"
                    )
                process, thread = pi.hProcess, pi.hThread
                _check(
                    AssignProcessToJobObject(job, process), "AssignProcessToJobObject"
                )
                _check(ResumeThread(thread) != 0xFFFFFFFF, "ResumeThread")
                CloseHandle(thread)
                thread = HANDLE()
            finally:
                for handle in inherited:
                    try:
                        SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0)
                    except OSError:
                        pass
            WaitForSingleObject(process, INFINITE)
            code = DWORD()
            _check(
                GetExitCodeProcess(process, ctypes.byref(code)), "GetExitCodeProcess"
            )
            CloseHandle(process)
            process = HANDLE()
            CloseHandle(job)
            job = None
            return int(code.value)
        except OSError:
            if process:
                CloseHandle(process)
            if thread:
                CloseHandle(thread)
            if job:
                CloseHandle(job)
            raise

    def close(self) -> None:
        if self.restricted_token:
            CloseHandle(self.restricted_token)
            self.restricted_token = HANDLE()
        if self.current_token:
            CloseHandle(self.current_token)
            self.current_token = HANDLE()
        for item in self.sid_storage:
            try:
                if isinstance(item, ctypes.c_void_p) and item.value:
                    LocalFree(item)
            except Exception:
                pass
        self.sid_storage.clear()

    def revoke_temp_grants(self) -> None:
        if self.temp_sid is not None and self.temp_dir is not None:
            try:
                self.revoke_write(self.temp_dir, self.temp_sid)
            except Exception as exc:  # cleanup must not mask the child's exit code
                sys.stderr.write(f"{RUNNER_SIGNATURE}: cleanup: {exc}\n")


def _fail(detail: str) -> int:
    sys.stderr.write(f"{RUNNER_SIGNATURE}: {detail}\n")
    return RUNNER_FAILURE_EXIT


def _parse_args(argv: list[str]):
    workspace = None
    mode = None
    temp = None
    writable: list[str] = []
    max_processes = None
    max_memory = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        index += 1
        if index >= len(argv):
            raise ValueError(f"missing value after {token}")
        value = argv[index]
        index += 1
        if token == "--workspace":
            workspace = value
        elif token == "--mode":
            mode = value
        elif token == "--temp":
            temp = value
        elif token == "--writable":
            writable.append(value)
        elif token == "--max-processes":
            max_processes = int(value)
        elif token == "--max-memory-bytes":
            max_memory = int(value)
        else:
            raise ValueError(f"unknown argument: {token}")
    command = argv[index:]
    if not workspace:
        raise ValueError("missing --workspace")
    if not os.path.isdir(workspace):
        raise ValueError(f"--workspace is not an existing directory: {workspace}")
    if mode not in ("workspace", "read-only"):
        raise ValueError(f"unknown mode: {mode}")
    if not temp or not os.path.isdir(temp):
        raise ValueError(f"--temp is not an existing directory: {temp}")
    if not command:
        raise ValueError("missing command after --")
    temp_abs = os.path.abspath(temp)
    workspace_abs = os.path.abspath(workspace)
    if temp_abs == workspace_abs or temp_abs.startswith(workspace_abs + os.sep):
        raise ValueError("--temp must be outside --workspace")
    for root in writable:
        if not os.path.isdir(root):
            raise ValueError(f"--writable is not an existing directory: {root}")
    return (
        workspace_abs,
        mode,
        temp_abs,
        [os.path.abspath(root) for root in writable],
        max_processes,
        max_memory,
        command,
    )


def main(argv: list[str]) -> int:
    try:
        workspace, mode, temp, writable, max_processes, max_memory, command = (
            _parse_args(argv)
        )
    except Exception as exc:
        return _fail(str(exc))
    exe = command[0]
    if not os.path.isabs(exe):
        resolved = shutil.which(exe)
        if resolved:
            exe = resolved
    sandbox = Sandbox(workspace, mode, temp, writable)
    try:
        SetConsoleCtrlHandler(None, 1)
        sandbox.init()
        exit_code = sandbox.spawn_inherited(exe, command[1:], max_processes, max_memory)
        sandbox.revoke_temp_grants()
        return exit_code
    except Exception as exc:
        return _fail(str(exc))
    finally:
        sandbox.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
