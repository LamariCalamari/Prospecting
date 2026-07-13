# 🔎 Prospecting one-sheet

A free, accuracy-first prospecting research tool. Type a person's name (plus
optional context), confirm exactly who you mean, and get a verified, link-rich
one-sheet for wealth-prospecting research.

**Sources — all free, no paid APIs:**

| Source | Used for | Key needed |
|---|---|---|
| [UK Companies House API](https://developer.company-information.service.gov.uk/) | Directorships, PSC (control %), company profiles, **filing history, charges, accounts dates, insolvency** | Free key |
| [Wikipedia API](https://www.mediawiki.org/wiki/API:Main_page) | Bio background (confident matches only) | No |
| [Wikidata](https://www.wikidata.org/) | Net worth, positions, education, official website, verified socials | No |
| [Google News RSS](https://news.google.com/) | Recent headlines + links | No |
| [The Gazette](https://www.thegazette.co.uk/) | Official notices (insolvency, strike-off, legal) | No |
| [FCA Register](https://register.fca.org.uk/Developer/s/) | Regulated-individual approvals, roles, firms | Free key + email |
| [Charity Commission](https://api-portal.charitycommission.gov.uk/) | Matching charities / eponymous foundations | Free key |
| [OpenSanctions](https://www.opensanctions.org/api/) | PEP / sanctions / watchlist screening | Free key |

Only Companies House is needed for the core sheet. The last three (FCA, Charity
Commission, OpenSanctions) are optional — without their keys the app simply
notes each as "not configured" and skips it. Wikidata, The Gazette, Wikipedia
and Google News need no keys at all.

## Design principles

- **Disambiguation is mandatory.** The app never builds a sheet until you
  confirm which specific person you mean.
- **Only real data.** Fields are left blank rather than guessed. Nothing is
  inferred or estimated.
- **Everything is attributed.** Every fact links back to its source (Companies
  House filing, Wikipedia page, or news outlet).
- **Modular.** One module per source in `sources/`, so you can add or swap
  sources — or add an LLM narrative layer — without a rewrite.

## Project structure

```
prospecting-app/
├── app.py                 # Streamlit UI (thin; search → confirm → sheet)
├── config.py              # env-driven config; no hardcoded secrets
├── requirements.txt
├── .env.example           # copy to .env and add your key
├── core/
│   ├── models.py          # dataclasses; every fact carries a source
│   ├── assembly.py        # orchestration: candidates + one-sheet build
│   └── export.py          # Markdown + PDF export
└── sources/
    ├── companies_house.py # officer search, appointments, company, PSC
    ├── wikipedia.py       # search + confident summary
    └── news.py            # Google News RSS
```

## Setup

You'll need Python 3.10+.

### 1. Create and activate a virtual environment

```bash
cd prospecting-app
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Get a free Companies House API key

1. Register at <https://developer.company-information.service.gov.uk/>.
2. Create an application and choose the **Live** REST API.
3. Copy the API key it gives you.

Wikipedia and news work without any key — but directorships and PSC data need
this one.

### 4. Add your key

```bash
cp .env.example .env
```

Open `.env` and paste your key after `COMPANIES_HOUSE_API_KEY=`. The `.env`
file is git-ignored, so your key never gets committed.

### 5. Run the app

```bash
streamlit run app.py
```

Streamlit prints a local URL (usually <http://localhost:8501>) and opens it in
your browser.

## Deploying (access it from anywhere)

To reach the app from any computer, always-on, deploy it free to **Streamlit
Community Cloud**. Your API keys go in its Secrets manager, never in the repo —
`config.py` reads from `st.secrets` automatically when there's no `.env`.

1. **Put the code on GitHub.** From the project root:
   ```bash
   git init
   git add .
   git commit -m "Prospecting one-sheet"
   ```
   `.env` and `.streamlit/secrets.toml` are git-ignored, so your keys are never
   committed. Create a repo on GitHub (private is fine) and push:
   ```bash
   git remote add origin https://github.com/<you>/prospecting-app.git
   git push -u origin main
   ```
2. **Deploy.** Go to <https://share.streamlit.io>, sign in with GitHub, click
   **Create app**, pick your repo, and set the main file to `app.py`.
3. **Add your keys.** In the app's **Settings → Secrets**, paste the contents of
   [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) with your
   real values, then save. The app restarts and picks them up.

You get a permanent `https://<your-app>.streamlit.app` URL. Keep the repo
**private** if you'd rather not share the code — deployment works either way.

## Using it

1. **Search** — enter a name and, optionally, context like
   *"CTO of a London fintech, ex-Revolut"*.
2. **Confirm** — the app shows Companies House officers and Wikipedia pages
   with disambiguating details. Pick the right one(s). This step is required.
3. **One-sheet** — review the assembled sheet, then export to Markdown or PDF.

## Notes & limits

- Companies House covers **UK** entities only.
- The officer search matches on name; use the birth year, appointment count,
  and associated companies shown on each card to pick the right person.
- Google News RSS is unauthenticated and rate-limited; occasional empty results
  are normal — just retry.
- PSC "natures of control" (e.g. *ownership-of-shares-75-to-100-percent*) are
  shown verbatim from Companies House; the app does not compute or infer
  percentages.

## Extending it

- **Add a source:** create `sources/<name>.py` returning `core.models` objects,
  then call it from `core/assembly.py`. The UI needs no changes for data that
  fits existing sections.
- **Add an LLM narrative layer:** add a function in `core/assembly.py` that
  takes a built `OneSheet` and produces a summary. Keep it clearly separated
  from the sourced facts so attribution stays intact.
