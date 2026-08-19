from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from book_to_skill.utils import _chapter_number

REPO_ROOT = Path(__file__).resolve().parent.parent

ProgressFn = Callable[[str], None]


class ExtractionResult:
    def __init__(self, workdir: Path, meta: dict, full_text: str, sections: list[dict]):
        self.workdir = workdir
        self.meta = meta
        self.full_text = full_text
        self.sections = sections


def run_extraction(paths: list[str], mode: str, workdir: Path, on_log: Optional[ProgressFn] = None) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["BOOK_SKILL_WORKDIR"] = str(workdir)
    cmd = [
        sys.executable,
        "-m",
        "book_to_skill",
        *paths,
        "--mode",
        mode,
        "--install-missing",
        "no",
    ]
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_log:
            on_log(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"extraction failed with exit code {proc.returncode}")
    meta_path = workdir / "metadata.json"
    if not meta_path.exists():
        raise RuntimeError("extraction did not produce metadata.json")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def run_dependency_check(on_log: Optional[ProgressFn] = None) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "book_to_skill", "--check"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_log:
            on_log(line.rstrip())
    proc.wait()


def load_workdir(workdir: Path, fallback_words: int = 800) -> ExtractionResult:
    meta = json.loads((workdir / "metadata.json").read_text(encoding="utf-8"))
    full_text = (workdir / "full_text.txt").read_text(encoding="utf-8")
    sections = split_sections(full_text, fallback_words)
    return ExtractionResult(workdir, meta, full_text, sections)


def split_sections(full_text: str, fallback_words: int = 800) -> list[dict]:
    lines = full_text.splitlines()
    headings = [(i, _chapter_number(line)) for i, line in enumerate(lines) if _chapter_number(line)]
    if len(headings) >= 2:
        sections = []
        for k, (start, _num) in enumerate(headings):
            end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
            body = "\n".join(lines[start:end]).strip()
            if len(body.split()) >= 80:
                sections.append({"title": lines[start].strip(), "body": body})
        if sections:
            return sections
    pages = [p.strip() for p in full_text.split("\f") if p.strip()]
    if len(pages) >= 2:
        sections = [{"title": f"Page {i}", "body": page} for i, page in enumerate(pages, 1) if len(page.split()) >= 40]
        if sections:
            return sections
    words = full_text.split()
    return [
        {"title": f"Section {i + 1}", "body": " ".join(chunk)}
        for i, chunk in enumerate(
            words[j : j + fallback_words] for j in range(0, len(words), fallback_words)
        )
    ]


def chapter_budget(book_type: str, depth: str) -> int:
    if book_type == "technical":
        return 2500 if depth == "study" else 1500
    return 1400 if depth == "study" else 1000


def estimate_cost(meta: dict, sections: list[dict], book_type: str, depth: str) -> tuple[int, int]:
    input_tokens = int(meta.get("estimated_tokens", 0) * 1.3)
    output_tokens = len(sections) * chapter_budget(book_type, depth) + 4000 + 4500
    return input_tokens, output_tokens


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return (slug or "chapter")[:max_len].rstrip("-")


class LLM:
    def __init__(self, provider: str, model: str, base_url: str, api_key: str):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key or ("sk-local" if provider == "openai" else "")

    def chat(self, system: str, user: str, on_delta: Optional[ProgressFn] = None, max_tokens: int = 4096) -> str:
        if self.provider == "anthropic":
            return self._chat_anthropic(system, user, on_delta, max_tokens)
        return self._chat_openai(system, user, on_delta, max_tokens)

    def _chat_openai(self, system: str, user: str, on_delta: Optional[ProgressFn], max_tokens: int) -> str:
        from openai import OpenAI

        client = OpenAI(base_url=self.base_url or None, api_key=self.api_key)
        stream = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            stream=True,
            temperature=0.3,
        )
        parts: list[str] = []
        for chunk in stream:
            delta = (chunk.choices[0].delta.content) if chunk.choices else ""
            if delta:
                parts.append(delta)
                if on_delta:
                    on_delta(delta)
        return "".join(parts)

    def _chat_anthropic(self, system: str, user: str, on_delta: Optional[ProgressFn], max_tokens: int) -> str:
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)
        parts: list[str] = []
        with client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.3,
        ) as stream:
            for text in stream.text_stream:
                parts.append(text)
                if on_delta:
                    on_delta(text)
        return "".join(parts)


SYSTEM_PROMPT = (
    "You are the generator half of book-to-skill, a tool that converts a document "
    "into a structured agent skill. You follow the book-to-skill spec: extract "
    "structure, not summaries; preserve the author's exact framework and term names; "
    'write in practitioner voice ("Use X when Y", not "The book explains X"); never '
    "copy raw passages from the source — always synthesize. Respond with ONLY the "
    "requested artifact (JSON or markdown), no preamble."
)


def extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def analyze_structure(llm: LLM, text: str, on_delta: Optional[ProgressFn] = None) -> dict:
    prompt = (
        "Read this excerpt from the beginning of an extracted document and produce a JSON object with:\n"
        '- "title": the full title\n'
        '- "authors": list of author name strings (empty list if unknown)\n'
        '- "chapters": list of {"number": int, "title": str} for major sections you can infer (may be empty)\n'
        '- "themes": 3-6 core subject keywords\n'
        "Respond with ONLY the JSON object.\n\nEXCERPT:\n"
        + text[:8000]
    )
    raw = llm.chat(SYSTEM_PROMPT, prompt, on_delta=on_delta, max_tokens=2000)
    try:
        data = extract_json(raw)
    except json.JSONDecodeError:
        data = {"title": "", "authors": [], "chapters": [], "themes": []}
    return data


def chapter_prompt(analysis: dict, section: dict, index: int, total: int, book_type: str, depth: str) -> str:
    technical = book_type == "technical"
    study = depth == "study"
    extra_sections = ""
    if technical:
        extra_sections += (
            "\n\n## Code Examples\n"
            "<!-- Copy the most instructive snippet from the chapter. Preserve indentation exactly. -->\n"
            "```<language>\n<key code example from this chapter>\n```\n"
            "- **What it demonstrates**: <one line>\n\n"
            "## Reference Tables\n"
            "<!-- Reproduce any comparison matrix, parameter table, or decision table in markdown. -->"
        )
    if study:
        extra_sections += (
            "\n\n## Worked Example\n"
            "<!-- Reconstruct one concrete example the author works through (sample document, "
            "dialogue, filled-in template, decision walked end-to-end). Keep it faithful but compact; "
            "never copy long raw passages. -->"
        )
    return (
        f"You are generating the per-chapter file for a book skill.\n"
        f'Book: "{analysis.get("title", "Untitled")}" by {", ".join(analysis.get("authors", []) or ["Unknown"])}.\n'
        f"Book type: {book_type}. Depth: {depth}.\n"
        f"Target length: about {chapter_budget(book_type, depth)} tokens — be dense, never pad.\n"
        "IMPORTANT: cover the ENTIRE source text, from its beginning to its end. "
        "Do not focus only on the first or last part; extract frameworks from every part of the text.\n"
        "Write the chapter file in exactly this markdown structure (omit nothing except as noted):\n\n"
        "# Chapter N: <Full Title>\n"
        "## Core Idea\n<1-2 sentences: the single most important thing this section teaches>\n\n"
        "## Frameworks Introduced\n- **<Framework Name>**: <exact formulation — preserve the author's naming>\n  - When to use: <specific situation>\n  - How: <steps or criteria>\n\n"
        "## Key Concepts\n- **<Term>**: <precise definition in 1 sentence>\n(5-10 most important terms)\n\n"
        "## Mental Models\n<2-4 thinking tools. Write as \"Use X when Y\" or \"Think of X as Y\">\n\n"
        "## Anti-patterns\n- **<What to avoid>**: <why it fails>\n"
        + extra_sections
        + "\n\n## Key Takeaways\n1. <Actionable insight>\n(3-7 takeaways)\n\n"
        "## Connects To\n- **<other section or concept>**: <why it relates>\n\n"
        f"SOURCE TEXT (section {index} of {total}):\n\n{section['body']}"
    )


def glossary_prompt(digest: str) -> str:
    return (
        "Generate the glossary.md file for a book skill:\n"
        "- Every significant term from the book, alphabetically sorted\n"
        "- Format: `**Term** — definition (Ch N)`\n"
        "- Max 1,500 tokens\n\n"
        "BOOK DIGEST (structure analysis + condensed chapter summaries):\n\n" + digest
    )


def patterns_prompt(digest: str) -> str:
    return (
        "Generate the patterns.md file for a book skill:\n"
        "- All concrete techniques, design patterns, and algorithms from the book\n"
        "- Format: `## Pattern Name\\n**When to use**: ...\\n**How**: ...\\n**Trade-offs**: ...`\n"
        "- Max 2,000 tokens\n\n"
        "BOOK DIGEST:\n\n" + digest
    )


