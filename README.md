# Ticket Shredder

Ticket Shredder is a Windows-friendly Tkinter dashboard that turns open GitHub
issues labeled `auto` into isolated Cursor agent jobs.

## Prerequisites

- Python 3.10 or newer
- Git
- GitHub CLI (`gh auth login`)
- Cursor CLI on `PATH`
- A `CURSOR_API_KEY` from Cursor Dashboard → Integrations

## Install and run

```powershell
py -m pip install -e .
```

Create a `.env` file in the project root:

```env
CURSOR_API_KEY=crsr_...
```

Then run:

```powershell
ticket-shredder
```

To add a Desktop shortcut that launches Ticket Shredder (no console window):

```powershell
ticket-shredder-desktop
```

Paste a Git remote URL and select **Connect**. Ticket Shredder then:

1. validates and clones the repository under `$CARGO_DIR/.hProjects/<repo>`
   (requires the `CARGO_DIR` environment variable);
2. checks out (or creates) its `agents` branch;
3. syncs open `auto` issues into `.scratch/.itickets/auto`;
4. creates one branch and Git worktree per issue under
   `$CARGO_DIR/.hProjects/worktrees/<repo>/<issue>`;
5. runs up to three local Cursor agents concurrently; and
6. marks completed jobs ready for human review.

Click a ticket title to open its worktree in Cursor. **Merge** merges its branch
into `dev` and cleans up the worktree. **Dump** discards the branch and
worktree. Failed tickets remain available to dump and retry on the next launch.

Set `TICKET_SHREDDER_MODEL` to choose another Cursor model. The default is
`composer-2.5`.

## Layout

- `src/ticket_shredder/` — application source
- `tests/` — unit tests

## Tests

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover -s tests -v
```
