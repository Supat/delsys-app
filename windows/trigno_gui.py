"""Entry point for the PyInstaller build (Trigno.exe)."""
import sys

from trigno.gui import main

if __name__ == "__main__":
    sys.exit(main())