def cheatsheet_prompt(digest: str) -> str:
    return (
        "Generate the cheatsheet.md file for a book skill. Treat it as a reasoning aid, not a keyword list: "
        "capture the author's judgment. Prioritize, in order:\n"
        "1. Decision rules — \"When X, do Y, because Z.\"\n"
        "2. Decision trees / flowcharts (nested bullets or small tables)\n"
        "3. Trade-off matrices\n"
        "4. Thresholds & defaults — the specific numbers the author commits to\n"
        "5. Tells & smells — fast heuristics for recognizing a situation\n"
        "Avoid bare term->definition rows (that is the glossary) and prose paragraphs (that is the chapters).\n"
        "Max 1,200 tokens.\n\n"
        "BOOK DIGEST:\n\n" + digest
    )


def master_prompt(analysis: dict, digest: str, skill_name: str, total_chapters: int, pages: int) -> str:
    return (
        f"Generate the master SKILL.md for a book skill. CRITICAL: keep the body under 4,000 tokens; "
        f"put the most important content FIRST.\n\n"
        f'Book: "{analysis.get("title", "Untitled")}" by {", ".join(analysis.get("authors", []) or ["Unknown"])}.\n'
        f"Skill name slug: {skill_name}\n"
        f"Pages: ~{pages} | Chapters: {total_chapters} | Generated: {date.today().isoformat()}\n\n"
        "Use exactly this structure:\n"
        "---\n"
        "name: <skill_name>\n"
        'description: "Knowledge base from \\"<Full Title>\\" by <Author(s)>. Use when applying <author>\'s frameworks for <key topics, 3-6 terms>, studying the book, or referencing its concepts."\n'
        "---\n\n"
        "<!-- argument-hint: [topic, framework name, or chapter number] -->\n\n"
        "# <Full Title>\n"
        "**Author**: <Author(s)> | **Pages**: ~<N> | **Chapters**: <N> | **Generated**: <YYYY-MM-DD>\n\n"
        "## How to Use This Skill\n"
        "- **Without arguments** — load core frameworks for reference\n"
        "- **With a topic** — ask about a topic; I find and read the relevant chapter\n"
        "- **With chapter** — ask for `ch05`; I load that specific chapter\n"
        "- **Browse** — ask \"what chapters do you have?\" to see the full index\n\n"
        "## Core Frameworks & Mental Models\n"
        "(~2,000 tokens: the author's most important named frameworks and principles. "
        "Preserve exact names. Write as \"Use X when Y\", \"Prefer X over Y because Z\".)\n\n"
        "## Chapter Index\n"
        "| # | Title | Key Frameworks |\n"
        "|---|-------|----------------|\n"
        "(one row per chapter, linking chapters/chNN-<slug>.md)\n\n"
        "## Topic Index\n"
        "<!-- Alphabetical. Major terms/frameworks -> chapter(s) that cover them. -->\n\n"
        "## Supporting Files\n"
        "- [glossary.md](glossary.md) — all key terms with definitions\n"
        "- [patterns.md](patterns.md) — all techniques and design patterns\n"
        "- [cheatsheet.md](cheatsheet.md) — quick reference tables and decision guides\n\n"
        "## Scope & Limits\n"
        "This skill covers the book content only.\n\n"
        "BOOK DIGEST:\n\n" + digest
    )


def digest_of(analysis: dict, chapter_files: list[tuple[str, str]]) -> str:
    parts = [
        f"Title: {analysis.get('title', 'Untitled')}",
        f"Authors: {', '.join(analysis.get('authors', []) or ['Unknown'])}",
        f"Themes: {', '.join(analysis.get('themes', []))}",
        "CHAPTER SUMMARIES (condensed):",
    ]
    for filename, content in chapter_files:
        parts.append(f"--- {filename}\n{content[:1200]}")
    return "\n\n".join(parts)


