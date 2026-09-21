"""Entry point so `python -m csync ...` works."""
import sys

from .cli import main

sys.exit(main())
