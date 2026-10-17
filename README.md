# EditMatch

EditMatch is a hackathon prototype website that connects **video editors** with **clients** who need custom video editing.

- **Editors** create a profile, upload up to five portfolio samples, set prices and availability, and receive invitations.
- **Clients** describe a video in plain English, check the requirements the site understood, see the best-matching editors with explanations, and send invitations.
- **TinyFish** (a web agent) reads public portfolio or reference web pages and returns the visible facts as structured data.

The project is built with Python (Flask), SQLite, plain HTML/CSS, and a little JavaScript. No React, Node, Tailwind, Bootstrap or Docker is used.

---

## 1. Quick start on Windows (VS Code)

Run these commands in the VS Code terminal, inside the `EditMatch` folder.

### Step 1: Check Python (3.11 or newer)

```bash
py --version
```

If Python is missing, install Python 3.11+ from python.org and tick "Add python.exe to PATH".

### Step 2: Create a virtual environment

```bash
py -m venv .venv
```

### Step 3: Activate it

In **PowerShell** (VS Code's default on Windows):

```bash
.\.venv\Scripts\Activate.ps1
```

In **Command Prompt** instead:

```bash
.venv\Scripts\activate.bat
```

If PowerShell says scripts are disabled, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

### Step 4: Install the packages

```bash
python -m pip install -r requirements.txt
```

### Step 5: Create your `.env` file

Copy `.env.example` to `.env` (same folder), then open `.env` and set:

- `FLASK_SECRET_KEY`: any long random text. Generate one with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- `TINYFISH_API_KEY`: your key from https://agent.tinyfish.ai/api-keys. **Leave it empty** if you do not have one. The site still works. Public portfolio reading is then shown as "TinyFish is not configured".

Never commit `.env` to GitHub. It is already listed in `.gitignore`.

### Step 6: Add demo data (optional but recommended)

```bash
python seed_demo.py
```

This creates four fictional editor profiles and one demo client. The password for every demo account is `DemoPass123`.

| Role | Email | Password |
|---|---|---|
| Editor (gaming, Fortnite) | demo.aarav@editmatch.local | DemoPass123 |
| Editor (reels, shorts) | demo.meera@editmatch.local | DemoPass123 |
| Editor (cinematic, ads) | demo.rohan@editmatch.local | DemoPass123 |
| Editor (podcasts, educational) | demo.ishita@editmatch.local | DemoPass123 |
| Client | demo.client@editmatch.local | DemoPass123 |

These accounts are for local development only. Delete them or change their passwords before any public deployment.

### Step 7: Start the website

```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Press `Ctrl+C` in the terminal to stop it.

### Step 8: Run the tests

```bash
python -m unittest discover -s tests -v
```

The matching tests use fixed inputs, so they always give the same results.

---

## 2. Demonstration flow

1. Open the home page and look at the two options: **Be an Editor** and **Hire an Editor**.
2. Register as an editor, then fill in the profile and upload a sample video (MP4 works best).
3. Open the editor dashboard to see the profile completion percentage and the portfolio.
4. Log out. Register as a client (or log in as `demo.client@editmatch.local`).
5. Click **Hire an Editor**, describe a Fortnite montage under ₹2,000 with velocity editing, then click **Understand My Requirements**.
6. Correct any pointer if needed, then click **Find My Editors**.
7. On the matches page, read why each editor fits. Click **Invite Editor**.
8. Log in as that editor and **Accept** the invitation. The client's dashboard now shows the accepted status and the editor's contact email.

---

## 3. Folder structure

```
EditMatch/
├── app.py                  Routes, login checks, forms, uploads, invitations
├── database.py             SQLite tables and connection helpers
├── matching_service.py     Requirement parsing and the scoring rules (edit weights here)
├── tinyfish_service.py     TinyFish calls, URL safety checks, result cleaning
├── seed_demo.py            Adds fictional demo accounts
├── requirements.txt        Python packages
├── .env.example            Template for your secret settings
├── .gitignore
├── README.md
├── instance/               The SQLite database file is created here on first run
├── static/
│   ├── css/style.css       All colours, sizes and card styles (start with :root)
│   ├── js/main.js          Small helpers: double-click protection, file names, examples
│   └── uploads/            Uploaded photos, videos and reference files (created at runtime)
├── templates/              HTML pages (Jinja2). base.html holds the header and footer.
└── tests/
    ├── test_matching.py    Tests for the scoring and parsing rules
    └── test_app.py         Tests for accounts, access control and the TinyFish fallback
```

---

## 4. How the pieces work together

1. **Browser → Flask routes (`app.py`).** Each address, such as `/hire`, runs one Python function.
2. **Templates (`templates/`).** Flask fills in the HTML with data from the database.
3. **SQLite (`instance/editmatch.db`).** Every query uses `?` placeholders, so user input is never pasted into SQL.
4. **Matching (`matching_service.py`).** Pure Python. No database access, so it is easy to test.
5. **TinyFish (`tinyfish_service.py`).** Called only from the backend. The API key is read from `.env` and is never sent to the browser.

### Matching score (out of 100)

| Part | Points | What it checks |
|---|---|---|
| Category and game | 30 | The editor lists the requested game and/or category (in the profile, samples, or extracted public page facts). |
| Effects and techniques | 25 | Share of the requested techniques found in the profile, samples, or extracted public facts. |
| Budget | 20 | The editor's starting price is within the client's maximum. |
| Deadline | 10 | The editor's typical delivery time fits the client's timeline. |
| Portfolio evidence | 10 | Each matching sample (or matching public-page fact) adds 5 points. |
| Availability | 5 | Available = full points, Busy = partial, Unavailable = none. |

Rules to remember:

- A starting price above the client's maximum loses the budget points **and** a further 10-point penalty.
- Missing information (price, delivery time, availability) gets half points or none. It is never treated as free or ideal.
- A portfolio URL, or a successful TinyFish run on its own, adds **no** points.
- The score measures how well saved information fits the request. It does **not** measure creativity, skill or editing quality.

The weights and synonyms are at the top of `matching_service.py`.

### How the requirement parser works

The description is read with keyword lists and regular expressions (for example `under ₹2,000`, `60-second`, `48 hours`, `velocity`, `beat sync`). It does not use an AI model. The final value for each field is chosen in this order:

1. The structured field the client selected on the form.
2. The pointer the client corrected.
3. The value found in the description.

If the description disagrees with a chosen value, the page says so.

---

## 5. TinyFish

TinyFish is used in two places:

- **Editor portfolio:** on the profile form or the dashboard, *Analyse Public Portfolio with TinyFish* sends the public URL and a goal to TinyFish. It asks for visible project titles, descriptions, stated categories, techniques, portfolio links and public prices. Results are stored with the source URL and shown separately from what the editor typed.
- **Client reference link:** if a client adds a public reference URL, TinyFish reads its visible title, description and style text.

Safety rules in the code:

- Only `http://` and `https://` addresses are accepted.
- `localhost`, private IP ranges, and hosts that resolve to private addresses are refused before TinyFish is called.
- URLs with usernames or passwords are refused.
- Each run has a time limit. A run that returns no useful data is marked **incomplete**, not **completed**.
- Successful results are saved, so the same page is not read again unless you press **Retry**.
- Every attempt is listed in "Recent TinyFish activity" on the editor dashboard.

**Without a key**, the site still works. Analysis is reported as "TinyFish is not configured" and nothing is invented.

---

## 6. Customising the site

| To change... | Edit this |
|---|---|
| Colours, font sizes, spacing, card shapes | `static/css/style.css`, the `:root` block at the top |
| Homepage text and layout | `templates/index.html` |
| Header and footer links | `templates/base.html` |
| Editor profile form fields | `templates/editor_apply.html` and `read_editor_fields()` in `app.py` |
| Client project form fields | `templates/hire_editor.html` and `create_job()` in `app.py` |
| Add a new editing category or technique | The option lists at the top of `matching_service.py` (`CATEGORY_OPTIONS`, `TECHNIQUE_OPTIONS`). They appear automatically on every form. |
| Add a new game | `GAME_OPTIONS` and `TEXT_GAME_KEYWORDS` in `matching_service.py` |
| Add a new spelling or synonym | `TAG_SYNONYMS` in `matching_service.py` |
| Score weights | `SCORE_WEIGHTS` in `matching_service.py` |
| Maximum upload size | The `MAX_VIDEO_MB` value in `.env` |
| The TinyFish instructions | `PORTFOLIO_GOAL` and `REFERENCE_GOAL` in `tinyfish_service.py` |

Good first files to open: `static/css/style.css`, then `templates/index.html`.

After you change any Python file, stop the server (`Ctrl+C`) and run `python app.py` again. Refresh the browser after you edit HTML or CSS.

---

## 7. What the prototype does not do

- No payments, contracts, escrow, or chat between users.
- No real identity checks. Self-declared experience is not verified.
- No email sending. Invitations appear on dashboards only.
- Browsers can only play some video formats. MP4 with H.264 plays most reliably. MOV and WebM depend on the browser. The site does not convert videos.
- YouTube and Vimeo links are shown as "Watch Sample" links. Videos are not embedded or downloaded.
- TinyFish cannot watch videos or judge their quality. It reads visible text on public pages.
- Login has no rate limiting, no password reset, and no email verification.
- Local storage only: uploads go in `static/uploads/` and the database is a single SQLite file.
- The app is not production ready. For a real deployment you would need HTTPS, a production secret, persistent storage for uploads, backups, and a proper web server.

---

## 8. Troubleshooting

| Problem | What to try |
|---|---|
| `py` is not recognised | Reinstall Python and tick "Add python.exe to PATH". Then open a new terminal. |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `ModuleNotFoundError: No module named 'flask'` | Activate `.venv` (you should see `(.venv)` in the prompt), then run `python -m pip install -r requirements.txt`. |
| Port 5000 is already in use | Close the other program, or change the port at the bottom of `app.py`. |
| Video does not play in the browser | Re-encode it as MP4 (H.264). MOV files often fail in Chrome. |
| "The page has expired" or a security error when submitting a form | The security token expired. Reload the form page and submit again. |
| Logins reset every time the server restarts | Set `FLASK_SECRET_KEY` in `.env` so the key does not change. |
| "TinyFish is not configured" | Add `TINYFISH_API_KEY` to `.env`, then restart the app. Leaving it empty is fine. |
| Demo editors do not appear | Run `python seed_demo.py`. It never changes real accounts. |
| Want to start over | Stop the server, delete `instance/editmatch.db`, and run `python app.py` again. Uploaded files stay in `static/uploads/` until you delete them. |
