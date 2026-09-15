# Setup — for Florentine

Instructions for the agent, to be run on the Mac Mini. Follow them in order.
Do not improvise around a failing step: report it and stop.

Everything here is read-only or local until the very last section. Nothing in
this file writes to one of Larissa's documents.

---

## 0. Before anything

**The secrets are not in this repository, and must never be committed to it.**
Romir supplies them. If a step below needs a credential you do not have, stop
and ask him for that one credential by name.

---

## 1. Retire the old pipeline first

The old package lives at `~/draaiboek` and contains `render.py` and
`publish.py` — the code that clears a document body and rewrites it. That is
what destroyed a day of manual edits on 15 September.

It must leave the import path before the new one arrives. Do not delete it;
move it aside so it can still be consulted but not imported or run:

```bash
mkdir -p ~/romir_ws/archive
mv ~/draaiboek ~/romir_ws/archive/draaiboek-old-pipeline
```

Check that nothing can still reach it:

```bash
cd ~/romir_ws && python3 -c "import draaiboek" 2>&1 | tail -1
```

That must say `ModuleNotFoundError`. If it imports, something else is still on
the path — find it and move that aside too, and say so in your report.

This is not tidying. While `render()` is reachable, it is one bad call away from
running, and every guarantee the new system makes is void.

## 2. Get the code

Clone outside `~/romir_ws`, so the old name can never shadow the new package:

```bash
cd ~
git clone <this repository> draaiboek
cd ~/draaiboek
```

If `~/draaiboek` already exists, `git pull` instead of cloning.

## 3. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Python 3.12 or newer. Confirm it worked:

```bash
.venv/bin/draaiboek --help
```

## 4. Credentials

Create `~/.draaiboek/env`, readable only by you:

```bash
mkdir -p ~/.draaiboek && chmod 700 ~/.draaiboek
cp .env.example ~/.draaiboek/env && chmod 600 ~/.draaiboek/env
```

Then fill it in. Blank lines are skipped, so a partial setup still works —
every system you configure becomes available, the rest are reported as not
connected rather than failing silently.

| Key | Where it comes from |
|---|---|
| `CLICKUP_TOKEN`, `CLICKUP_TEAM_ID` | ClickUp → Settings → Apps → API Token |
| `MISSIVE_TOKEN` | Missive → Settings → API |
| `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, `XERO_REFRESH_TOKEN`, `XERO_TENANT_ID` | developer.xero.com |
| `GOOGLE_CREDENTIALS` | path to the service-account key JSON |
| `GOOGLE_IMPERSONATE` | `<mailbox to read>` |

**Xero rotates its refresh token on every single use.** The old one dies
immediately. The code rewrites `~/.draaiboek/env` in place when it rotates, so
never hand-edit that line while something is running, and never call the Xero
token endpoint from a scratch script — that consumes the token and the
replacement is lost.

## 5. Google access

The service account needs to see the documents. Two ways; either is fine.

**Simplest** — in Google Drive, share the draaiboeken folder with the service
account's address as **Editor**:

```
<service-account>@<project>.iam.gserviceaccount.com
```

**Better** — Workspace admin → Security → Access and data control → API controls
→ Domain-wide delegation. On client ID `<service-account client id>`, grant:

```
https://www.googleapis.com/auth/documents
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/gmail.readonly
```

Delegation is detected automatically — nothing needs reconfiguring afterwards.
The Gmail scope is already granted; Docs and Drive may not be.

Note: Docs and Drive authenticate as the service account itself, Gmail as the
impersonated mailbox. Do not merge those scope sets — requesting them together
with impersonation is rejected outright.

## 6. Check

```bash
.venv/bin/draaiboek doctor
```

Every line must read `ok`. It names the exact fix for anything that fails,
including the scopes above. Do not continue past a `FAIL` on Google access —
without it, nothing can be read or written.

## 7. Start the workspace

This is what Larissa opens.

```bash
.venv/bin/draaiboek ui
```

It serves on `http://127.0.0.1:8765`. To let her reach it from her own machine,
serve on the network and set a key first — the workspace reads all of the
mailbox, so it must not be open:

```bash
DRAAIBOEK_UI_KEY='<a long random string>' .venv/bin/draaiboek ui --host 0.0.0.0
```

Send her the link, and the key separately. Keep it running (launchd or `tmux`).

## 8. Connect yourself to it

Register the MCP server so you get the tools:

```json
{"mcpServers": {"draaiboek": {
  "command": "/Users/<you>/draaiboek/.venv/bin/draaiboek",
  "args": ["serve"]
}}}
```

Thirteen tools appear. Read **AGENT.md** in this repository — that is your
operating procedure, and it is not optional.

**Then remove your direct Google Docs API access.** If raw `batchUpdate` stays
reachable, it will get used the first time a tool refuses something, and that is
exactly how the logo, the column widths and the legend were lost on 15 September.
The tools are the only sanctioned path.

---

## The four rules that matter

1. **You propose, Larissa decides.** Use `propose_edits`, not `apply_edits`.
   She opens the workspace and deploys. Only her deploy writes.
2. **Everything you write needs a source you can quote.** If you cannot quote
   it, you do not know it — it goes to Open Punten as a question.
3. **Read the attachments.** `read_attachment(ref, index)` opens the floor plan,
   the rider, the menu. The numbers a draaiboek needs are usually in there.
4. **Never test against one of her documents.** Use a local one:
   `draaiboek new-local test` and work on `local:test`.

## When something refuses you

A refusal is information, not an obstacle — nothing was written. Fix the content
or ask Larissa. Never route around it. The full table is in AGENT.md.

## Handing over to Larissa

Send her the workspace link **at the bottom of the Topic**, every time. She has
asked for this three separate times. Her own guide is `HANDLEIDING.md` in this
repository — she needs nothing else.
