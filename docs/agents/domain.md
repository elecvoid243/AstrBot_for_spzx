# Domain documents

This repository uses a single domain context.

## Read before exploring

- Read the root `CONTEXT.md` for domain terminology.
- Read ADRs in `docs/adr/` that relate to the area being changed.

If these documents do not exist, continue silently. Do not flag their
absence or suggest creating them in advance. The `domain-modeling` skill
creates them when terminology or architectural decisions need to be recorded.

## Layout

- `CONTEXT.md`: repository-wide domain vocabulary and model.
- `docs/adr/`: repository-wide architectural decision records.

## Use the domain vocabulary

Use terms defined in `CONTEXT.md` when naming domain concepts in issues,
proposals, hypotheses, and tests. Respect explicitly excluded synonyms.

If a needed concept is missing, reconsider whether it belongs to the
project's language. Record a real vocabulary gap through `domain-modeling`.

## Surface ADR conflicts

Explicitly identify any proposal that contradicts an existing ADR and
explain why the decision should be reconsidered.
