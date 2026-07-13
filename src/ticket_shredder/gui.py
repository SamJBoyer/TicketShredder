from __future__ import annotations

import queue
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from tkinter import messagebox, ttk
from typing import cast

from .controller import TicketController
from .model import Repository, Ticket, TicketStatus

STATUS_COLORS = {
    TicketStatus.QUEUED: "#9ca3af",
    TicketStatus.WORKING: "#4285f4",
    TicketStatus.REVIEW: "#34a853",
    TicketStatus.MERGED: "#18743a",
    TicketStatus.FAILED: "#ea4335",
}


class TicketCard(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        ticket: Ticket,
        *,
        on_open: Callable[[Ticket], None],
        on_merge: Callable[[Ticket], None],
        on_dump: Callable[[Ticket], None],
        on_close: Callable[[Ticket], None],
    ) -> None:
        super().__init__(parent, padding=12, relief="solid", borderwidth=1)
        self.ticket = ticket
        self.on_open = on_open

        self.columnconfigure(1, weight=1)
        self.lamp = tk.Canvas(self, width=18, height=18, highlightthickness=0)
        self.lamp.grid(row=0, column=0, sticky="n", padx=(0, 9), pady=2)
        self.title = ttk.Label(
            self,
            text=f"#{ticket.number}  {ticket.title}",
            font=("Segoe UI", 11, "bold"),
            cursor="hand2",
        )
        self.title.grid(row=0, column=1, sticky="ew")

        self.status = ttk.Label(self)
        self.status.grid(row=1, column=1, sticky="w", pady=(3, 0))
        self.detail = ttk.Label(self, wraplength=700, foreground="#555555")
        self.detail.grid(row=2, column=1, sticky="ew", pady=(4, 8))
        for widget in (self, self.lamp, self.title, self.status, self.detail):
            widget.bind("<Button-1>", lambda _event: self.on_open(ticket))
            widget.configure(cursor="hand2")

        actions = ttk.Frame(self)
        actions.grid(row=3, column=1, sticky="e")
        self.merge_button = ttk.Button(
            actions, text="Merge", command=lambda: on_merge(ticket)
        )
        self.merge_button.pack(side="left", padx=(0, 6))
        self.dump_button = ttk.Button(
            actions, text="Dump", command=lambda: on_dump(ticket)
        )
        self.dump_button.pack(side="left", padx=(0, 6))
        self.close_button = ttk.Button(
            actions, text="Close", command=lambda: on_close(ticket)
        )
        self.close_button.pack(side="left")
        self.refresh()

    def refresh(self) -> None:
        self.lamp.delete("all")
        self.lamp.create_oval(
            2,
            2,
            16,
            16,
            fill=STATUS_COLORS[self.ticket.status],
            outline="",
        )
        self.status.configure(text=self.ticket.status.value)
        self.detail.configure(text=self.ticket.detail)
        self.merge_button.configure(
            state="normal" if self.ticket.status == TicketStatus.REVIEW else "disabled"
        )
        can_dump = self.ticket.worktree and self.ticket.status != TicketStatus.WORKING
        self.dump_button.configure(state="normal" if can_dump else "disabled")
        self.close_button.configure(
            state=(
                "disabled"
                if self.ticket.status == TicketStatus.WORKING
                else "normal"
            )
        )

    def set_busy(self) -> None:
        self.merge_button.configure(state="disabled")
        self.dump_button.configure(state="disabled")
        self.close_button.configure(state="disabled")