def generate_skill(
    llm: LLM,
    extracted: ExtractionResult,
    book_type: str,
    depth: str,
    purpose: str,
    skill_name: str,
    skills_home: str,
    on_step: Optional[ProgressFn] = None,
    on_delta: Optional[ProgressFn] = None,
) -> Path:
    meta = extracted.meta
    sections = extracted.sections
    pages = meta.get("pages", 0)

    def step(label: str) -> None:
        if on_step:
            on_step(label)

    step("Analyzing book structure...")
    analysis = analyze_structure(llm, extracted.full_text, on_delta=on_delta)

    skill_dir = Path(skills_home).expanduser() / skill_name
    chapters_dir = skill_dir / "chapters"
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    chapters_dir.mkdir(parents=True, exist_ok=True)

    chapter_files: list[tuple[str, str]] = []
    for i, section in enumerate(sections, start=1):
        step(f"Generating chapter {i}/{len(sections)}: {section['title'][:60]}")
        content = llm.chat(
            SYSTEM_PROMPT,
            chapter_prompt(analysis, section, i, len(sections), book_type, depth),
            on_delta=on_delta,
            max_tokens=chapter_budget(book_type, depth) + 1500,
        )
        filename = f"ch{i:02d}-{slugify(section['title'])}.md"
        (chapters_dir / filename).write_text(content, encoding="utf-8")
        chapter_files.append((filename, content))

    digest = digest_of(analysis, chapter_files)

    step("Generating glossary.md...")
    (skill_dir / "glossary.md").write_text(
        llm.chat(SYSTEM_PROMPT, glossary_prompt(digest), on_delta=on_delta, max_tokens=2200),
        encoding="utf-8",
    )

    step("Generating patterns.md...")
    (skill_dir / "patterns.md").write_text(
        llm.chat(SYSTEM_PROMPT, patterns_prompt(digest), on_delta=on_delta, max_tokens=2800),
        encoding="utf-8",
    )

    step("Generating cheatsheet.md...")
    (skill_dir / "cheatsheet.md").write_text(
        llm.chat(SYSTEM_PROMPT, cheatsheet_prompt(digest), on_delta=on_delta, max_tokens=2000),
        encoding="utf-8",
    )

    step("Generating master SKILL.md...")
    (skill_dir / "SKILL.md").write_text(
        llm.chat(
            SYSTEM_PROMPT,
            master_prompt(analysis, digest, skill_name, len(sections), pages),
            on_delta=on_delta,
            max_tokens=6000,
        ),
        encoding="utf-8",
    )
    return skill_dir


STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "is", "are", "was", "were",
    "what", "which", "when", "where", "who", "how", "why", "does", "do", "did", "it", "its",
    "this", "that", "i", "you", "we", "they", "be", "by", "with", "from", "as", "at", "about",
    "can", "should", "would", "could", "if", "then", "than", "not", "no", "my", "me", "your",
    "him", "her", "his", "their", "there", "here", "has", "have", "had", "will",
}


def search_skill(skill_dir: Path, query: str, max_hits: int = 12) -> list[dict]:
    terms = [t for t in re.split(r"\W+", query.lower()) if t and t not in STOPWORDS]
    if not terms:
        return []
    results: dict[str, dict] = {}
    for md in sorted(skill_dir.rglob("*.md")):
        rel = str(md.relative_to(skill_dir))
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        count = sum(low.count(t) for t in terms)
        if not count:
            continue
        entry = {"rel": rel, "path": str(md), "count": count, "snippets": []}
        for term in terms:
            start = 0
            for _ in range(2):
                idx = low.find(term, start)
                if idx == -1:
                    break
                entry["snippets"].append(text[max(0, idx - 200) : idx + 300].strip())
                start = idx + len(term)
        results[rel] = entry
    return sorted(results.values(), key=lambda r: (-r["count"], r["rel"]))[:max_hits]


def ask_context(skill_dir: Path, query: str, max_files: int = 3, max_chars: int = 16000) -> tuple[str, list[str]]:
    hits = search_skill(skill_dir, query, max_hits=20)
    parts: list[str] = []
    used: list[str] = []
    budget = max_chars
    for hit in hits:
        if len(used) >= max_files or budget <= 0:
            break
        text = Path(hit["path"]).read_text(encoding="utf-8", errors="replace")
        parts.append(f"### FILE: {hit['rel']}\n{text[:budget]}")
        used.append(hit["rel"])
        budget -= len(text)
    if not parts:
        root = skill_dir / "SKILL.md"
        if root.exists():
            text = root.read_text(encoding="utf-8", errors="replace")
            parts.append(f"### FILE: SKILL.md\n{text[:budget]}")
            used.append("SKILL.md")
    return "\n\n".join(parts), used


def build_ask_prompt(skill_dir: Path, question: str) -> tuple[str, str, list[str]]:
    context, used = ask_context(skill_dir, question)
    system = (
        "You are a book knowledge assistant. Answer questions using ONLY the provided book-skill files. "
        "Use the author's exact terms, be precise, and cite the file (e.g. chapters/ch02-....md) for each claim. "
        "If the files do not contain the answer, say so and suggest the closest related topic."
    )
    user = (
        f"BOOK SKILL FILES (from skill \"{skill_dir.name}\"):\n\n"
        f"{context}\n\n"
        f"QUESTION:\n{question}"
    )
    return system, user, used


def run_security_scan(skill_dir: Path) -> tuple[int, str]:
    scanner = REPO_ROOT / "tools" / "scan_generated_skill.py"
    proc = subprocess.run(
        [sys.executable, str(scanner), str(skill_dir)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()
