from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

import engine

st.set_page_config(page_title="book-to-skill", page_icon="📚", layout="wide")


def ensure_uploaded_files(uploaded) -> list[str]:
    if not uploaded:
        return []
    if "upload_dir" not in st.session_state:
        st.session_state.upload_dir = tempfile.mkdtemp(prefix="book_to_skill_uploads_")
    saved = st.session_state.setdefault("uploaded_paths", {})
    paths = []
    for file in uploaded:
        key = f"{file.name}:{file.size}"
        if key in saved:
            paths.append(saved[key])
            continue
        dest = Path(st.session_state.upload_dir) / file.name
        dest.write_bytes(file.getbuffer())
        saved[key] = str(dest)
        paths.append(str(dest))
    return paths


def render_extract_tab():
    st.header("1. Extract text")
    col1, col2 = st.columns([2, 1])
    with col1:
        mode = st.radio(
            "Extraction mode",
            ["text", "technical"],
            horizontal=True,
            help="technical = Docling (structure-aware, ~1.5s/page, best for code/tables). text = fastest extractor per file.",
        )
    with col2:
        workdir = Path(
            st.text_input(
                "Work directory",
                value=st.session_state.get("workdir", str(Path(tempfile.gettempdir()) / "book_skill_work")),
            )
        ).expanduser()
        st.session_state.chunk_words = st.number_input(
            "Max words per section",
            min_value=300,
            max_value=5000,
            step=100,
            value=st.session_state.get("chunk_words", 800),
            help="How much text each generation call covers. Lower = better coverage on small local models, more files.",
        )

    uploaded = st.file_uploader(
        "Drop files (PDF, EPUB, DOCX, HTML, MD, TXT, RTF, MOBI/AZW)",
        accept_multiple_files=True,
    )
    extra_paths = st.text_input(
        "Or paste file/folder/glob paths (comma-separated) — useful for files too large to upload",
    )
    paths = ensure_uploaded_files(uploaded)
    if extra_paths.strip():
        paths += [p.strip() for p in extra_paths.split(",") if p.strip()]

    if st.button("Extract", type="primary", disabled=not paths):
        log_box = st.empty()
        log_lines = []
        with st.spinner("Extracting..."):
            try:
                engine.run_extraction(
                    paths, mode, workdir, on_log=lambda line: log_lines.append(line)
                )
            except RuntimeError as exc:
                st.error(str(exc))
                return
            finally:
                log_box.code("\n".join(log_lines), language="text")

        extracted = engine.load_workdir(workdir, fallback_words=st.session_state.get("chunk_words", 800))
        st.session_state.extracted = extracted
        st.session_state.workdir = str(workdir)
        st.success("Extraction complete")

    show_extraction_summary()


def show_extraction_summary():
    extracted = st.session_state.get("extracted")
    if not extracted:
        return
    meta = extracted.meta
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Sources", meta.get("total_sources", 1))
    m2.metric("Pages", meta.get("pages", 0))
    m3.metric("Words", f"{meta.get('words', 0):,}")
    m4.metric("Tokens", f"~{meta.get('estimated_tokens', 0) // 1000}K")
    m5.metric("Sections", len(extracted.sections))
    with st.expander("Detected sections (used for per-chapter files)"):
        for i, section in enumerate(extracted.sections, start=1):
            st.text(f"{i:02d}. {section['title']} ({len(section['body'].split()):,} words)")
    with st.expander("Metadata"):
        st.json(meta)
    with st.expander("Extracted text preview"):
        st.text_area("full_text.txt", extracted.full_text[:20000], height=300, disabled=True)


