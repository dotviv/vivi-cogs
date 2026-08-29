"""Test suite for vivi-cogs.

Run with: python -m unittest discover -s tests -t .
"""

import logging

# Several tests deliberately drive failure paths that log warnings -- an
# unregistered core casetype, an unwritable modlog channel. Keep the output
# readable; assertions, not log lines, are what report a problem here.
logging.getLogger("red.vivi-cogs").setLevel(logging.CRITICAL)
