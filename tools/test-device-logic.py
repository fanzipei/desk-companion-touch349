"""Compile and run pure touch/news state tests; never accesses hardware."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
if os.name=='nt' and Path('C:/msys64/ucrt64/bin/gcc.exe').is_file():
    os.environ['PATH']='C:/msys64/ucrt64/bin;'+os.environ['PATH']
compiler=shutil.which('gcc')
if not compiler:raise SystemExit('Install GCC (MSYS2 UCRT64 on Windows).')
with tempfile.TemporaryDirectory(prefix='touch349-logic-') as folder:
    for source in sorted((root/'tests').glob('*_test.c')):
        output=Path(folder)/(source.stem+('.exe' if os.name=='nt' else ''))
        subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror',str(source),'-o',str(output)],check=True)
        subprocess.run([str(output)],check=True)
