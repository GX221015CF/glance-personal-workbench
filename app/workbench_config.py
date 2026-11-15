"""Storage boundary for the standalone portfolio copy."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('WORKBENCH_DATA_DIR', PROJECT / '.local')).resolve()
OFFLINE = os.environ.get('WORKBENCH_OFFLINE') == '1'
