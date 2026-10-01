import sys
import subprocess
from pathlib import Path

PASTA = Path(__file__).resolve().parent
PYTHON = PASTA / ".venv" / "Scripts" / "python.exe"

if not PYTHON.exists():
    PYTHON = Path(sys.executable)

processo_voz = subprocess.Popen(
    [str(PYTHON), "main.py"],
    cwd=PASTA
)

try:
    subprocess.run(
        [str(PYTHON), "jarvis_tela.py"],
        cwd=PASTA
    )
finally:
    if processo_voz.poll() is None:
        processo_voz.terminate()