def render_generate_tab():
    st.header("2. Generate skill")
    extracted = st.session_state.get("extracted")
    if not extracted:
        st.info("Run extraction first (tab 1), or enter an existing work directory below.")
        workdir = Path(
            st.text_input("Work directory", value=st.session_state.get("workdir", str(Path(tempfile.gettempdir()) / "book_skill_work")), key="generate_workdir")
        ).expanduser()
        if workdir.exists() and (workdir / "metadata.json").exists():
            if st.button("Load extracted text"):
                st.session_state.extracted = engine.load_workdir(
                    workdir, fallback_words=st.session_state.get("chunk_words", 800)
                )
                st.session_state.workdir = str(workdir)
                st.rerun()
        return

    meta = extracted.meta
    with st.form("generation_form"):
        st.subheader("Skill settings")
        c1, c2 = st.columns(2)
        with c1:
            skill_name = st.text_input(
                "Skill name (slug)",
                value=slug_from_meta(meta),
                help="lowercase-hyphens, e.g. `cialdini-influence`",
            )
            skills_home = st.text_input(
                "Skills home",
                value=st.session_state.get("skills_home", str(extracted.workdir)),
                help="Where the generated skill folder is written. Defaults to the extraction work directory.",
            )
        with c2:
            book_type = st.selectbox("Book type", ["text", "technical"], index=0)
            purpose = st.multiselect(
                "Purpose (what should the skill help you do?)",
                [
                    "Apply the author's frameworks while working",
                    "Think with the author's mental models",
                    "Reference specific chapters and concepts",
                ],
                default=["Apply the author's frameworks while working"],
            )

        st.subheader("LLM backend")
        c3, c4 = st.columns(2)
        with c3:
            provider = st.selectbox(
                "Provider",
                ["openai", "anthropic"],
                format_func=lambda p: {
                    "openai": "OpenAI-compatible (OpenAI, llama.cpp, Ollama, LM Studio)",
                    "anthropic": "Anthropic",
                }[p],
            )
            model = st.text_input(
                "Model",
                value=st.session_state.get("model", ""),
                placeholder="e.g. gpt-4o-mini, claude-sonnet-4-5, or your local model name",
            )
        with c4:
            base_url = st.text_input(
                "Base URL (OpenAI-compatible only)",
                value=st.session_state.get("base_url", "http://localhost:8083/v1"),
                help="llama.cpp server: http://localhost:8083/v1 · Ollama: http://localhost:11434/v1",
            )
            api_key = st.text_input(
                "API key (leave empty for local servers)",
                type="password",
            )
        target_inside_workdir = (
            Path(skills_home).expanduser().resolve() == extracted.workdir.resolve()
            or Path(skills_home).expanduser().resolve().is_relative_to(extracted.workdir.resolve())
        )
        if target_inside_workdir:
            st.caption("Skills home is inside the extraction work directory — temp cleanup will be skipped so your skill is not deleted.")
        cleanup = st.checkbox("Delete temp work directory after success", value=not target_inside_workdir)
        submitted = st.form_submit_button("Generate skill", type="primary")

    if not submitted:
        depth = "study" if len(purpose) != 1 or purpose[0] != "Reference specific chapters and concepts" else "reference"
        in_tokens, out_tokens = engine.estimate_cost(meta, extracted.sections, book_type, depth)
        st.caption(
            f"Estimated: ~{in_tokens // 1000}K input tokens · ~{out_tokens // 1000}K output tokens · "
            f"{len(extracted.sections)} chapter files · {book_type} × {depth} depth"
        )
        return

    model = (model or "").strip()
    base_url = (base_url or "").strip()
    api_key = (api_key or "").strip()
    skill_name = (skill_name or "").strip()
    skills_home = (skills_home or "").strip()
    if not model:
        st.error("Please set a model name.")
        return
    if provider == "anthropic" and not api_key:
        st.error("Anthropic requires an API key.")
        return
    st.session_state.model = model
    st.session_state.base_url = base_url
    st.session_state.skills_home = skills_home

    llm = engine.LLM(provider, model, base_url, api_key)
    step_box = st.empty()
    text_box = st.empty()
    accumulated = {"text": ""}

    def on_step(label: str) -> None:
        step_box.info(f"**{label}**")

    def on_delta(delta: str) -> None:
        accumulated["text"] += delta
        text_box.code(accumulated["text"][-6000:], language="markdown")

    try:
        skill_dir = engine.generate_skill(
            llm,
            extracted,
            book_type,
            "study" if len(purpose) != 1 or purpose[0] != "Reference specific chapters and concepts" else "reference",
            ", ".join(purpose),
            skill_name,
            skills_home,
            on_step=on_step,
            on_delta=on_delta,
        )
    except Exception as exc:
        st.error(f"Generation failed: {exc}")
        return

    code, scan_out = engine.run_security_scan(skill_dir)
    if code != 0:
        st.warning("Security scan found issues — review before loading the skill:")
        st.code(scan_out)
    else:
        st.success("Security scan passed")

    st.success(f"Skill created: {skill_dir}")
    st.session_state.skill_dir = str(skill_dir)
    files = sorted(skill_dir.rglob("*"), key=lambda p: str(p))
    for f in files:
        if f.is_file():
            st.text(f"  {f.relative_to(skill_dir)}  ({f.stat().st_size:,} bytes)")

    if cleanup and not target_inside_workdir:
        shutil_rmtree(extracted.workdir)
        st.session_state.pop("extracted", None)

    with st.expander("Generated SKILL.md"):
        st.code((skill_dir / "SKILL.md").read_text(encoding="utf-8"), language="markdown")


def slug_from_meta(meta: dict) -> str:
    name = meta.get("filename", "book")
    return engine.slugify(Path(name).stem, max_len=48)


def shutil_rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def detect_skill_dirs() -> list[str]:
    found: list[str] = []
    for raw in [st.session_state.get("workdir"), st.session_state.get("skills_home")]:
        if not raw:
            continue
        root = Path(raw).expanduser()
        if not root.exists():
            continue
        found += [
            str(p) for p in sorted(root.iterdir()) if p.is_dir() and (p / "SKILL.md").exists()
        ]
    return sorted(set(found))


