# Ticket Shredder

Ticket Shredder is a Windows-friendly Tkinter dashboard that turns open GitHub
issues labeled `auto` into isolated Cursor agent jobs.

## Prerequisites

- Python 3.10 or newer
- Git
- GitHub CLI (`gh auth login`)
- Cursor CLI on `PATH`
- A `CURSOR_API_KEY` from Cursor Dashboard → Integrations
- `CARGO_DIR` pointing at the Cargo root (for example `C:\Users\…\Desktop\Cargo`)

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

1. validates the remote and ensures a bare layout under
   `$CARGO_DIR/.hProjects/<repo>/` (migrating legacy full clones automatically);
2. keeps `wt/agents` on the `agents` branch as the integration worktree;
3. keeps `wt/dev` on the `dev` branch for human / TicketsPlease use;
4. syncs open `auto` issues into `wt/agents/.scratch/.itickets/auto`;
5. creates one branch and worktree per issue under `wt/tickets/<issue>`;
6. runs up to three local Cursor agents concurrently; and
7. marks completed jobs ready for human review.

Click a ticket title to open its worktree in Cursor. **Merge** merges its branch
into `agents` (and pushes `origin/agents`), then cleans up the ticket worktree.
**Dump** pushes the ticket branch to origin as a safety backup when it still has
unique commits, then discards the local worktree and branch. Failed tickets
remain available to dump and retry on the next launch.

Promotion ladder: `ticket-shredder/issue-N` → `agents` (Merge) → `dev` (human) →
`main` (release). Do not open `wt/agents` as a TicketsPlease or Cursor project
workspace — use `wt/dev`.

Set `TICKET_SHREDDER_MODEL` to choose another Cursor model. The default is
`auto`.

## Layout

```
$CARGO_DIR/.hProjects/<repo>/
  .bare/                 # single object store
  wt/agents/             # TicketShredder merge target + .scratch
  wt/dev/                # human / TicketsPlease workspace
  wt/tickets/<issue>/    # agent job worktrees
```

- `src/ticket_shredder/` — application source
- `tests/` — unit tests

## Tests

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover -s tests -v
```
