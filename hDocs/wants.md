Wants:
- a special git tag that marks that an issue can be automatically picked up by an agent and implemented autonomously. This git tag should be standard in the glossary
- the agent to create a new worktree to implement the change
- a gui that allows me to visualize which tickets are in progresses and which tickets are waiting for approval to be dismissed
    - use python with tkinter
    - screen with a single vertically scrolling panel
    - each ticket is placed in the scroll panel as a rectangular object
    - each ticket has a status light which is blue if being worked on, green if ready for review. 
    - clicking on the ticket body opens the work tree in cursor 
    - each ticket should have 2 buttons. one called merge and the other called dump. merge button merges the worktree to the dev branch, dump deletes the work tree and branch 
- 


- When a ticket is closed it should go to the chronology.
- a harness system that connects the issue to ai 