# Citation Verifier — Setup Guide

Everything below is copy-paste into Terminal (the black/white icon in Applications > Utilities).

---

## 1. Check Python

Paste this and press Enter:

```
python3 --version
```

If you see `Python 3.10` or higher, skip to Step 2.

If not, install it:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12
```

## 2. Check LaTeX

```
pdflatex --version
```

If you see output mentioning "TeX", skip to Step 3.

If not, install MacTeX (large download, ~5 GB):

```
brew install --cask mactex
```

Then **close and reopen Terminal**.

## 3. Set Up the Project

Navigate to the project folder (right-click the folder in Finder > "Copy as Pathname", then type `cd ` and paste):

```
cd /path/to/citation-verifier
```

Create a virtual environment and install dependencies:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Add Your API Key

```
cp env.example .env
open -e .env
```

This opens a text file. Replace `sk-ant-...` with your actual Anthropic API key, then save and close.

To get an API key: go to https://console.anthropic.com/settings/keys

## 5. Test It

Run a dry run on the included test paper (no API calls, no cost):

```
source .venv/bin/activate
python run.py --tex test_input/main.tex --papers test_input/papers --dry-run
```

You should see a list of citations found and a cost estimate.

## 6. Run on Your Own Paper

Put your files in the `input/` folder that's already included in the project:

```
input/
  main.tex      ← your LaTeX file
  papers/       ← a subfolder with the cited PDFs
```

Then run:

```
source .venv/bin/activate
python run.py --tex input/main.tex --papers input/papers
```

It will:
1. Create `input/mapping.xlsx` — review it, fix any `??` entries
2. Run again with the same command — produces `input/output/main_verified.pdf`

## Every Time You Come Back

Open Terminal, then:

```
cd /path/to/citation-verifier
source .venv/bin/activate
```

Now you can run commands as in Steps 5–6.

---

**Cost:** Approximately $0.015 per citation ($0.75 for 50 citations). Use `--dry-run` to see estimate first.

**Problems?** Open an issue with the Terminal output.
