# Native observer: supported Windows link formats

The observer is a read-only completion gate, not a shortcut resolver. It never
calls `Resolve`, launches the target, or changes a registry value or link.

Compatibility fixes cover the isolated Windows 10 / ACP 936 / Inno 6.7.3 shapes:

- `RegQueryInfoKeyW` uses a bounded 256-WCHAR class buffer. A nonzero status,
  inconsistent returned length, or class exceeding that supported capacity blocks
  observation. The entire tree still compares class, timestamps and value counts
  before and after enumeration. In particular status 122 is not an empty class.
- ANSI LinkInfo (header 28) is decoded with the actual `GetACP` and strict
  `MultiByteToWideChar`, never a guessed locale or lossy Python codec. The ACP is
  rechecked and included in the captured shortcut interpretation. Unicode
  StringData does not imply Unicode LinkInfo. Header sizes 29–35 are rejected.
- KnownFolderDataBlock must be exactly 28 bytes, unique, and name UsersFiles at
  the exact first-child PIDL offset 58. Its UsersFiles root is compared with the
  current user's authoritative `SHGetKnownFolderIDList` result. Only the three
  BEEF0026 FILETIMEs are excluded from that identity comparison.
- The supported UsersFiles branch is deliberately narrow: the known 130-byte
  CFSF AppData delegate shape, with its two provider GUIDs, names, lengths and
  extension headers fixed. Only dates and the eight-byte MFT reference are
  excluded from comparison. A trusted current-user AppData delegate must match
  every other byte, including unknown version fields and name offsets. Remaining items must have supported filesystem types. No arbitrary
  `0x74` item or unknown namespace GUID is accepted.
- Before accepting the full target, bounded prefix conversions must agree with
  the current Profile, Profile/AppData and LocalAppData paths. The complete PIDL,
  local LinkInfo and installer target must also agree. The root identity and
  folder mappings are bound into completion evidence and rechecked on replay.
- The previous local MyComputer/drive/filesystem branch remains supported.
  Tracker blocks must have the standard size/version; duplicate ExtraData blocks
  and unknown target-redirection blocks fail closed. PropertyStore bytes remain
  captured in full, as before.

Unsupported shapes (including other KnownFolder IDs, other delegate layouts,
UsersFiles links outside the supported AppData/Local branch, or longer registry
classes) remain blocked rather than being silently treated as completion.
Existing ownership, hashes, physical identities, no-reparse checks and registry
view selection are unchanged.

Tests use synthetic paths, zeroed timestamps/file references and zeroed tracker
identities. Offline passing tests do not establish native installation success;
the isolated Windows installation must be re-observed against the final commit.

References:
- [MS-SHLLINK KnownFolderDataBlock](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-shllink/5c7410e4-ec19-4ec5-8fff-cf4ccc46c5b6)
- [RegQueryInfoKeyW](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regqueryinfokeyw)
- [MultiByteToWideChar](https://learn.microsoft.com/en-us/windows/win32/api/stringapiset/nf-stringapiset-multibytetowidechar)
- [Known folder identifiers](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)
- [libfwsi's documented shell-item layouts](https://github.com/libyal/libfwsi/blob/main/documentation/Windows%20Shell%20Item%20format.asciidoc)

The authoritative AppData comparison uses only the fixed UsersFiles CLSID plus
`AppData` in `SHParseDisplayName`, after thread COM initialization. It never feeds
untrusted link text to that API. COM initialization is balanced on success and
respects an existing apartment. Both the trusted 190-byte shape and every
non-metadata byte must agree; API failures or changed layouts block completion.
The BEEF0026 normalization covers root bytes 32–55 only (the three FILETIMEs),
leaving its preceding control DWORD and extension-offset trailer intact.
