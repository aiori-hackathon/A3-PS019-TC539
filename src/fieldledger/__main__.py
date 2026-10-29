"""Entry point for ``python -m fieldledger``."""

import sys

from .cli import main

sys.exit(main())
