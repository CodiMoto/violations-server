# Setting up a manager's computer

About 20 minutes per computer. Codi does step 1 and the Tailscale sign-in in
step 3 (they need her accounts); the rest can be done at the manager's
computer. After that, **new versions install themselves** — see
[Updates](#updates).

**What you need**

- A Windows 10 or 11 computer at the park that **stays on** during the day —
  the phone talks to it. If it's asleep or off, the phone app can't send.
- The printer that notices should print on, already set up in Windows.
- About 20 minutes.

---

## 1. Codi: make a Rent Manager login for this manager

Each computer gets its **own** Rent Manager API user, so one computer can be
switched off without touching the others, and notes show who wrote them.

1. In Rent Manager: **Users → New user** (e.g. `Violations-Joplin`).
2. On its **General** tab tick **API Access**.
3. Give it access to **only this manager's park(s)**, and permission to add
   History Notes for tenants and prospects.
4. Note the username and password for step 3.

## 2. Download the program onto the computer

1. Open <https://github.com/CodiMoto/violations-server> (no GitHub account
   needed) → green **Code** button → **Download ZIP**.
2. Unzip it to **`C:\ViolationsServer`** — not the Desktop, Downloads or a
   OneDrive folder.

## 3. Run the installer

It installs everything the program needs that isn't already there — Python,
the printing helper and Tailscale (what lets the phone reach the computer from
anywhere, Wi-Fi or cellular, with nothing installed on the phone).

1. In `C:\ViolationsServer`, right-click **install.ps1** → **Run with PowerShell**.
   (If Windows blocks it: open PowerShell and run
   `powershell -ExecutionPolicy Bypass -File C:\ViolationsServer\install.ps1`.)
2. Windows asks *"Do you want to allow this app to make changes?"* — click
   **Yes**.
3. When asked, type the **Rent Manager username and password** from step 1,
   then the **manager's full name** (printed on notices) and a **username** for
   the phone (e.g. their first name).
4. **Tailscale — Codi:** type a short name for the phone address (e.g.
   `morristown` → `https://morristown.<your-tailnet>.ts.net`). A sign-in page
   opens: **sign in with the company Tailscale account** and approve the
   computer. The installer then turns on the phone address by itself.
5. It finishes by opening **Violations Settings**, and puts a **Violations
   Settings** icon on the desktop.

If anything goes wrong part-way (no internet, a sign-in closed too early),
just run **install.ps1** again — it picks up where it left off and keeps
everything already set.

## 4. Finish in Violations Settings

Open **Violations Settings** from the desktop. Everything at the top should be
green. Then:

1. **Phone password** — 12+ characters (a short sentence works well). Click
   **Set password** and wait for the green ✓.
2. **Parks** — tick this manager's park(s).
3. **Printer** — pick it, then **Print a test page**.
4. **How long residents get** — check the deadlines; **Save**.

## 5. The phone

1. Scan the QR code on the Violations Settings page with the phone's camera
   (or type the address).
2. Sign in with the username and password.
3. Add it to the home screen: iPhone **Share → Add to Home Screen**; Android
   **⋮ → Add to Home screen**.
4. The first time you take a photo, allow the camera.

## 6. Test, then go live

It starts in **TEST** mode — notices go on a test record in Rent Manager and
nothing prints. Try a couple end to end. When happy, **Go live** in Violations
Settings.

---

## Updates

Every hour each computer asks GitHub whether there's a new version. When there
is, it downloads it, runs the program's own checks on it, waits until nobody
has used the phone app for 10 minutes, puts it in and restarts the phone
server (a few seconds). If the new version doesn't start, it puts the old one
back by itself and doesn't try that version again.

An update **never** changes this computer's Rent Manager login, phone
password, parks, printer, deadlines, test/live setting or past notices.

**Violations Settings → Updates** shows which version is in, when it last
checked, and has **Check for updates now**.

Running **install.ps1** again is always safe too (it keeps everything) — only
needed if Violations Settings says to.

## If something's wrong

| What you see | What to do |
|---|---|
| Phone says it can't reach the manager computer | Is the computer on and awake? Is Tailscale signed in (tray icon)? |
| Violations Settings: "Phone server isn't running" | Restart the computer; if still red, run install.ps1 again. |
| "Could not sign in to Rent Manager" | Delete `config.json` and run install.ps1 again to re-enter the login. Check **API Access** is ticked on the Rent Manager user. |
| Password doesn't work on the phone | Set it again in Violations Settings and wait for the green ✓. |
| Nothing prints | Violations Settings → Printer → Print a test page. Test mode never prints. Notices print only while the manager's Windows user is signed in (it can be locked); ones added while nobody is signed in print at the next sign-in. |
| Updates: "Couldn't reach GitHub" | Is the computer online? It tries again every hour by itself. |

Logs: `C:\ViolationsServer\data\violations\server.log` (phone server),
`C:\ViolationsServer\data\violations\print.log` (printing),
`C:\ViolationsServer\data\update.log` (updates).
