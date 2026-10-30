from __future__ import annotations

import json
import html
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

from ai_generator import LLMError, generate_quiz
from pdf_processor import PDFProcessingError, extract_pdf_pages
from quiz_agent import execute_quiz_plan, plan_quiz_request
from quiz_utils import (
    QuizValidationError,
    calculate_score,
    demo_quiz,
    validate_quiz,
)
from rag_pipeline import RAGError, chunk_text, retrieve_study_context
from vector_store import VectorStoreError, index_chunks

load_dotenv()
logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title="QuizCraft | AI Quiz Studio",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    f"<style>{Path(__file__).with_name('app.css').read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)


def init_state() -> None:
    defaults = {
        "page": "Dashboard",
        "nav_page": "Dashboard",
        "nav_rendered_page": "Dashboard",
        "pending_navigation": None,
        "quiz": None,
        "quiz_index": 0,
        "quiz_answers": {},
        "quiz_result": None,
        "history": [],
        "collection_id": None,
        "material_sources": [],
        "material_info": None,
        "last_retrieved": [],
        "last_agent_plan": None,
        "review_quiz_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


def _secret(name: str, fallback: str = "") -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, fallback))
    except (FileNotFoundError, KeyError, StreamlitSecretNotFoundError):
        return fallback


def _new_quiz(
    questions: list[dict],
    topic: str,
    difficulty: str,
    mode: str,
    source: str,
    agent_note: str | None = None,
) -> None:
    quiz_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    st.session_state.quiz = {
        "id": quiz_id,
        "topic": topic,
        "difficulty": difficulty,
        "mode": mode,
        "source": source,
        "agent_note": agent_note,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "questions": questions,
    }
    st.session_state.quiz_index = 0
    st.session_state.quiz_answers = {}
    st.session_state.quiz_result = None
    st.session_state.page = "Take Quiz"


