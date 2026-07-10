Wants:
- a bar on the top of the gui 
- itickets marked with "auto" to be put into .scratch, then automatically add to the gui for autonomous implementation 
- 
- the agent to create a new worktree to implement the change
- a gui that allows me to visualize which tickets are in progresses and which tickets are waiting for approval to be dismissed
    - use python with tkinter
    - an upper bar with 1 field. The first is a git url field that tells the GUI which repo to target
    - A lamp next to the text field thats gray by default when empty. If full and the repo is found and accessible, the lamp is green. If the repo can't be found or is inaccessible, the lamp is red
    - When the GUI aquires the repo, it clones it and switches to its dev branch.
    - has a body with a single vertically scrolling panel
    - In the clone, it adds each auto iticket to .scratch, then populates each auto to the scroll panel as a ticket object
    - each ticket has a status light which is blue if being worked on, green if ready for review. 
    - each ticket should open its own cursor agent that creates its own work tree. 
    - clicking on the ticket body opens the work tree in cursor 
    - each ticket should have 2 buttons. one called merge and the other called dump. merge button merges the worktree to the dev branch, dump deletes the work tree and branch 
- a harness that tells the ai to implement this change and return success or failure.

- a harness system that connects the issue to ai 