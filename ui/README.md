# book-to-skill UI

A web interface for [book-to-skill](https://github.com/virgiliojr94/book-to-skill) that
covers the whole pipeline: pick files, extract text, answer the usual agent questions in
a form, and generate the full skill (SKILL.md + chapters + glossary + patterns +
cheatsheet) through any OpenAI-compatible or Anthropic LLM backend — local or cloud.

Built with [Streamlit](https://streamlit.io).

## Requirements

- Python 3.9+
- Extractors for your file formats (optional but recommended):
  - PDF text: `pdftotext` (`sudo apt install poppler-utils`)
  - PDF technical (tables/code): `docling` (installed below)
  - MOBI/AZW: [Calibre](https://calibre-ebook.com/download) `ebook-convert` on PATH
- An LLM backend for the generation step (only extraction works without one):
  - [llama.cpp](https://github.com/ggml-org/llama.cpp) server, Ollama, LM Studio, or any
    OpenAI-compatible endpoint, **or**
  - OpenAI / Anthropic cloud API keys

## Install after cloning

```bash
git clone https://github.com/pantelisgeorg/book-to-skill-UI.git
cd book-to-skill-UI

# with uv
uv venv
uv pip install ".[ui,all]"          # UI + all optional extractors

# or with pip
python3 -m venv .venv
source .venv/bin/activate
pip install ".[ui,all]"
```

- `[ui]` installs Streamlit + the OpenAI and Anthropic clients.
- `[all]` installs every optional extractor (docling, pypdf, pdfminer, ebooklib, etc.).
  Skip it if you already have what you need.

## Run

```bash
source .venv/bin/activate
streamlit run ui/app.py
```

Open the printed URL (usually http://localhost:8501).

## Using the UI

### Tab 1 — Extract

1. Drop files into the upload box (PDF, EPUB, DOCX, HTML, MD, TXT, RTF, MOBI/AZW), and/or
   paste file/folder/glob paths (comma-separated) for files too large to upload.
2. Pick a mode:
   - **text** — fastest extractor per format (pdftotext for PDFs). For prose books.
   - **technical** — Docling, structure-aware (tables, code blocks as markdown),
     ~1.5s/page. For programming/academic books.
3. Click **Extract**. You'll see the extraction log, stats, detected sections (these
   become your per-chapter files), and a preview of the extracted text.

### Tab 2 — Generate skill

1. **Skill settings** — skill name (slug), skills home directory, book type, and purpose.
   - Purpose drives depth automatically: only "Reference chapters" → lean `reference`
     chapters; anything else → deeper `study` chapters with worked examples.
2. **LLM backend**:
   | Backend | Provider | Base URL | API key |
   |---|---|---|---|
   | llama.cpp | `OpenAI-compatible` | `http://localhost:8083/v1` | leave empty |
   | Ollama | `OpenAI-compatible` | `http://localhost:11434/v1` | leave empty |
   | LM Studio | `OpenAI-compatible` | `http://localhost:1234/v1` | leave empty |
   | OpenAI | `OpenAI-compatible` | default (leave empty) | your key |
   | Anthropic | `Anthropic` | n/a | your key |
3. Click **Generate skill**. Progress streams live: structure analysis → one LLM call per
   section → glossary/patterns/cheatsheet → master SKILL.md → security scan.

The result lands in `<skills home>/<skill-name>/`:

```
<skills home>/<skill-name>/
├── SKILL.md            # core frameworks + chapter/topic index
├── chapters/chNN-*.md  # per-chapter files, loaded on-demand
├── glossary.md
├── patterns.md
└── cheatsheet.md
```

Use the skills home your agent discovers, e.g. `~/.claude/skills` (Claude Code),
`~/.copilot/skills` (GitHub Copilot CLI), `~/.agents/skills` (Amp / cross-agent), or
`~/.config/opencode/skills` (opencode). The UI auto-detects which of these exist and
pre-fills the field.

### Tab 3 — Setup check

Click **Run --check** to see which extractors are installed per format and the exact
commands to install whatever is missing.

## Example: local llama.cpp

```bash
# terminal 1 — start the server
llama-server -m models/your-model.gguf --port 8083

# terminal 2 — start the UI
cd book-to-skill && source .venv/bin/activate
streamlit run ui/app.py
```

In the Generate tab: Provider `OpenAI-compatible`, Base URL
`http://localhost:8083/v1`, Model = the model name your server reports, API key empty.
Note: generation quality depends on the local model — a 7B model will produce weaker
skills than a 30B+ model; context must fit the largest chunk (per-section calls are
small, the digest calls grow with chapter count).

## Troubleshooting

- **"Extraction failed" / all sources skipped** — run the Setup check tab; the log will
  name the missing extractor.
- **Generation fails with a connection error** — the backend isn't running or the base
  URL is wrong. Test it: `curl http://localhost:8083/v1/models`.
- **Local model output is truncated or off-format** — increase context size on the
  server, use a larger model, or switch to a cloud provider.
- **Uploads too large** — use the path input instead of the uploader; it reads files
  directly from disk.
