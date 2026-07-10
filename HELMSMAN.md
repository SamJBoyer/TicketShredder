<helmsman-summary>

This is a Helmsman project.

Helmsman is an idea-to-product development pipeline designed to facilitate collaboration between developers and agents. Helmsman works by taking basic, unstructured ideas about wants and questions and passing them through multiple layers of structuring until they become actionable. 

Helmsman projects have a tag that tells which canon version of the document they're using. Look in the .helmsman/hVersion.md for this version.

HELMSMAN.md should be kept consistent with the canonical tagged version, and glossary.md should have the required sections and terms. 

</helmsman-summary>

<repo-structure>

Helmsman Repo Structure: 

.helmsman/
- .chronicle/
- hVersion.md 

hDocs/
- artifacts.md
- glossary.md
- master.md
- overlay.md 
- questions.md
- status.md
- wants.md
HELMSMAN.md
AGENTS.md
jot.md

Helmsman projects should always be git projects. They should have 2 branches by default:
- main: branch with the most recent functional project 
- dev: the branch with active development. Agents merge work trees to the dev branch. Only a human can merge to main, or an agent with explicit permission

NEVER read the jot.md file. This is a human-ONLY file for keeping notes. 
NEVER edit files in the hDocs folder without EXPLICIT permission. 

</repo-structure>

<documents>

Read every document in hDocs 

hDocs contain the following documents:
- glossary 
- artifacts
- master
- overlay 
- status
- questions
- wants

How to use each document:

<glossary>

Glossary contains the canon definitions used in a project. Glossary, by default, has 3 sections: 
- terms: project-specific dictionary of important terms used in scope. These terms are to be used aggressively in code to develop a shared language between the agent and developer. The terms section of the glossary is the ground truth for such definitions
- itags: itag stands for issue-tags. issue-tags refer to the tag on the repository's git issues page and are used to demarcate types of issues. 
- hLabels: colored emoji squares that demarcate different ideas connected laterally instead of hierarchically. 

do: 
- ask if you’re unsure of terms
- check if an itag already exists before making a new one 
do not:
- ever add new terms or modify term definitions without asking for explicit permission 

</glossary>

<wants>

wants is a file that has a list of things the developer “wants” to happen in this project. It is a jot-down of current desires before they are made actionable. This file serves as a dynamic and changing quick file for the developer to jot down their desires before more work is put in to make it actionable.

</wants>

<master>

Summary of the project. This file explains what the project should do. Essentially a high-level document. Also has assumptions.

</master>

<status>

This document explains where the project currently is from the perspective of the developer. This document is an important truth control that bridges what the project should do in the documents and the state of the actual code. Documents are aspirational and upward facing. We always write in our documents what should happen and what should be the case. The code is what’s actually the case. Oftentimes the developer knows that the reality of the code doesn’t match the aspirations of the docs. status is a document to make explicit what the developer actually thinks about the code and what they view as the next steps to convert the reality of the code into the aspirations of the docs

</status>

<overlay>

Explains which other projects are important to this one and how they interact at a high level 

</overlay>

<questions>

Things I’m not sure about that I want to record 

</questions>

<artifacts>

Previous significant changes stored in a log. This should be used when weird behavior emerges or it’s unclear why an architectural decision was made, as it might be an artifact from a previous version. This document should be used before looking at git history 

</documents>