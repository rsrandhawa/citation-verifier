---
name: jstor-downloader
description: >
  Download academic papers from JSTOR using Chrome browser automation. Accepts BibTeX (.bib) files,
  LaTeX \bibitem references, paper titles, or JSON lists as input. Parses the input, shows the user
  a download plan for approval, then searches JSTOR and downloads PDFs in parallel.
  Use this skill whenever the user wants to download papers from JSTOR, process a bibliography for
  bulk downloads, fetch academic articles, or mentions .bib files with JSTOR. Also triggers for
  "download paper", "get paper from JSTOR", "batch download papers", "fetch articles from JSTOR",
  or any request involving JSTOR article retrieval. Even if the user just pastes a list of paper
  titles or bibliography entries, use this skill.
---

# JSTOR Fast Paper Downloader

Download academic papers from JSTOR via Chrome browser automation.
Uses parallel tab searching and parallel fetch()-based PDF download for speed.

## How This Skill Works (for the user)

1. User provides papers in any format (bib file, bibitem text, titles, JSON)
2. Claude parses the input and shows a numbered list: "Here are the N papers I'll download..."
3. User confirms
4. Claude searches JSTOR in parallel, downloads all PDFs, and reports results

The whole process takes about 10-15 seconds for a batch of 20 papers.

---

## Step 0: Parse Input & Show Plan

This is the most important step for user experience. Parse the input FAST, then present
a clear plan before doing any browser work.

### Parsing formats

**BibTeX (.bib file)**: Run the bundled parser:
```bash
python3 <skill-path>/scripts/parse_bib.py <path-to-bib-file>
```
Returns JSON array of `{title, author, year, journal, key}`.

**LaTeX \bibitem**: Parse inline. Each `\bibitem[label]{key}` block contains:
- Author: first line of the body (before the year/title sentence)
- Year: from the citation label, e.g. `\bibitem[Smith(2019)]` → `2019`
- Title: the sentence between the author line and the `\emph{Journal}` line
- Journal: inside `\emph{...}`

**Plain text titles**: If user just pastes titles (one per line or comma-separated),
treat each as `{title: "...", author: "", year: ""}`.

**JSON**: If user provides `[{title, author, year}, ...]`, use directly.

### Show the plan

After parsing, immediately show the user what you found. Be concise:

```
Found 5 papers to download from JSTOR:

 1. Finklebottom & Przbylski (2017) — On the surprising aerodynamics of office chairs
 2. Chatterjee, Woo & Lastname (2011) — Organizational trust in medium-density housing
 3. Van der Sploot et al (2023) — Why do committees always pick the worst option
 ...

I'll search JSTOR for each, download the PDFs, and save them to ~/Downloads/papers/.
Shall I proceed?
```

Wait for the user to confirm before doing any browser work. If they want to remove
or modify entries, adjust the list and re-confirm.

---

## Step 1: Setup

1. **Get Chrome tab context**: `tabs_context_mcp`. Create a tab group if none exists.
   - If this tool is not available, tell the user: "This skill requires the **Claude in Chrome** extension. Install it from the Chrome Web Store, then try again."
2. **Create output folder** (cross-platform):
   - **macOS**: Use AppleScript: `do shell script "mkdir -p ~/Downloads/papers"`
   - **Windows**: Use Bash: `mkdir -p ~/Downloads/papers` (or PowerShell equivalent)
   - If AppleScript is available, prefer it for all file operations on macOS
3. **Navigate to JSTOR** and verify login:
   - Navigate a tab to `https://www.jstor.org`
   - Wait 3 seconds, take a screenshot
   - Look for "Access provided by" banner at the top → logged in
   - If you see "Register" / "Log in" buttons → NOT logged in
   - If not logged in, tell the user: "You need to be logged into JSTOR first. Please click 'Log in through your library' at the top of the JSTOR page in Chrome, complete your university login, then tell me when you're done."
   - Wait for user to confirm login, then re-verify with a screenshot

---

## Step 2: Accept T&C Cookie (one-time, ~1s)

Execute this JavaScript on the JSTOR tab. It pre-accepts JSTOR's Terms & Conditions
by fetching a PDF URL, extracting the CSRF token, and POSTing to the verify endpoint.
This sets a cookie so all subsequent PDF downloads work without T&C modals.

