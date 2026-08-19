# Book-to-Skill UI

A web interface that turns books and documents (PDF, EPUB, DOCX, HTML, Markdown,
plain text, RTF, MOBI/AZW) into structured **agent skills** — no terminal, no
agent commands. Pick files, answer a short form, and the app generates
`SKILL.md` + per-chapter files + glossary + patterns + cheatsheet, driven by any
local or cloud LLM.

Built on top of the [book-to-skill](https://github.com/virgiliojr94/book-to-skill)
extraction engine (MIT), with a Streamlit front end that replaces the manual,
agent-driven workflow with a three-tab app.

## What it does

- **Extract** — drop files (or paste paths), pick text/technical mode, get stats,
  detected sections, and a preview of the extracted text.
- **Generate** — answer the usual questions in a form (skill name, book type,
  purpose/depth), pick an LLM backend, and watch each step stream: structure
  analysis → per-chapter files → glossary/patterns/cheatsheet → master SKILL.md →
  security scan.
- **Check** — one-click report of which extractors are installed per format and
  how to install what's missing.

The output is a skill folder your agent can load on demand:

```
<skills home>/<skill-name>/
├── SKILL.md            # core frameworks + chapter/topic index
├── chapters/chNN-*.md  # per-chapter files, loaded on-demand
├── glossary.md
├── patterns.md
└── cheatsheet.md
```

## Quick start

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

Run it:

```bash
source .venv/bin/activate
streamlit run ui/app.py
```

Open the printed URL (usually http://localhost:8501).

## LLM backends

Generation needs any OpenAI-compatible or Anthropic endpoint:

| Backend | Provider in UI | Base URL | API key |
|---|---|---|---|
| llama.cpp | OpenAI-compatible | `http://localhost:8083/v1` | empty |
| Ollama | OpenAI-compatible | `http://localhost:11434/v1` | empty |
| LM Studio | OpenAI-compatible | `http://localhost:1234/v1` | empty |
| OpenAI | OpenAI-compatible | default | your key |
| Anthropic | Anthropic | — | your key |

Example with llama.cpp:

```bash
# terminal 1
llama-server -m models/your-model.gguf --port 8083
# terminal 2
streamlit run ui/app.py
```

Quality note: small local models may only cover part of a long section — lower
"Max words per section" in the Extract tab for better coverage on small models.

See [ui/README.md](ui/README.md) for the full walkthrough and troubleshooting.

## Repository structure

```
book-to-skill-UI/
├── ui/
│   ├── app.py             # Streamlit app (3 tabs)
│   ├── engine.py          # extraction wrapper + generation orchestration
│   └── README.md          # detailed setup/usage guide
├── book_to_skill/         # extraction engine (from upstream book-to-skill)
├── SKILL.md               # the generator spec the UI follows
├── tools/                 # security scanner, skill validator, discovery-tax tool
├── scripts/               # extract.py entrypoint + banner
├── docs/                  # engine architecture & performance notes
└── tests/                 # pytest suite
```

## Credits

The extraction engine, `SKILL.md` generator spec, parsers, and tooling come from
[book-to-skill](https://github.com/virgiliojr94/book-to-skill) by virgiliojr94.
This project adds the web UI layer on top.

## License

MIT — applies to the converter (code + skill definition) in this repository,
**not** to any book or document you process with it.
