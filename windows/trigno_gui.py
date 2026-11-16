"""Entry point for the PyInstaller build (Trigno.exe).

Before starting the app it imports the native libraries (numpy, Qt, LSL). If one of them fails to
load, it explains which file is missing from the Trigno folder instead of showing a raw numpy
traceback, since an incomplete copy of dist\\Trigno or an antivirus quarantine are the usual causes.

    Trigno.exe --selftest [report.txt]   import everything, write the result, exit 0 (ok) or 1
"""
import glob
import os
import platform
import sys
import traceback

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Files that must sit next to Trigno.exe (glob patterns relative to the app folder).
REQUIRED = [
    ("python%d%d.dll" % sys.version_info[:2], "Python"),
    ("vcruntime140.dll", "Visual C++ runtime"),
    ("libopenblas*.dll", "numpy's OpenBLAS library"),
    (os.path.join("numpy", "core", "_multiarray_umath*.pyd"), "numpy"),
    (os.path.join("PyQt5", "Qt5", "bin", "Qt5Core.dll"), "Qt"),
    (os.path.join("pylsl", "lib", "*lsl*.dll"), "Lab Streaming Layer"),
]
VC_REDIST = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def missing_files():
    if not getattr(sys, "frozen", False):  # running from source: nothing is bundled next to this file
        return []
    return [(pattern, what) for pattern, what in REQUIRED
            if not glob.glob(os.path.join(APP_DIR, pattern))]


def diagnose(exc):
    """Human-readable explanation of an import failure, in English and Japanese."""
    lines = ["Trigno could not start because a library failed to load.",
             "Trigno を起動できませんでした（ライブラリの読み込みに失敗）。", ""]
    missing = missing_files()
    if missing:
        lines.append("These files are missing from the Trigno folder (%s):" % APP_DIR)
        lines += ["    %s  (%s)" % (pattern, what) for pattern, what in missing]
        lines += [
            "",
            "Copy the WHOLE dist\\Trigno folder from the build PC, not just Trigno.exe.",
            "If the folder was copied completely, antivirus software has probably removed the file:",
            "restore it from quarantine or add the Trigno folder to the exclusions, then copy the",
            "folder again.",
            "",
            "Trigno.exe だけでなく dist\\Trigno フォルダ全体をコピーしてください。全部コピー済みなら",
            "ウイルス対策ソフトが上記ファイルを隔離した可能性があります。",
        ]
    else:
        lines.append("All of Trigno's own files are present, so a Windows system library is missing.")
        if sys.platform == "win32" and platform.release() == "7":
            lines += ["On Windows 7, install Service Pack 1 and the Universal C Runtime update",
                      "KB2999226 (https://support.microsoft.com/kb/2999226), then restart."]
        lines += ["Installing the Microsoft Visual C++ 2015-2022 Redistributable (x64) usually fixes it:",
                  "    " + VC_REDIST,
                  "",
                  "Windows のシステムライブラリが不足しています。上記の Visual C++ 再頒布可能パッケージを",
                  "インストールしてください。"]
    lines += ["", "Technical details:", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()]
    return "\n".join(lines)


def show_error(text):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Trigno could not start", 0x10)  # MB_ICONERROR
    if sys.stderr:
        print(text, file=sys.stderr)


def import_all():
    import numpy  # noqa: F401
    import pyqtgraph.Qt  # noqa: F401
    import pylsl  # noqa: F401
    import trigno.gui  # noqa: F401


def selftest(report_path):
    try:
        import_all()
        result, code = "ok\n", 0
    except Exception as exc:  # noqa: BLE001 - any import failure is a failed self-test
        result, code = diagnose(exc) + "\n", 1
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(result)
    if sys.stdout:
        print(result, end="")
    return code


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2] if len(sys.argv) > 2 else None)
    try:
        import_all()
        from trigno.gui import main as gui_main
    except Exception as exc:  # noqa: BLE001 - ImportError or OSError from a missing DLL
        show_error(diagnose(exc))
        return 1
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
