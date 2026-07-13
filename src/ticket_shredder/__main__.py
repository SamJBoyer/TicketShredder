from __future__ import annotations

from dotenv import load_dotenv

from .gui import TicketShredderApp


def main() -> None:
    load_dotenv()
    app = TicketShredderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
