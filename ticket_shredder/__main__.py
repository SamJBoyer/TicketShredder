from __future__ import annotations

from .gui import TicketShredderApp


def main() -> None:
    app = TicketShredderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
