"""Streamlit entry point launched by ``mazpop editor``.

Streamlit requires a script path (not a module), so the CLI runs this file;
it simply hands control to the app shell.
"""

from mazpop.gui.app import run

run()
