---
name: draaiboek
description: Write and edit Leeuwenbergh event runbooks (draaiboeken). Use for any request to make, update, check or read a draaiboek, to look up what was agreed for an event, or to find out who still has to be called. Gathers from Gmail, ClickUp, Xero, Missive and Larissa's own chat history; proposes changes she approves.
category: operations
triggers:
  - draaiboek
  - maak een draaiboek
  - werk het draaiboek bij
  - pas het draaiboek aan
  - wie moeten we nog bellen
  - wat is er afgesproken voor
---

# Draaiboek

A draaiboek is the runbook the crew reads on the day at Leeuwenbergh Utrecht:
one timetable, the room setup, catering, deliveries, who to ring, and what is
still unanswered. Larissa owns them and edits them by hand. Her edits are law.

You have tools; they are the only way to touch a document. You do not have, and
must not use, raw Google Docs API access. If a tool refuses you, that is the
answer — not an obstacle to route around.

## Every task, in this order

1. **`house_rules()`** — first, every time. They change weekly, because she adds
   them in conversation. Never cache them.
2. **`venue()`** — the building. Grote zaal holds 225, Córdoba 20, the small
   meeting room 9; a full house is 200 across the three. Read it before writing
   a guest count or a room setup.
3. **Find the document.** Check the ClickUp task description — she pastes the
   link there. One almost always exists. `create_draaiboek` is only for an event
   that has never had one.
4. **`read_draaiboek(doc_id)`** — always, immediately before writing. Note the
   `revision_id`. Read `changed_by_larissa_since_last_read`.
5. **`gather(query)`** — Gmail, ClickUp, Xero, Missive and her chat history at
   once. Then **`read_attachment(ref, index)`** on anything attached: floor
   plans, riders, menus and signed quotes hold the numbers, and the filename
   alone tells you nothing.
6. **`propose_edits(...)`** with that `revision_id`, a title she will recognise,
   and a one-sentence `reason` on every edit. You propose; she deploys.
7. Post the workspace link **at the bottom of the topic**. Every time.

## The rule that matters most

Every row you write carries a `source` with a **literal quote** from the thing
it came from.

If you cannot quote it, you do not know it. It becomes a question under Open
punten — never a fact in the timetable. A suggestion is welcome at the bottom,
phrased as a question. Never inline.

This is the complaint she has made more than any other: *"Ga geen dingen
verzinnen."*

| `source.kind` | `ref` is | when to use |
|---|---|---|
| `email` | Gmail message id | anything from the mailbox |
| `clickup` | task id | the task description, fields, comments |
| `xero` | quote number | **verify it against the ClickUp task first** — quotes have repeatedly attached themselves to the wrong event |
| `missive` | conversation id | internal team comments, which exist nowhere else |
| `memory` | memory id | something she said in chat rather than wrote in mail |
| `house_rule` | short name | a standing rule, quoted from `house_rules()` |
| `larissa` | date | she said it to you in this conversation |
| `doc` | the draaiboek | correcting something already in it |

## When a tool refuses you

Nothing was written. Fix the content; never retry unchanged.

| | |
|---|---|
| `revision_conflict` | She edited it since you read it. Re-read and rebuild on what is there **now** — she may have done it already, or removed it deliberately. |
| `refused_by_house_rules` | The content breaks a standing rule. Change it, or raise it with her. |
| `unsupported_claim` | Your source does not say what you wrote. Quote it exactly, or make it a question. |
| `tombstone` | She deleted this row by hand. It does not come back. Ask her. |
| `expectation_failed` | You are editing a different row than you think. Re-read. |

## Never

- **Bijzonderheden** is hers. You do not write there.
- **Financials** — no prices, amounts, quote totals. Anywhere. Money lives in
  ClickUp and Xero.
- **Source citations in the document.** Provenance goes in `source`; it is
  recorded in the ledger and never shown in a cell.
- **Cancelled events** — do nothing at all. No updates, no questions, no mention.
- **Testing on one of her documents.** Use the sandbox, or `new-local`.

## Conditional rules are conditional

`house_rules()` lists automatic inclusions with the condition that triggers
each. Check the condition. She has corrected blind application:

> "Dit evenement heeft geen diner, dus deze vaste regel over Spa Rood en Blauw
> is niet van toepassing hier."

## Consistency

Guest counts, dates and names appear in several places including the header.
Use `replace_text` for those — per-row edits miss occurrences, and she checks.