class TicketShredderApp(tk.Tk):
    def __init__(self, workspace_root: Path | None = None) -> None:
        super().__init__()
        self.title("Ticket Shredder")
        self.geometry("900x650")
        self.minsize(680, 420)
        self.controller = TicketController(
            workspace_root or Path.home() / ".ticket-shredder"
        )
        self.cards: dict[int, TicketCard] = {}
        self.repository: Repository | None = None
        self.events: queue.Queue[tuple[object, ...]] = queue.Queue()
        self._build()
        self.after(50, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Git URL").pack(side="left", padx=(0, 8))
        self.remote = tk.StringVar()
        self.entry = ttk.Entry(top, textvariable=self.remote)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self.connect())
        self.repo_lamp = tk.Canvas(top, width=22, height=22, highlightthickness=0)
        self.repo_lamp.pack(side="left", padx=9)
        self._set_repo_lamp("#9ca3af")
        self.connect_button = ttk.Button(top, text="Connect", command=self.connect)
        self.connect_button.pack(side="left")
        self.remote.trace_add("write", self._remote_changed)

        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(shell, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        self.list_frame = ttk.Frame(self.canvas, padding=(12, 0, 12, 12))
        self.window = self.canvas.create_window(
            (0, 0), window=self.list_frame, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.list_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window, width=event.width),
        )
        self.empty_label = ttk.Label(
            self.list_frame,
            text="Connect a repository to load open issues labeled “auto”.",
            padding=24,
        )
        self.empty_label.pack()

    def connect(self) -> None:
        remote_url = self.remote.get().strip()
        if not remote_url:
            self._set_repo_lamp("#ea4335")
            return
        self.connect_button.configure(state="disabled")
        self.entry.configure(state="disabled")
        for card in self.cards.values():
            card.set_busy()
        self._set_repo_lamp("#9ca3af")
        future = self.controller.executor.submit(self.controller.connect, remote_url)
        future.add_done_callback(
            lambda result: self.events.put(("connected", result))
        )

    def _connected(self, future: Future[Repository]) -> None:
        self.connect_button.configure(state="normal")
        self.entry.configure(state="normal")
        try:
            repository = future.result()
        except Exception as exc:
            self._set_repo_lamp("#ea4335")
            for card in self.cards.values():
                card.refresh()
            messagebox.showerror("Repository unavailable", str(exc), parent=self)
            return
        self._set_repo_lamp("#34a853")
        self.repository = repository
        self._render(repository)
        for ticket in repository.tickets:
            if ticket.status in {TicketStatus.QUEUED, TicketStatus.FAILED}:
                self.controller.start(repository, ticket, self._queue_refresh)

    def _render(self, repository: Repository) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.cards.clear()
        if not repository.tickets:
            ttk.Label(
                self.list_frame,
                text="No open issues labeled “auto”.",
                padding=24,
            ).pack()
            return
        for ticket in repository.tickets:
            card = TicketCard(
                self.list_frame,
                ticket,
                on_open=self._open,
                on_merge=lambda selected, repo=repository: self._merge(repo, selected),
                on_dump=lambda selected, repo=repository: self._dump(repo, selected),
                on_close=lambda selected, repo=repository: self._close_ticket(
                    repo, selected
                ),
            )
            card.pack(fill="x", pady=(0, 10))
            self.cards[ticket.number] = card

    def _queue_refresh(self, ticket: Ticket) -> None:
        self.events.put(("refresh", ticket))

    def _refresh(self, ticket: Ticket) -> None:
        card = self.cards.get(ticket.number)
        if card:
            card.refresh()

    def _remove_ticket(self, ticket: Ticket) -> None:
        card = self.cards.pop(ticket.number, None)
        if card is not None:
            card.destroy()
        if self.repository is not None and not self.repository.tickets:
            for child in self.list_frame.winfo_children():
                child.destroy()
            ttk.Label(
                self.list_frame,
                text="No open issues labeled “auto”.",
                padding=24,
            ).pack()

    def _open(self, ticket: Ticket) -> None:
        try:
            self.controller.git.open_in_cursor(ticket)
        except Exception as exc:
            messagebox.showerror("Could not open worktree", str(exc), parent=self)

    def _merge(self, repository: Repository, ticket: Ticket) -> None:
        self._submit_action(
            repository,
            ticket,
            self.controller.merge,
            "Merge failed",
        )

    def _dump(self, repository: Repository, ticket: Ticket) -> None:
        if not messagebox.askyesno(
            "Discard worktree",
            f"Discard all work for issue #{ticket.number}?",
            parent=self,
        ):
            return
        self._submit_action(
            repository,
            ticket,
            self.controller.dump,
            "Dump failed",
        )

    def _close_ticket(self, repository: Repository, ticket: Ticket) -> None:
        if not messagebox.askyesno(
            "Close issue",
            f"Close GitHub issue #{ticket.number} and remove it from Ticket Shredder?",
            parent=self,
        ):
            return
        card = self.cards.get(ticket.number)
        if card and card.ticket is ticket:
            card.set_busy()
        future = self.controller.executor.submit(
            self.controller.close, repository, ticket
        )
        future.add_done_callback(
            lambda result: self.events.put(("closed", ticket, result))
        )

    def _submit_action(
        self,
        repository: Repository,
        ticket: Ticket,
        action: Callable[[Repository, Ticket], None],
        title: str,
    ) -> None:
        card = self.cards.get(ticket.number)
        if card and card.ticket is ticket:
            card.set_busy()
        future = self.controller.executor.submit(action, repository, ticket)
        future.add_done_callback(
            lambda result: self.events.put(("action", ticket, result, title))
        )

    def _action_finished(
        self, ticket: Ticket, future: Future[None], title: str
    ) -> None:
        try:
            future.result()
        except Exception as exc:
            messagebox.showerror(title, str(exc), parent=self)
        self._refresh(ticket)

    def _closed_finished(self, ticket: Ticket, future: Future[None]) -> None:
        try:
            future.result()
        except Exception as exc:
            messagebox.showerror("Close failed", str(exc), parent=self)
            self._refresh(ticket)
            return
        self._remove_ticket(ticket)

    def _set_repo_lamp(self, color: str) -> None:
        self.repo_lamp.delete("all")
        self.repo_lamp.create_oval(3, 3, 19, 19, fill=color, outline="")

    def _remote_changed(self, *_args: object) -> None:
        if str(self.entry.cget("state")) != "disabled":
            self._set_repo_lamp("#9ca3af")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "connected":
                self._connected(cast(Future[Repository], event[1]))
            elif kind == "refresh":
                self._refresh(cast(Ticket, event[1]))
            elif kind == "action":
                self._action_finished(
                    cast(Ticket, event[1]),
                    cast(Future[None], event[2]),
                    cast(str, event[3]),
                )
            elif kind == "closed":
                self._closed_finished(
                    cast(Ticket, event[1]),
                    cast(Future[None], event[2]),
                )
        self.after(50, self._drain_events)

    def _close(self) -> None:
        self.controller.shutdown()
        self.destroy()
