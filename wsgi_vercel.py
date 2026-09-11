"""Entry point for Vercel's Python/WSGI framework detection.

See config.vercel.yaml for the important caveat: this deployment's storage
lives in /tmp, which is NOT persistent across Vercel's serverless instances
- expect the ledger and workspace to reset unpredictably. It's here so you
can see the app run on a real public URL quickly; for storage that actually
persists, see the PythonAnywhere path in README.md instead.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from agentbridge.config import load_config
from agentbridge.web.app import create_app

config = load_config(PROJECT_DIR / "config.vercel.yaml")
application = create_app(config)
