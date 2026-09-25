# Violations Server

Twin Peaks Management's violation app. A manager takes a photo on their
phone, circles the problem, types the lot number and ticks what's wrong; the
**manager's computer** writes the Violation Notice (the same form the parks
use on paper), saves it with the photos to the resident's **History & Notes**
in Rent Manager, prints it, and keeps a list of active violations with their
deadlines. Tapping **It's fixed** adds a "fixed" note with a photo of the fix.

**To set up a manager's computer, follow [SETUP.md](SETUP.md).**

## How it fits together

```
 phone (web app, any browser)
        │  https://<computer>.<tailnet>.ts.net   (Tailscale Funnel)
        ▼
 manager's computer ── mgrserver.py (port 8790, this computer only)
        │                 ├─ violations.py  notice PDF, deadlines, Rent Manager notes
        │                 ├─ drafts.py      each step saved as it's done
        │                 └─ rmconn.py / rmclient.py   Rent Manager API
        ├─ settings_app.py  Violations Settings (127.0.0.1:8791, never exposed)
        └─ print_queue.py → printer (SumatraPDF, silent)
```

- **Rent Manager is the record.** Violations are tenant History Notes,
  category *Violation Notice*, with the notice PDF and photos attached. Each
  note carries a `Correct by:` line and a `[Clippy violation V-…]` tag; the
  active list and deadlines are read back from Rent Manager.
- **No AI runs in this program.** It's plain code: rules, lookups, a form.
- **Test mode** (the default) writes to a test prospect and never prints.
- **Always running.** The phone server starts with Windows (no sign-in
  needed) and is checked every 5 minutes. It runs outside anyone's sign-in,
  where Windows printing silently does nothing, so it queues each notice and
  the *Violations Print* task prints it in the signed-in session — at once, or
  at the next sign-in.
- **Updates itself.** `updater.py` runs hourly on each manager's computer
  (public repository — no GitHub account or key needed):
  a push to `main` reaches every computer within the hour, once its tests pass
  there and the phone app has been idle 10 minutes; if the new version won't
  start it rolls back. It never writes `config.json`, `violations_config.json`,
  `data/`, `tools/` or `venv/`.

## Making changes (read before pushing)

A push to `main` **is** a release to every park. So:

- Run the tests first: `venv\Scripts\python -m unittest discover -s tests`.
  (Each computer runs them again before installing; failing tests = not installed.)
- **New setting?** Add it to `violations_config.example.json` — computers
  that don't have it get the example's value at load time
  (`violations.with_defaults`). Never rename or repurpose an existing setting.
- **New library?** Add it to `requirements.txt`; the updater installs it.
- Anything that needs `install.ps1` to run again (a new scheduled task, a
  Windows change) doesn't happen by itself — make the program do it on
  start-up instead, or tell the managers.

## Security

- The phone page is reachable from the internet (that's what Funnel does),
  so: the server itself only listens on the computer; every request needs a
  signed-in session; passwords are 12+ characters and stored only as salted
  hashes; wrong passwords are rate-limited by address and by username;
  changing the password signs out every phone; strict browser security headers.
- Violations Settings listens on the computer only and is not forwarded.
- Each computer uses its own Rent Manager API user. `config.json` (that
  login) is readable only by the Windows user who installed it.
- **This repository is public.** Nothing personal or secret goes in it: logins, settings, tenant data,
  photos and notices live in `config.json`, `violations_config.json` and
  `data/`, all excluded by `.gitignore`.

## Files

| | |
|---|---|
| `install.ps1` | install on a manager's computer (safe to re-run) |
| `updater.py` | hourly self-update from GitHub, with rollback |
| `mgrserver.py` | the phone server |
| `violations.py` | notice, deadlines, Rent Manager notes, reminders |
| `drafts.py` | the step-by-step violation being written |
| `settings_app.py` | Violations Settings page |
| `print_queue.py` | prints queued notices in the signed-in session |
| `rmconn.py`, `rmclient.py` | Rent Manager API (token re-use, rate limits) |
| `imaging.py` | phone photos incl. iPhone HEIC |
| `notify.py` | reminder email (only where switched on) |
| `ui/phone/` | the phone app — one screen per step, never scrolls |
| `ui/settings/` | the settings page |
| `*.example.json` | templates for this computer's settings |
| `tests/` | `venv\Scripts\python -m unittest discover tests` |
