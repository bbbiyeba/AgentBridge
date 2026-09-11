"""Entry point for hosting AgentBridge on PythonAnywhere.

PythonAnywhere's "Manual configuration" web app type expects a WSGI file
exposing a module-level `application` object. This module builds one from
config.deploy.yaml (the public-deployment config: no Ollama agent, and
require_client_keys on so visitors bring their own API keys).

Don't run this file directly - see the "Deploying to PythonAnywhere"
section in README.md for how it's wired into PythonAnywhere's web app
config (you paste a version of this, with your own path, into the WSGI
file PythonAnywhere generates for you).
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from agentbridge.config import load_config
from agentbridge.web.app import create_app

config = load_config(PROJECT_DIR / "config.deploy.yaml")
application = create_app(config)
