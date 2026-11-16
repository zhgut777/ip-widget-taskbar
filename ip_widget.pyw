# -*- coding: utf-8 -*-
"""Запуск без консоли: pythonw ip_widget.pyw (то же, что python -m ipwidget)."""
import sys

from ipwidget.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
