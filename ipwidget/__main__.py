"""Точка входа: python -m ipwidget [--debug] [--version] [--preview файл.png [light]]."""

from __future__ import annotations

import sys
import traceback

from . import __version__
from .logs import log, setup


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print(__version__)
        return 0
    setup(debug="--debug" in args)

    if "--preview" in args:                     # картинки для README, окно не создаётся
        from .preview import make_preview
        index = args.index("--preview")
        out = args[index + 1] if index + 1 < len(args) else "preview.png"
        print("saved:", make_preview(out, light="light" in args, lang="en" if "en" in args else "ru"))
        return 0

    try:
        from . import system
        from .app import Widget
        system.enable_dpi_awareness()          # до создания любых окон
        Widget().run()
    except SystemExit:
        pass
    except Exception:  # noqa: BLE001
        log.critical("виджет остановлен:\n%s", traceback.format_exc())
        try:
            from . import winapi as w
            from .paths import LOG_PATH
            w.user32.MessageBoxW(None, f"Не удалось запустить виджет.\n\nПодробности:\n{LOG_PATH}",
                                 "IP-виджет", 0x10)
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