```javascript
(async () => {
    const resp = await fetch('/stable/pdf/26621237.pdf');
    const html = await resp.text();
    const m = html.match(/csrfmiddlewaretoken"\s+value="([^"]+)"/);
    if (m) {
        const fd = new URLSearchParams();
        fd.append('csrfmiddlewaretoken', m[1]);
        await fetch('/tc/verify?origin=/stable/pdf/26621237.pdf', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: fd.toString()
        });
        return 'accepted';
    }
    return 'no_tc_needed';
})()
```

---

## Step 3: Parallel Search (batch of up to 8)

This is where the speed comes from. Instead of searching papers one at a time,
open multiple Chrome tabs and search them all simultaneously.

For each batch of up to 8 papers:

1. **Create N new tabs** using `tabs_create_mcp` (one per paper)
2. **Navigate ALL tabs at once** — issue all navigate calls in a SINGLE message:
   ```
   Tab A → https://www.jstor.org/action/doBasicSearch?Query={encoded_title+author}&so=rel
   Tab B → https://www.jstor.org/action/doBasicSearch?Query={encoded_title+author}&so=rel
   ...
   ```
   URL-encode the search query as: `{full_paper_title} {first_author_last_name}`

3. **Wait ONCE** for all pages to render: `wait` 6 seconds (on any tab)

4. **Extract stable IDs from ALL tabs at once** — issue all JS calls in a SINGLE message:
   ```javascript
   (() => {
       const btn = document.querySelector('mfe-download-pharos-button[data-qa="download-pdf"]');
       if (btn) return btn.getAttribute('data-doi');
       const link = document.querySelector('a[href*="/stable/"]');
       if (link) {
           const m = link.href.match(/\/stable\/(\d+)/);
           if (m) return m[1];
       }
       const item = document.querySelector('[data-doi]');
       if (item) return item.getAttribute('data-doi');
       return null;
   })()
   ```
   The `data-doi` attribute contains the stable ID needed for PDF URL construction.

5. Record results: `{title, author, year, stable_id, filename}`
6. **Close all batch tabs** (`tabs_close_mcp`) to free resources
7. Repeat for next batch if more papers remain

**The speed trick**: Issuing all navigate calls and all JS extraction calls as parallel
tool calls in a single message is what makes this fast. Do NOT do them one at a time.

---

## Step 4: Parallel PDF Download

On the main JSTOR tab, execute a single JavaScript call that fetches and downloads ALL
found PDFs in parallel using `Promise.all()` and `fetch()`. The browser's authenticated
session cookies handle auth automatically.

Replace `PAPERS_JSON` with the actual JSON array of `{sid: "stableId", fn: "filename.pdf"}`:

```javascript
(async () => {
    const papers = PAPERS_JSON;
    const results = await Promise.all(papers.map(async (p) => {
        const t0 = performance.now();
        try {
            let resp = await fetch('/stable/pdf/' + p.sid + '.pdf');
            let blob = await resp.blob();
            if (blob.type !== 'application/pdf') {
                const html = await blob.text();
                const m = html.match(/csrfmiddlewaretoken"\s+value="([^"]+)"/);
                if (m) {
                    const fd = new URLSearchParams();
                    fd.append('csrfmiddlewaretoken', m[1]);
                    resp = await fetch('/tc/verify?origin=/stable/pdf/' + p.sid + '.pdf', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body: fd.toString()
                    });
                    blob = await resp.blob();
                }
            }
            const ms = Math.round(performance.now() - t0);
            if (blob.type === 'application/pdf' && blob.size > 5000) {
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = p.fn;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                setTimeout(() => URL.revokeObjectURL(url), 2000);
                return {fn: p.fn, ok: true, kb: Math.round(blob.size/1024), ms};
            }
            return {fn: p.fn, ok: false, why: blob.type + ':' + blob.size, ms};
        } catch (e) {
            return {fn: p.fn, ok: false, why: e.message, ms: Math.round(performance.now() - t0)};
        }
    }));
    return results;
})()
```

**NOTE**: Chrome will prompt the user ONCE to "Allow this site to download multiple files".
After the user clicks Allow, all PDFs download simultaneously. Tell the user to expect
this prompt the first time. Subsequent batches won't re-prompt.

**Fallback**: If downloads don't appear in ~/Downloads after the JS returns `ok: true`,
navigate the tab directly to `https://www.jstor.org/stable/pdf/{stableId}.pdf` — this
opens Chrome's PDF viewer. Then use the `computer` tool to click Chrome's download button.