def _generate(
    topic: str,
    difficulty: str,
    question_count: int,
    academic_level: str,
    mode: str,
    engine: str,
    custom_instructions: str = "",
) -> None:
    try:
        if engine == "Demo mode":
            if mode != "Topic-Based":
                st.error("Document-grounded generation needs an LLM API key. Demo mode uses a curated sample quiz and does not claim to use your notes.")
                return
            questions = validate_quiz(
                demo_quiz(difficulty), expected_count=5
            )
            st.info("Demo mode: these are curated sample questions, not LLM output. Demo mode uses five sample questions.")
            _new_quiz(questions, topic, difficulty, "Demo sample", "Curated sample")
            return

        api_key = _secret("LLM_API_KEY") or _secret("GROQ_API_KEY")
        base_url = _secret("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        model = _secret("LLM_MODEL", "openai/gpt-oss-20b")
        context = None
        if mode == "Study-Material-Based":
            collection_id = st.session_state.collection_id
            if not collection_id:
                st.error("Index a PDF or pasted notes on the Study materials page first.")
                return
            context = retrieve_study_context(collection_id, topic)
            if not context:
                st.error("No relevant passages were found. Try a more specific topic or index another document.")
                return
            st.session_state.last_retrieved = context

        questions = generate_quiz(
            topic=topic,
            difficulty=difficulty,
            question_count=question_count,
            academic_level=academic_level,
            context=context,
            custom_instructions=custom_instructions,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        questions = validate_quiz(questions, expected_count=question_count)
        source = "Retrieved study passages" if context else "LLM"
        _new_quiz(questions, topic, difficulty, mode, source)
    except (LLMError, QuizValidationError, RAGError, VectorStoreError) as exc:
        logging.warning("Quiz generation failed: %s", exc)
        st.error(str(exc))


def _run_agent(request: str, use_material: bool, engine: str, academic_level: str) -> None:
    try:
        plan = plan_quiz_request(request, use_material=use_material)
        st.session_state.last_agent_plan = plan
        st.caption(
            f"Plan: **{plan.topic}** · **{plan.question_count} questions** · "
            f"**{plan.difficulty}** · "
            f"{'retrieved study material' if plan.use_material else 'topic generation'}"
        )
        if engine == "Demo mode":
            if plan.use_material:
                st.error("The offline demo cannot claim to generate from your notes. Select LLM mode and configure an API key.")
                return
            st.info("The offline demo uses five curated questions; the request's count and topic are not applied.")
            _generate(plan.topic, plan.difficulty, plan.question_count, academic_level, "Topic-Based", engine)
            return

        api_key = _secret("LLM_API_KEY") or _secret("GROQ_API_KEY")
        base_url = _secret("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        model = _secret("LLM_MODEL", "openai/gpt-oss-20b")

        def generate_for_plan(topic: str, difficulty: str, count: int, context: list[dict] | None) -> list[dict]:
            return generate_quiz(
                topic=topic,
                difficulty=difficulty,
                question_count=count,
                academic_level=academic_level,
                context=context,
                api_key=api_key,
                base_url=base_url,
                model=model,
            )

        questions, retrieved = execute_quiz_plan(
            plan,
            generate_fn=generate_for_plan,
            collection_id=st.session_state.collection_id,
        )
        st.session_state.last_retrieved = retrieved
        source = "Retrieved study passages" if retrieved else "LLM"
        process_note = (
            f"Parsed {plan.question_count} {plan.difficulty.lower()} questions about "
            f"{plan.topic}; retrieved {len(retrieved)} study passages and validated "
            f"the generated questions."
            if retrieved
            else f"Parsed {plan.question_count} {plan.difficulty.lower()} questions about "
            f"{plan.topic}; generated and validated the topic-based quiz."
        )
        _new_quiz(
            questions,
            plan.topic,
            plan.difficulty,
            "AI Agent",
            source,
            agent_note=process_note,
        )
    except (LLMError, QuizValidationError, RAGError, VectorStoreError, ValueError) as exc:
        logging.warning("Agent execution failed: %s", exc)
        st.error(str(exc))


PAGES = ["Dashboard", "Create Quiz", "Study Materials", "My Quizzes", "Performance"]


def _api_is_configured() -> bool:
    return bool(_secret("LLM_API_KEY") or _secret("GROQ_API_KEY"))


def _navigate(page: str) -> None:
    st.session_state.page = page
    if page in PAGES:
        st.session_state.pending_navigation = page
    st.rerun()


def _page_heading(title: str, subtitle: str, eyebrow: str | None = None) -> None:
    if eyebrow:
        st.markdown(f'<div class="qc-eyebrow">{html.escape(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<p class="qc-subtitle">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


def _section_heading(title: str, detail: str = "") -> None:
    detail_html = f"<span>{html.escape(detail)}</span>" if detail else ""
    st.markdown(
        f'<div class="qc-section-head"><h2>{html.escape(title)}</h2>{detail_html}</div>',
        unsafe_allow_html=True,
    )


def _stat_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f'<div class="qc-card"><div class="qc-stat-label">{html.escape(label)}</div>'
        f'<div class="qc-stat-value">{html.escape(value)}</div>'
        f'<div class="qc-stat-note">{html.escape(note)}</div></div>',
        unsafe_allow_html=True,
    )


def _empty_state(title: str, message: str) -> None:
    st.markdown(
        f'<div class="qc-empty"><strong>{html.escape(title)}</strong>'
        f'<span>{html.escape(message)}</span></div>',
        unsafe_allow_html=True,
    )


def _completed_count() -> int:
    return len(st.session_state.history)


def _render_navigation() -> None:
    pending = st.session_state.pending_navigation
    if pending:
        st.session_state.page = pending
        if pending in PAGES:
            st.session_state.nav_page = pending
            st.session_state.nav_rendered_page = pending
        st.session_state.pending_navigation = None
    if st.session_state.nav_page not in PAGES:
        st.session_state.nav_page = "Dashboard"
    if st.session_state.nav_rendered_page not in PAGES:
        st.session_state.nav_rendered_page = st.session_state.nav_page

    with st.container(key="qc_topbar"):
        brand, spacer, profile, action = st.columns([1.45, 3.9, 0.65, 1.35], vertical_alignment="center")
        with brand:
            st.markdown(
                '<div class="qc-brand"><span class="qc-brand-mark">Q</span>QuizCraft</div>',
                unsafe_allow_html=True,
            )
        with spacer:
            st.empty()
        with profile:
            with st.popover("Q", help="Current session"):
                st.markdown("**QuizCraft workspace**")
                st.caption("Your quiz history and study collection are session-only.")
        with action:
            if st.button("＋ New quiz", type="primary", key="top_create_quiz", use_container_width=True):
                _navigate("Create Quiz")
        selected_page = st.radio(
            "Main navigation",
            PAGES,
            horizontal=True,
            key="nav_page",
            label_visibility="collapsed",
        )
        if selected_page != st.session_state.nav_rendered_page:
            st.session_state.page = selected_page
            st.session_state.review_quiz_id = None
        st.session_state.nav_rendered_page = selected_page

    if st.session_state.quiz and not st.session_state.quiz_result:
        quiz_topic = html.escape(st.session_state.quiz["topic"])
        resume_col, note_col = st.columns([1.2, 5.8], vertical_alignment="center")
        with resume_col:
            if st.button("Resume quiz", key="resume_active_quiz", use_container_width=True):
                st.session_state.page = "Take Quiz"
                st.rerun()
        with note_col:
            st.caption(f"Quiz in progress · {quiz_topic}")


def _render_dashboard() -> None:
    history = st.session_state.history
    completed = len(history)
    answered = sum(item["result"]["total"] for item in history)
    if st.session_state.quiz and not st.session_state.quiz_result:
        answered += len(st.session_state.quiz_answers)
    average = (
        sum(item["result"]["percentage"] for item in history) / completed
        if completed
        else None
    )
    recent_activity = (
        history[0]["result"]["completed_at"][:10]
        if history
        else "—"
    )

    st.markdown(
        '<section class="qc-hero"><div class="qc-eyebrow">YOUR PERSONAL QUIZ STUDIO</div>'
        '<h1>Your learning, made smarter.</h1>'
        '<p>Build focused practice from the subjects you care about or the notes you already have.</p>'
        '</section>',
        unsafe_allow_html=True,
    )
    hero_left, hero_right = st.columns([3, 1], vertical_alignment="center")
    with hero_left:
        if st.button("＋  Create new quiz", type="primary", key="dashboard_create"):
            _navigate("Create Quiz")
    with hero_right:
        st.markdown(
            '<div style="text-align:right;color:#69798f;font-size:.86rem">'
            "Personal workspace<br><strong style='color:#3648a8'>Session data only</strong></div>",
            unsafe_allow_html=True,
        )

    _section_heading("Your progress", "Stats update as you complete quizzes")
    stat_cols = st.columns(4)
    with stat_cols[0]:
        _stat_card("Quizzes completed", str(completed), "Completed in this session")
    with stat_cols[1]:
        _stat_card("Questions answered", str(answered), "Completed and active quiz answers")
    with stat_cols[2]:
        _stat_card("Average score", f"{average:.0f}%" if average is not None else "—", "Completed attempts")
    with stat_cols[3]:
        _stat_card("Recent activity", recent_activity, "Latest completed quiz date")

    _section_heading("Quick start", "Choose where your next quiz begins")
    quick_a, quick_b = st.columns(2)
    with quick_a:
        st.markdown(
            '<div class="qc-card"><div class="qc-card-kicker">01 · Topic-based</div>'
            '<h3>Start with a subject</h3><p>Choose a topic, set the difficulty, and shape a practice session for your level.</p></div>',
            unsafe_allow_html=True,
        )
        if st.button("Create a topic quiz", key="quick_topic", use_container_width=True):
            st.session_state.create_source = "Topic-based"
            _navigate("Create Quiz")
    with quick_b:
        st.markdown(
            '<div class="qc-card"><div class="qc-card-kicker">02 · Study materials</div>'
            '<h3>Use your own notes</h3><p>Index a PDF or pasted notes and retrieve relevant passages for grounded questions.</p></div>',
            unsafe_allow_html=True,
        )
        if st.button("Explore study materials", key="quick_materials", use_container_width=True):
            _navigate("Study Materials")

    _section_heading("Recent quizzes", "Your latest completed sessions")
    if not history:
        _empty_state("Your first quiz is a great place to start", "Completed quizzes will appear here with scores and review actions.")
    else:
        for item in history[:4]:
            _quiz_history_row(item, "dashboard")


def _render_create() -> None:
    _page_heading(
        "Create your next quiz",
        "Set a learning goal, choose your source, and get a focused question set.",
        "QUIZ BUILDER",
    )
    has_api = _api_is_configured()
    if "generation_engine" not in st.session_state:
        st.session_state.generation_engine = "LLM (API)" if has_api else "Demo mode"

    mode = st.radio(
        "Choose your source",
        ["Topic-based", "Study materials"],
        horizontal=True,
        key="create_source",
    )
    st.markdown('<div style="height:.35rem"></div>', unsafe_allow_html=True)
    if has_api:
        st.markdown('<div class="qc-notice">LLM provider is configured. Your prompt and, when selected, retrieved notes are sent to the configured provider.</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="qc-notice">No LLM key is configured. Demo mode is ready to use; add a provider key in <code>.env</code> to enable custom AI generation.</div>',
            unsafe_allow_html=True,
        )

    engine = st.radio(
        "Generation engine",
        ["Demo mode", "LLM (API)"],
        horizontal=True,
        key="generation_engine",
    )
    if engine == "Demo mode":
        st.caption("Offline demo uses five curated sample questions per difficulty. It does not claim to use your topic or notes.")
    elif not has_api:
        st.caption("Configure LLM_API_KEY or GROQ_API_KEY in .env or Streamlit secrets to generate custom questions.")
    else:
        st.caption("Custom question generation uses the configured OpenAI-compatible provider.")

    mode_value = "Topic-Based" if mode == "Topic-based" else "Study-Material-Based"
    if mode_value == "Study-Material-Based":
        material = st.session_state.material_info
        if material:
            st.markdown(
                f'<div class="qc-card"><div class="qc-card-kicker">Indexed material</div>'
                f'<h3>{html.escape(material["source_name"])}</h3>'
                f'<p>{material["chunk_count"]} chunks · semantic retrieval ready</p></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="qc-notice">Index a PDF or paste notes in Study Materials before creating a document-grounded quiz.</div>',
                unsafe_allow_html=True,
            )
            if st.button("Open Study Materials", key="create_open_materials"):
                _navigate("Study Materials")

    with st.container(border=True):
        st.markdown("#### Quiz setup")
        with st.form("quiz_configuration"):
            topic = st.text_input(
                "Topic or learning focus",
                placeholder="e.g., Database normalization and functional dependencies",
            )
            control_a, control_b, control_c = st.columns([1, 1, 1.4])
            with control_a:
                difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"], index=1)
            with control_b:
                if engine == "Demo mode":
                    st.text_input("Question count", value="5 curated questions", disabled=True)
                    question_count = 5
                else:
                    question_count = st.selectbox("Question count", [5, 10, 15], index=0)
            with control_c:
                academic_level = st.selectbox(
                    "Academic level",
                    ["BTech CSE - beginner", "BTech CSE - intermediate", "BTech CSE - advanced"],
                    index=1,
                )
            custom_instructions = st.text_area(
                "Learning objective or custom instructions · optional",
                placeholder="e.g., Focus on normalization anomalies and real-world database design.",
                height=90,
            )
            submitted = st.form_submit_button("Generate quiz", type="primary", use_container_width=True)
    if submitted:
        if not topic.strip():
            st.error("Enter a topic or learning focus before generating a quiz.")
        elif mode_value == "Study-Material-Based" and engine == "Demo mode":
            st.error("Select LLM (API) to generate questions grounded in your indexed notes.")
        elif mode_value == "Study-Material-Based" and not st.session_state.collection_id:
            st.error("Index a PDF or pasted notes on the Study Materials page first.")
        elif engine == "LLM (API)" and not has_api:
            st.error("No API key is configured. Add a key to .env or Streamlit secrets, or switch to Demo mode.")
        else:
            with st.spinner("Retrieving relevant context and validating your quiz…"):
                _generate(
                    topic.strip(),
                    difficulty,
                    question_count,
                    academic_level,
                    mode_value,
                    engine,
                    custom_instructions=custom_instructions,
                )
            if st.session_state.page == "Take Quiz":
                st.rerun()

    st.markdown(
        '<div class="qc-card"><div class="qc-card-kicker">How generation works</div>'
        '<p>The system prompt defines the question schema. Your settings become the task prompt. '
        'For study materials, QuizCraft retrieves relevant local passages before generation. '
        'Each response is validated before it becomes an attempt.</p></div>',
        unsafe_allow_html=True,
    )

    with st.expander("Advanced · quiz-planning agent"):
        st.write("Describe your goal. The bounded agent extracts topic, count, and difficulty, optionally retrieves indexed notes, then invokes the same validated generator.")
        agent_engine = st.radio("Agent engine", ["Demo mode", "LLM (API)"], horizontal=True, key="agent_engine")
        with st.form("agent_request"):
            request = st.text_input("Your request", placeholder="Prepare 10 medium-level DBMS questions from my uploaded notes.")
            use_material = st.checkbox("Retrieve from my indexed study materials")
            agent_level = st.selectbox(
                "Academic level",
                ["BTech CSE - beginner", "BTech CSE - intermediate", "BTech CSE - advanced"],
                index=1,
                key="agent_level",
            )
            agent_submit = st.form_submit_button("Plan and generate")
        if agent_submit:
            if not request.strip():
                st.error("Describe the quiz you want.")
            elif use_material and not st.session_state.collection_id:
                st.error("Index study material before asking the agent to retrieve it.")
            elif agent_engine == "LLM (API)" and not has_api:
                st.error("Configure an API key to use the quiz-planning agent with LLM generation.")
            else:
                with st.spinner("Planning, retrieving if requested, and validating…"):
                    _run_agent(request, use_material, agent_engine, agent_level)
                if st.session_state.page == "Take Quiz":
                    st.rerun()


def _render_materials() -> None:
    _page_heading(
        "Study materials",
        "Add class notes once, then retrieve semantically relevant passages for document-grounded quizzes.",
        "YOUR KNOWLEDGE BASE",
    )
    if st.session_state.material_info:
        material = st.session_state.material_info
        st.markdown(
            f'<div class="qc-card"><div class="qc-card-kicker">Indexed · local vector store</div>'
            f'<h3>{html.escape(material["source_name"])}</h3>'
            f'<p>{material["chunk_count"]} chunks indexed with overlap · '
            f'{material["page_count"]} text pages · ready for semantic retrieval</p></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="qc-notice">Nothing indexed yet. Add a selectable-text PDF or paste notes below; scanned PDFs require OCR first.</div>',
            unsafe_allow_html=True,
        )

    upload_tab, retrieval_tab = st.tabs(["Add material", "Preview retrieval"])
    with upload_tab:
        st.markdown("#### Add a source")
        st.caption("Choose a PDF or paste notes for this indexing operation.")
        upload = st.file_uploader("Upload a PDF", type=["pdf"], help="Selectable text is extracted locally. Scanned PDFs require OCR.")
        notes = st.text_area(
            "Or paste study notes",
            height=180,
            placeholder="Paste lecture notes or a textbook excerpt here…",
        )
        if st.button("Index study material", type="primary", key="index_material"):
            if upload is not None and notes.strip():
                st.error("Choose either a PDF or pasted notes for each indexing operation.")
            elif upload is None and not notes.strip():
                st.error("Upload a PDF or paste some notes.")
            else:
                try:
                    page_count = 1
                    if upload is not None:
                        pages = extract_pdf_pages(upload.getvalue())
                        page_count = len(pages)
                        text = "\n\n".join(
                            f"[Page {page_number}]\n{page_text}"
                            for page_number, page_text in pages
                        )
                        sources = [f"PDF page {page_number}" for page_number, _ in pages]
                        source_name = upload.name
                    else:
                        text = notes.strip()
                        sources = ["Pasted notes"]
                        source_name = "Pasted notes"
                    chunks = chunk_text(text)
                    if not chunks:
                        st.error("No readable text was found. For scanned PDFs, run OCR first and paste the extracted text.")
                    else:
                        with st.spinner("Creating local embeddings and indexing passages…"):
                            collection_id = index_chunks(chunks, source_name)
                        st.session_state.collection_id = collection_id
                        st.session_state.material_sources = sources
                        st.session_state.material_info = {
                            "source_name": source_name,
                            "chunk_count": len(chunks),
                            "page_count": page_count,
                        }
                        st.session_state.last_retrieved = []
                        st.success(f"Indexed {len(chunks)} chunks from {source_name}.")
                        st.rerun()
                except (PDFProcessingError, RAGError, VectorStoreError) as exc:
                    logging.warning("Study material indexing failed: %s", exc)
                    st.error(str(exc))
        if st.session_state.material_info and st.button(
            "Generate quiz from materials", key="generate_from_materials"
        ):
            st.session_state.create_source = "Study materials"
            _navigate("Create Quiz")

    with retrieval_tab:
        st.markdown("#### Search indexed passages")
        query = st.text_input("Search your indexed material", placeholder="e.g., What does third normal form require?")
        if st.button("Retrieve relevant passages", key="retrieve_material"):
            if not st.session_state.collection_id:
                st.error("Index some study material first.")
            elif not query.strip():
                st.error("Enter a search topic or question.")
            else:
                try:
                    passages = retrieve_study_context(
                        st.session_state.collection_id, query.strip(), top_k=4
                    )
                    st.session_state.last_retrieved = passages
                    if not passages:
                        st.info("No passages matched that query. Try a broader search.")
                    for index, passage in enumerate(passages, start=1):
                        with st.expander(f"Passage {index} · {passage.get('source', 'Study material')}"):
                            st.write(passage["text"])
                            if passage.get("chunk_index") is not None:
                                st.caption(f"Chunk {passage['chunk_index'] + 1}")
                except (RAGError, VectorStoreError) as exc:
                    st.error(str(exc))

    st.markdown(
        '<div class="qc-card"><div class="qc-card-kicker">Retrieval-augmented generation</div>'
        '<p>PDF or pasted text → overlapping chunks → local embeddings in ChromaDB → '
        'top semantic matches → only the retrieved passages are sent to the LLM prompt. '
        'Uploaded material is not used in topic-only mode.</p></div>',
        unsafe_allow_html=True,
    )


def _save_quiz_answer(quiz_id: str, question_index: int) -> None:
    answer = st.session_state.get(f"answer_{quiz_id}_{question_index}")
    if answer:
        st.session_state.quiz_answers[str(question_index)] = answer


def _render_quiz() -> None:
    quiz = st.session_state.quiz
    if not quiz:
        _page_heading("No quiz in progress", "Create a topic quiz or prepare your study materials first.")
        if st.button("Create a quiz", type="primary"):
            _navigate("Create Quiz")
        return

    questions = quiz["questions"]
    index = min(st.session_state.quiz_index, len(questions) - 1)
    question = questions[index]
    answered = len(st.session_state.quiz_answers)
    st.markdown(
        f'<div class="qc-eyebrow">QUIZ SESSION · {html.escape(quiz["topic"])}</div>',
        unsafe_allow_html=True,
    )
    heading_left, heading_right = st.columns([3, 1], vertical_alignment="center")
    with heading_left:
        st.title(quiz["topic"])
        st.caption(f"{len(questions)} questions · {quiz['mode']} · {quiz['source']}")
    with heading_right:
        tone = "teal" if quiz["difficulty"] == "Easy" else "amber" if quiz["difficulty"] == "Hard" else ""
        st.markdown(
            f'<span class="qc-badge {tone}">{html.escape(quiz["difficulty"])} difficulty</span>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<div class="qc-section-head"><h3>Question {index + 1} of {len(questions)}</h3>'
        f'<span>{answered} answered</span></div>',
        unsafe_allow_html=True,
    )
    st.progress((index + 1) / len(questions))
    with st.container(border=True):
        st.markdown(f'<div class="qc-question-number">Question {index + 1:02d}</div>', unsafe_allow_html=True)
        st.markdown(f"### {question['question']}")
        current_answer = st.session_state.quiz_answers.get(str(index))
        selected = st.radio(
            "Choose one answer",
            question["options"],
            index=question["options"].index(current_answer) if current_answer in question["options"] else None,
            key=f"answer_{quiz['id']}_{index}",
            on_change=_save_quiz_answer,
            args=(quiz["id"], index),
            label_visibility="collapsed",
        )
    if selected:
        st.session_state.quiz_answers[str(index)] = selected

    previous_col, next_col, submit_col = st.columns([1, 1, 2])
    with previous_col:
        if st.button("← Previous", disabled=index == 0, use_container_width=True):
            st.session_state.quiz_index = index - 1
            st.rerun()
    with next_col:
        if st.button("Next →", disabled=index == len(questions) - 1, use_container_width=True):
            st.session_state.quiz_index = index + 1
            st.rerun()
    with submit_col:
        if st.button("Submit quiz", type="primary", use_container_width=True):
            missing = len(questions) - len(st.session_state.quiz_answers)
            if missing:
                st.warning(f"Answer all questions before submitting ({missing} unanswered).")
            else:
                result = calculate_score(questions, st.session_state.quiz_answers)
                result.update(
                    {
                        "quiz_id": quiz["id"],
                        "topic": quiz["topic"],
                        "difficulty": quiz["difficulty"],
                        "mode": quiz["mode"],
                        "source": quiz["source"],
                        "completed_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                st.session_state.quiz_result = result
                st.session_state.history.insert(0, {"quiz": quiz, "result": result})
                st.session_state.review_quiz_id = quiz["id"]
                _navigate("My Quizzes")


def _render_result_review(item: dict) -> None:
    quiz = item["quiz"]
    result = item["result"]
    score_col, summary_col = st.columns([1, 2.5], vertical_alignment="center")
    with score_col:
        st.markdown(
            f'<div class="qc-result-score"><strong>{result["percentage"]:.0f}%</strong>'
            f'<span>{result["correct"]} of {result["total"]} correct</span></div>',
            unsafe_allow_html=True,
        )
    with summary_col:
        st.markdown(f"**{html.escape(result['summary'])}**")
        st.caption(
            f"{result['correct']} correct · {result['incorrect']} incorrect · "
            f"{result['completed_at']}"
        )
    score_cols = st.columns(3)
    with score_cols[0]:
        st.metric("Questions", result["total"])
    with score_cols[1]:
        st.metric("Correct", result["correct"])
    with score_cols[2]:
        st.metric("Incorrect", result["incorrect"])

    if quiz.get("agent_note"):
        st.markdown(f'<div class="qc-notice">{html.escape(quiz["agent_note"])}</div>', unsafe_allow_html=True)
    active_quiz = st.session_state.quiz
    if active_quiz and quiz["id"] == active_quiz["id"]:
        if st.session_state.last_retrieved:
            with st.expander("Study passages used"):
                for passage in st.session_state.last_retrieved:
                    st.markdown(f"**{html.escape(passage.get('source', 'Study material'))}**")
                    st.write(passage["text"])

    _section_heading("Answer review", "Correct answers and explanations")
    for number, (question, review) in enumerate(zip(quiz["questions"], result["review"]), start=1):
        status = "Correct" if review["is_correct"] else "Review"
        with st.expander(f"{number:02d} · {status} · {question['question']}"):
            answer_col, correct_col = st.columns(2)
            with answer_col:
                st.markdown("**Your answer**")
                st.write(review["your_answer"] or "Not answered")
            with correct_col:
                st.markdown("**Correct answer**")
                st.write(question["correct_answer"])
            st.markdown("**Explanation**")
            st.write(question["explanation"])

    download_col, retry_col = st.columns([1, 1])
    with download_col:
        st.download_button(
            "Download quiz JSON",
            data=json.dumps(quiz, indent=2, ensure_ascii=False),
            file_name=f"quizcraft-{quiz['id']}.json",
            mime="application/json",
            use_container_width=True,
        )
        st.download_button(
            "Download results JSON",
            data=json.dumps(result, indent=2, ensure_ascii=False),
            file_name=f"quizcraft-results-{result['quiz_id']}.json",
            mime="application/json",
            use_container_width=True,
        )
    with retry_col:
        if st.button("Try this quiz again", key=f"retake_{quiz['id']}", use_container_width=True):
            _new_quiz(
                quiz["questions"],
                quiz["topic"],
                quiz["difficulty"],
                quiz["mode"],
                quiz["source"],
                quiz.get("agent_note"),
            )
            st.rerun()
        if st.button("Create another quiz", key=f"another_{quiz['id']}", type="primary", use_container_width=True):
            _navigate("Create Quiz")


def _quiz_history_row(item: dict, key_prefix: str) -> None:
    quiz = item["quiz"]
    result = item["result"]
    with st.container(border=True):
        detail_col, difficulty_col, score_col, action_col = st.columns([2.8, 1, 1, 1.6], vertical_alignment="center")
        with detail_col:
            st.markdown(f"**{html.escape(quiz['topic'])}**")
            st.caption(f"{len(quiz['questions'])} questions · {result['completed_at']}")
        with difficulty_col:
            st.markdown(f'<span class="qc-badge">{html.escape(quiz["difficulty"])}</span>', unsafe_allow_html=True)
        with score_col:
            st.markdown(f"**{result['percentage']:.0f}%**")
            st.caption(f"{result['correct']}/{result['total']} correct")
        with action_col:
            review_col, retake_col = st.columns(2)
            with review_col:
                if st.button("Review", key=f"{key_prefix}_review_{quiz['id']}", use_container_width=True):
                    st.session_state.review_quiz_id = quiz["id"]
                    _navigate("My Quizzes")
            with retake_col:
                if st.button("Retake", key=f"{key_prefix}_retake_{quiz['id']}", use_container_width=True):
                    _new_quiz(
                        quiz["questions"],
                        quiz["topic"],
                        quiz["difficulty"],
                        quiz["mode"],
                        quiz["source"],
                        quiz.get("agent_note"),
                    )
                    st.rerun()


def _render_my_quizzes() -> None:
    _page_heading(
        "My quizzes",
        "Your completed practice sessions, scores, reviews, and retake options.",
        "SESSION LIBRARY",
    )
    if st.session_state.quiz and not st.session_state.quiz_result:
        quiz = st.session_state.quiz
        with st.container(border=True):
            resume_col, info_col = st.columns([1, 4], vertical_alignment="center")
            with resume_col:
                if st.button("Resume quiz", key="history_resume", type="primary", use_container_width=True):
                    st.session_state.page = "Take Quiz"
                    st.rerun()
            with info_col:
                st.markdown(f"**In progress · {html.escape(quiz['topic'])}**")
                st.caption(f"{len(quiz['questions'])} questions · {len(st.session_state.quiz_answers)} answered")

    if not st.session_state.history:
        _empty_state("No completed quizzes yet", "Create a quiz, complete it, and your results will be saved here for this session.")
        if st.button("Create your first quiz", type="primary", key="empty_history_create"):
            _navigate("Create Quiz")
        return

    review_id = st.session_state.review_quiz_id
    selected_item = next(
        (item for item in st.session_state.history if item["quiz"]["id"] == review_id),
        st.session_state.history[0],
    )
    _section_heading("Selected result", selected_item["quiz"]["topic"])
    _render_result_review(selected_item)

    _section_heading("All completed quizzes", f"{len(st.session_state.history)} in this session")
    for item in st.session_state.history:
        _quiz_history_row(item, "history")


def _render_performance() -> None:
    _page_heading(
        "Your performance",
        "A clear view of your practice progress, based only on completed quizzes in this session.",
        "LEARNING INSIGHTS",
    )
    history = st.session_state.history
    if not history:
        _empty_state("Your performance starts with your first quiz", "Complete a quiz to see your actual scores and learning trends.")
        if st.button("Create a quiz", type="primary", key="performance_empty_create"):
            _navigate("Create Quiz")
        return

    attempts = len(history)
    questions = sum(item["result"]["total"] for item in history)
    average = sum(item["result"]["percentage"] for item in history) / attempts
    highest = max(item["result"]["percentage"] for item in history)
    cols = st.columns(4)
    with cols[0]:
        _stat_card("Completed quizzes", str(attempts), "This session")
    with cols[1]:
        _stat_card("Questions answered", str(questions), "Completed attempts")
    with cols[2]:
        _stat_card("Average score", f"{average:.0f}%", "Across completed quizzes")
    with cols[3]:
        _stat_card("Personal best", f"{highest:.0f}%", "Highest quiz score")

    _section_heading("Score trend", "Each point represents one completed quiz")
    scores = [item["result"]["percentage"] for item in reversed(history)]
    st.bar_chart(
        {
            "Attempt": list(range(1, len(scores) + 1)),
            "Score (%)": scores,
        },
        x="Attempt",
        y="Score (%)",
        color="#3648a8",
        height=270,
    )

    topic_scores: dict[str, list[float]] = {}
    difficulty_scores: dict[str, list[float]] = {}
    for item in history:
        result = item["result"]
        topic_scores.setdefault(result["topic"], []).append(result["percentage"])
        difficulty_scores.setdefault(result["difficulty"], []).append(result["percentage"])
    if len(topic_scores) > 1:
        _section_heading("Average by topic", "Topics with completed attempts")
        st.bar_chart(
            {
                "Topic": list(topic_scores),
                "Average score (%)": [sum(values) / len(values) for values in topic_scores.values()],
            },
            x="Topic",
            y="Average score (%)",
            color="#168a83",
            height=250,
        )
    if len(difficulty_scores) > 1:
        _section_heading("Average by difficulty", "Only completed attempts are included")
        st.bar_chart(
            {
                "Difficulty": list(difficulty_scores),
                "Average score (%)": [
                    sum(values) / len(values) for values in difficulty_scores.values()
                ],
            },
            x="Difficulty",
            y="Average score (%)",
            color="#168a83",
            height=250,
        )
    st.caption("Analytics are held in Streamlit session state and reset when the session ends.")


def _render_app() -> None:
    _render_navigation()
    page = st.session_state.page
    if page == "Dashboard":
        _render_dashboard()
    elif page == "Create Quiz":
        _render_create()
    elif page == "Study Materials":
        _render_materials()
    elif page == "Take Quiz":
        _render_quiz()
    elif page == "My Quizzes":
        _render_my_quizzes()
    else:
        _render_performance()


_render_app()
