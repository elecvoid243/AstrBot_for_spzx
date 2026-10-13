# Issue tracker: GitHub

Issues and PRDs live in GitHub Issues for `elecvoid243/AstrBot_for_spzx`
(the `origin` remote). Use the `gh` CLI and explicitly pass
`--repo elecvoid243/AstrBot_for_spzx` so operations target this fork.

## Operations

- Create: `gh issue create --repo elecvoid243/AstrBot_for_spzx --title "..." --body-file <path>`
- Read with discussion: `gh issue view <number> --repo elecvoid243/AstrBot_for_spzx --comments`
- List: `gh issue list --repo elecvoid243/AstrBot_for_spzx --state open --json number,title,body,labels`
- Comment: `gh issue comment <number> --repo elecvoid243/AstrBot_for_spzx --body-file <path>`
- Add or remove labels: `gh issue edit <number> --repo elecvoid243/AstrBot_for_spzx --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --repo elecvoid243/AstrBot_for_spzx --comment "..."`

For multiline bodies, write the exact Markdown to a temporary file and use
`--body-file`.

## Skill requests

When a skill says "publish to the issue tracker", create a GitHub issue.
When a skill says "fetch the relevant ticket", read the referenced issue
with its comments.

## PRs as request surface

PRs as request surface: no.
