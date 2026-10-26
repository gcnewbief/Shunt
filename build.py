"""Build dist/Shunt.exe — single file, windowed, UAC-admin manifest.

    py build.py
"""

import subprocess
import sys

subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--uac-admin",
    "--name", "Shunt",
    "--collect-all", "customtkinter",
    "shunt_app.py",
], check=True)