---

## Step 5: Move & Rename PDFs

After the parallel download completes, wait 2 seconds for Chrome to finish writing
all files, then move them all to the papers/ folder.

**macOS** (use AppleScript):
```applescript
do shell script "mv ~/Downloads/OriginalName.pdf ~/Downloads/papers/Author_Year_Title.pdf"
```

**Windows** (use Bash):
```bash
mv "$HOME/Downloads/OriginalName.pdf" "$HOME/Downloads/papers/Author_Year_Title.pdf"
```

To find the most recently downloaded PDF:
```applescript
do shell script "ls -lt ~/Downloads/*.pdf | head -3"
```

### File naming convention
```
{FirstAuthorLastName}_{Year}_{First_4_Words_Of_Title}.pdf
```
Remove special characters, replace spaces with underscores, max 80 chars.
Example: `Finklebottom_2017_On_the_surprising_aerodynamics.pdf`

---

## Step 6: Report Results

Show a clear summary immediately after completion:

```
Downloaded 4/5 papers to ~/Downloads/papers/

✓ Finklebottom_2017_On_the_surprising_aerodynamics.pdf (742KB, 0.8s)
✓ Chatterjee_2011_Organizational_trust_in_medium.pdf (1103KB, 0.6s)
✓ VanderSploot_2023_Why_do_committees_always.pdf (890KB, 1.1s)
✓ Gunderson_2009_Napkin_based_forecasting_models.pdf (2564KB, 0.9s)
✗ Patel_2020_Quarterly_Feelings — not found on JSTOR (may be a book, not article)
```

If some papers failed, briefly explain why and suggest alternatives (e.g., "This appears
to be a book chapter — try searching for individual chapters on JSTOR directly").

---

## Important Notes

### Speed
- The speed advantage comes entirely from **parallel tool calls**. Always issue
  navigate, JS extraction, and tab operations as batched parallel calls in a single message.
- Max 8 tabs per batch to avoid JSTOR throttling.

### JSTOR quirks
- **Stable ID location**: The download button has `data-doi="{stableId}"` and `data-qa="download-pdf"`.
  The PDF URL is `/stable/pdf/{stableId}.pdf`.
- **Non-numeric IDs**: Some results have IDs like `resrep59289` or `j.ctt1r2ggg.13` (research
  reports, book chapters). They work the same way.
- **"Read online" only**: Papers with `data-qa="read-online"` instead of `data-qa="download-pdf"`
  don't have downloadable PDFs. Report these as unavailable.
- **T&C cookie**: Once accepted in Step 2, it persists for the session. No need to re-accept.

### Cross-platform file operations
To detect the platform, try calling AppleScript first. If the tool exists, you're on macOS.
If it doesn't exist or errors, assume Windows/Linux and use Bash.

- **macOS**: Use AppleScript (`mcp__applescript_execute__applescript_execute`) for all file operations.
  The Cowork VM can't access ~/Downloads directly — AppleScript bridges to the real filesystem.
  ```applescript
  do shell script "mkdir -p ~/Downloads/papers"
  do shell script "mv ~/Downloads/Paper.pdf ~/Downloads/papers/Paper.pdf"
  do shell script "ls -lt ~/Downloads/*.pdf | head -10"
  ```
- **Windows/Linux**: Use Bash tool for file operations:
  ```bash
  mkdir -p "$HOME/Downloads/papers"
  mv "$HOME/Downloads/Paper.pdf" "$HOME/Downloads/papers/Paper.pdf"
  ls -lt "$HOME/Downloads/"*.pdf | head -10
  ```
  On Windows, `$HOME` resolves to `C:\Users\Username`. The Bash tool handles path translation.
- **Both**: Chrome browser automation (tabs, navigate, JS) works identically on all platforms.
  The search and download phases are pure JavaScript in the browser — no platform differences.

### Edge cases
- **Cookie banners**: Dismiss them. Choose privacy-preserving options.
- **Already downloaded**: Check the papers/ folder before starting. Skip duplicates.
- **Books vs articles**: Full books often aren't available as single PDFs on JSTOR.
  Individual chapters may be. Note this to the user.
- **Rate limiting**: If JSTOR returns errors, reduce batch size or add delays between batches.