def render_ask_tab():
    st.header("3. Ask the book")
    st.write("Search the generated skill's files, or ask the LLM a question grounded in the book content.")

    detected = detect_skill_dirs()
    c1, c2 = st.columns([3, 1])
    skill_dir_str = c1.text_input(
        "Skill directory",
        value=st.session_state.get("skill_dir", ""),
        placeholder="/home/george/Desktop/book_skill_work/early_greek_philosophy",
    )
    if detected and not skill_dir_str:
        skill_dir_str = c2.selectbox(
            "Or pick a detected skill",
            detected,
            format_func=lambda p: Path(p).name,
        )
    skill_dir = Path(skill_dir_str).expanduser() if skill_dir_str.strip() else None
    if not skill_dir or not (skill_dir / "SKILL.md").exists():
        st.info("Point to a generated skill directory (must contain SKILL.md).")
        return
    st.caption(f"Skill: **{skill_dir.name}**")

    st.subheader("Search")
    query = st.text_input("Keywords (space-separated, matched against all .md files)", key="ask_query")
    if st.button("Search", disabled=not query.strip(), key="ask_search_btn"):
        with st.spinner("Searching..."):
            hits = engine.search_skill(skill_dir, query.strip())
        st.session_state.search_results = (str(skill_dir), query.strip(), hits)
    cached = st.session_state.get("search_results")
    if cached and cached[0] == str(skill_dir) and cached[1] == query.strip() and query.strip():
        _, _, hits = cached
        if not hits:
            st.warning("No matches found.")
        for hit in hits:
            st.markdown(f"**{hit['rel']}** — {hit['count']} match{'es' if hit['count'] != 1 else ''}")
            for snippet in hit["snippets"][:2]:
                st.code(snippet, language="markdown")
            st.divider()

    st.subheader("Ask the LLM")
    with st.form("ask_form"):
        c3, c4 = st.columns(2)
        with c3:
            provider = st.selectbox(
                "Provider",
                ["openai", "anthropic"],
                format_func=lambda p: {"openai": "OpenAI-compatible", "anthropic": "Anthropic"}[p],
            )
            model = st.text_input(
                "Model",
                value=st.session_state.get("model", ""),
                placeholder="e.g. gpt-4o-mini, claude-sonnet-4-5, or your local model name",
            )
        with c4:
            base_url = st.text_input(
                "Base URL (OpenAI-compatible only)",
                value=st.session_state.get("base_url", "http://localhost:8083/v1"),
            )
            api_key = st.text_input("API key (leave empty for local servers)", type="password")
        question = st.text_area("Question", placeholder="e.g. What is the Logos framework and when should I use it?")
        submitted = st.form_submit_button("Ask", type="primary")

    if submitted:
        if not question.strip():
            st.warning("Please enter a question.")
            return
        model = (model or "").strip()
        if not model:
            st.error("Please set a model name.")
            return
        if provider == "anthropic" and not api_key:
            st.error("Anthropic requires an API key.")
            return
        st.session_state.model = model
        st.session_state.base_url = (base_url or "").strip()

        llm = engine.LLM(provider, model, (base_url or "").strip(), (api_key or "").strip())
        try:
            system, user, used = engine.build_ask_prompt(skill_dir, question.strip())
        except Exception as exc:
            st.error(f"Could not build context: {exc}")
            return
        st.caption(f"Context files: {', '.join(used) or 'SKILL.md'}")
        answer_box = st.empty()
        accumulated = {"text": ""}

        def on_delta(delta: str) -> None:
            accumulated["text"] += delta
            answer_box.markdown(accumulated["text"])

        try:
            with st.spinner("Thinking..."):
                answer = llm.chat(system, user, on_delta=on_delta, max_tokens=2048)
            if not accumulated["text"]:
                answer_box.markdown(answer)
        except Exception as exc:
            st.error(f"Ask failed: {exc}")


def render_setup_tab():
    st.header("Setup check")
    st.write("Reports which extractors are installed for every format and how to install what is missing.")
    if st.button("Run --check"):
        log_box = st.empty()
        lines = []
        with st.spinner("Checking..."):
            engine.run_dependency_check(on_log=lambda line: lines.append(line))
        log_box.code("\n".join(lines), language="text")


tab_extract, tab_generate, tab_ask, tab_setup = st.tabs(
    ["📖 Extract", "🧠 Generate skill", "💬 Ask the book", "🔧 Setup check"]
)
with tab_extract:
    render_extract_tab()
with tab_generate:
    render_generate_tab()
with tab_ask:
    render_ask_tab()
with tab_setup:
    render_setup_tab()
