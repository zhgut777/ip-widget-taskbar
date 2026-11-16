"""Генерирует файл версии для PyInstaller (--version-file): сведения на вкладке «Подробно»
в свойствах exe. Версия берётся из ipwidget/__init__.py — единственного места, где она задана.

Запуск: python packaging/make_version_file.py packaging/version_info.txt
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
version = re.search(r'__version__\s*=\s*"([\d.]+)"',
                    (ROOT / "ipwidget" / "__init__.py").read_text(encoding="utf-8")).group(1)
parts = (version.split(".") + ["0", "0", "0"])[:4]
tup = ", ".join(parts)
dotted = ".".join(parts)

text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'IP widget for the Windows taskbar'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', 'IP-widget'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'IP-widget.exe'),
      StringStruct('ProductName', 'IP widget'),
      StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "version_info.txt")
out.write_text(text, encoding="utf-8")
print(f"{out}: версия {dotted}")
