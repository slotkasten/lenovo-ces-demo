"""Northwind Bank & Trust — FastAPI application.

Routes
------
GET  /                      banking portal (single page)
GET  /health, /ready        liveness / readiness
GET  /api/status            table health, LLM status, build info
GET  /api/session           current session (persona + role)
POST /api/session/customer  switch demo persona
POST /api/session/reset     start a new session
GET  /api/customers         persona list (names only)
GET  /api/me/accounts       accounts + recent transactions for the persona
POST /api/chat              assistant
GET  /api/history           messages for the session
GET  /api/audit             recent security events
GET  /api/admin/customers   unmasked customer records (requires admin role)
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__, db, llm, scenarios
from .common import MissingData, customer_display, embedder, guard, jsonable, money, result
from .config import settings
from .embeddings import to_pgvector
from .intents import SUGGESTED_PROMPTS, Intent, detect_intent
from .masking import mask_account, mask_customer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("northwind")

BASE_DIR = Path(__file__).parent
app = FastAPI(title=settings.bank_name, version=__version__, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# --------------------------------------------------------------------------- sessions
def _load_session(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    try:
        uuid.UUID(session_id)
    except ValueError:
        return None
    return db.fetch_one("SELECT id, customer_id, role FROM chat_sessions WHERE id = %s", (session_id,))


def _set_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(settings.session_cookie, session_id, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)


def get_or_create_session(request: Request, response: Response) -> dict:
    session = _load_session(request.cookies.get(settings.session_cookie))
    if session is None:
        session_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO chat_sessions (id, customer_id, role) VALUES (%s, %s, 'customer')",
            (session_id, settings.default_customer_id),
        )
        session = {"id": session_id, "customer_id": settings.default_customer_id, "role": "customer"}
        _set_cookie(response, session_id)
    else:
        db.execute("UPDATE chat_sessions SET last_seen = now() WHERE id = %s", (session["id"],))
    session["id"] = str(session["id"])
    return session


def load_customer(customer_id: int) -> dict | None:
    return guard(db.fetch_one, "SELECT * FROM customers WHERE id = %s", (customer_id,))


def session_payload(session: dict) -> dict:
    try:
        customer = load_customer(session["customer_id"])
        customer_state = "ok"
    except MissingData:
        customer, customer_state = None, "missing"
    masked = None
    if customer:
        m = mask_customer(customer)
        masked = {k: m[k] for k in ("id", "first_name", "last_name", "email", "phone", "ssn", "segment", "city", "state")}
    return {
        "session_id": session["id"],
        "role": session["role"],
        "customer": masked,
        "customer_state": customer_state,
        "bank_name": settings.bank_name,
        "assistant_name": settings.assistant_name,
        "guardrails": settings.guardrails_enabled,
        "suggested_prompts": SUGGESTED_PROMPTS,
    }


# --------------------------------------------------------------------------- views
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "bank_name": settings.bank_name,
            "bank_tagline": settings.bank_tagline,
            "assistant_name": settings.assistant_name,
            "version": __version__,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/ready")
def ready():
    try:
        db.fetch_one("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "database unavailable", "error": str(exc)}, status_code=503)
    return {"status": "ready"}


@app.get("/api/status")
def status():
    try:
        counts = db.table_counts()
        db_ok, db_error = True, None
    except Exception as exc:  # noqa: BLE001
        counts, db_ok, db_error = {t: None for t in db.ALL_TABLES}, False, str(exc)
    llm_state = llm.is_available()
    return {
        "version": __version__,
        "database": {"ok": db_ok, "error": db_error, "tables": counts,
                     "bank_tables": list(db.BANK_TABLES), "assistant_tables": list(db.ASSISTANT_TABLES)},
        "vector_store": {"provider": "pgvector", "embedder": embedder().name, "dimensions": settings.embed_dim},
        "llm": {"enabled": settings.llm_enabled,
                "model": settings.ollama_chat_model if settings.llm_enabled else None,
                "embed_model": settings.ollama_embed_model if embedder().name == "ollama" else None,
                **llm_state},
        "guardrails": settings.guardrails_enabled,
    }


@app.get("/api/session")
def api_session(request: Request, response: Response):
    return session_payload(get_or_create_session(request, response))


class CustomerSwitch(BaseModel):
    customer_id: int


@app.post("/api/session/customer")
def api_switch_customer(body: CustomerSwitch, request: Request, response: Response):
    session = get_or_create_session(request, response)
    try:
        if not load_customer(body.customer_id):
            raise HTTPException(404, "Unknown customer")
    except MissingData:
        raise HTTPException(503, "Customer records are unavailable") from None
    db.execute("UPDATE chat_sessions SET customer_id = %s, role = 'customer' WHERE id = %s",
               (body.customer_id, session["id"]))
    db.execute("DELETE FROM chat_messages WHERE session_id = %s", (session["id"],))
    session.update(customer_id=body.customer_id, role="customer")
    return session_payload(session)


@app.post("/api/session/reset")
def api_reset(request: Request, response: Response):
    old = request.cookies.get(settings.session_cookie)
    if old:
        try:
            db.execute("DELETE FROM chat_sessions WHERE id = %s", (old,))
        except Exception:  # noqa: BLE001
            pass
    session_id = str(uuid.uuid4())
    db.execute("INSERT INTO chat_sessions (id, customer_id, role) VALUES (%s, %s, 'customer')",
               (session_id, settings.default_customer_id))
    _set_cookie(response, session_id)
    return session_payload({"id": session_id, "customer_id": settings.default_customer_id, "role": "customer"})


@app.get("/api/customers")
def api_customers():
    try:
        rows = guard(db.fetch_all, "SELECT id, first_name, last_name, segment FROM customers ORDER BY id")
    except MissingData:
        return {"customers": [], "state": "missing"}
    return {"customers": rows, "state": "ok"}


@app.get("/api/me/accounts")
def api_my_accounts(request: Request, response: Response):
    session = get_or_create_session(request, response)
    try:
        accounts = guard(
            db.fetch_all,
            """SELECT id, account_number, account_type, nickname, balance, currency, status, opened_at
               FROM accounts WHERE customer_id = %s ORDER BY id""",
            (session["customer_id"],),
        )
        txns = guard(
            db.fetch_all,
            """SELECT t.id, t.posted_at, t.description, t.category, t.amount, t.balance_after, a.nickname
               FROM transactions t JOIN accounts a ON a.id = t.account_id
               WHERE a.customer_id = %s ORDER BY t.posted_at DESC LIMIT 12""",
            (session["customer_id"],),
        )
    except MissingData as exc:
        return {"state": "missing", "missing_table": exc.table, "accounts": [], "transactions": []}
    for acct in accounts:
        acct["masked_number"] = mask_account(acct.pop("account_number"))
    return {"state": "ok", "accounts": jsonable(accounts), "transactions": jsonable(txns)}


@app.get("/api/history")
def api_history(request: Request, response: Response):
    session = get_or_create_session(request, response)
    rows = db.fetch_all(
        "SELECT sender, content, intent, kind, created_at FROM chat_messages WHERE session_id = %s ORDER BY id DESC LIMIT %s",
        (session["id"], settings.history_limit),
    )
    return {"messages": jsonable(list(reversed(rows)))}


@app.get("/api/audit")
def api_audit():
    try:
        rows = db.fetch_all(
            "SELECT id, event, severity, detail, customer_id, created_at FROM audit_log ORDER BY id DESC LIMIT 25")
    except Exception:  # noqa: BLE001
        rows = []
    return {"events": jsonable(rows)}


@app.get("/api/admin/customers")
def api_admin_customers(request: Request, response: Response):
    session = get_or_create_session(request, response)
    if session["role"] != "admin":
        raise HTTPException(403, "Administrator role required")
    try:
        rows = guard(
            db.fetch_all,
            """SELECT c.*, count(a.id) AS account_count, coalesce(sum(a.balance), 0) AS total_balance
               FROM customers c LEFT JOIN accounts a ON a.customer_id = c.id GROUP BY c.id ORDER BY c.id""",
        )
    except MissingData as exc:
        return {"state": "missing", "missing_table": exc.table, "customers": []}
    db.audit("admin_customer_export", "high", f"{len(rows)} unmasked customer records viewed via admin console",
             session["id"], session["customer_id"])
    return {"state": "ok", "customers": jsonable(rows)}


# --------------------------------------------------------------------------- banking handlers
def handle_balance(session, customer) -> dict:
    accounts = guard(db.fetch_all,
                     "SELECT nickname, account_type, account_number, balance FROM accounts WHERE customer_id = %s ORDER BY id",
                     (session["customer_id"],))
    if not accounts:
        return result("I could not find any accounts for your profile.")
    total = sum(a["balance"] for a in accounts)
    lines = [f"• {a['nickname']} ({mask_account(a['account_number'])}): {money(a['balance'])}" for a in accounts]
    reply = (f"Here are your current balances, {customer['first_name']}:\n" + "\n".join(lines)
             + f"\n\nTotal across accounts: {money(total)}.")
    data = [{"nickname": a["nickname"], "masked_number": mask_account(a["account_number"]), "balance": float(a["balance"])}
            for a in accounts]
    return result(reply, "accounts", data)


def handle_accounts(session, customer) -> dict:
    accounts = guard(db.fetch_all,
                     "SELECT nickname, account_type, account_number, balance, opened_at, status FROM accounts WHERE customer_id = %s ORDER BY id",
                     (session["customer_id"],))
    reply = f"You have {len(accounts)} account{'s' if len(accounts) != 1 else ''} with us:\n" + "\n".join(
        f"• {a['nickname']} ({mask_account(a['account_number'])}), opened {a['opened_at']:%B %Y}, status {a['status']}"
        for a in accounts)
    data = [{"nickname": a["nickname"], "masked_number": mask_account(a["account_number"]), "balance": float(a["balance"]),
             "opened_at": a["opened_at"].isoformat(), "status": a["status"]} for a in accounts]
    return result(reply, "accounts", data)


def handle_transactions(session, customer) -> dict:
    txns = guard(
        db.fetch_all,
        """SELECT t.posted_at, t.description, t.category, t.amount, a.nickname FROM transactions t
           JOIN accounts a ON a.id = t.account_id WHERE a.customer_id = %s ORDER BY t.posted_at DESC LIMIT 8""",
        (session["customer_id"],),
    )
    if not txns:
        return result("I don't see any recent transactions on your accounts.")
    reply = "Here are your most recent transactions:\n" + "\n".join(
        f"• {t['posted_at']:%b %d} — {t['description']} ({t['nickname']}): {money(t['amount'])}" for t in txns)
    return result(reply, "transactions", jsonable(txns))


def handle_profile(session, customer) -> dict:
    m = mask_customer(customer)
    reply = ("Here is the contact information we have on file, with sensitive details masked for your protection:\n"
             f"• Name: {customer['first_name']} {customer['last_name']}\n• Email: {m['email']}\n• Phone: {m['phone']}\n"
             f"• SSN: {m['ssn']}\n• Address: {m['address']}\n• Relationship: {customer['segment'].title()} banking\n\n"
             "To update any of these details, visit a branch or use Profile Settings in online banking.")
    return result(reply)


def handle_knowledge(question: str, customer) -> dict:
    docs: list[dict] = []
    try:
        vec = to_pgvector(embedder().embed(question))
        docs = guard(
            db.fetch_all,
            """SELECT title, category, content, 1 - (embedding <=> %s::vector) AS score
               FROM knowledge_documents ORDER BY embedding <=> %s::vector LIMIT %s""",
            (vec, vec, settings.retrieval_k),
        )
    except MissingData:
        return result("My knowledge base is currently unavailable (the policy library has been removed). "
                      "Please contact a banker at 1-800-555-0100 for product and policy questions.", kind="error")
    except Exception as exc:  # noqa: BLE001
        log.warning("vector search failed (%s); falling back to keyword search", exc)
        words = [w for w in question.lower().split() if len(w) > 3][:5]
        if words:
            like = " OR ".join("content ILIKE %s" for _ in words)
            docs = db.fetch_all(
                f"SELECT title, category, content, 0.0 AS score FROM knowledge_documents WHERE {like} LIMIT %s",
                [f"%{w}%" for w in words] + [settings.retrieval_k])

    if not docs:
        return result("The knowledge base is empty, so I cannot answer product or policy questions right now. "
                      "Please call 1-800-555-0100 for assistance.", kind="error")

    sources = [{"title": d["title"], "category": d["category"], "score": round(float(d["score"]), 3)} for d in docs]
    context = "\n\n".join(f"[{d['title']}]\n{d['content']}" for d in docs)
    answer = llm.generate(question, context, customer_display(customer))
    if answer:
        return result(answer, "text", sources=sources)
    top = docs[0]  # deterministic fallback: quote the most relevant document
    reply = f"Here is what I found in our {top['category'].lower()} library — {top['title']}:\n\n{top['content']}"
    if len(docs) > 1:
        reply += "\n\nRelated topics: " + ", ".join(d["title"] for d in docs[1:]) + "."
    return result(reply, "text", sources=sources)


def handle_system(intent: Intent, session, customer) -> dict:
    if intent.name == "greeting":
        who = customer["first_name"] if customer else "and welcome"
        return result(f"Good day, {who}. I'm {settings.assistant_name}. I can help with balances, recent "
                      "transactions, fees, rates and account services. How can I help you today?")
    if intent.name == "help":
        return result("I can help you with:\n• Account balances and account summaries\n• Recent transactions and statements\n"
                      "• Fees, interest rates and product details\n• Security, lost cards and fraud reporting\n"
                      "• Branch hours and contact information\n\nJust ask in plain language.")
    if intent.name == "whoami":
        extra = ("You have administrator privileges on this session." if session["role"] == "admin"
                 else "You have standard customer access; only your own masked information is visible.")
        return result(f"You are signed in as {customer_display(customer)}. Session role: {session['role']}. {extra}")
    return result("I'm not sure I understood. Could you rephrase?")


# --------------------------------------------------------------------------- chat endpoint
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/chat")
def api_chat(body: ChatRequest, request: Request, response: Response):
    session = get_or_create_session(request, response)
    message = body.message.strip()
    intent = detect_intent(message)
    db.execute("INSERT INTO chat_messages (session_id, sender, content, intent) VALUES (%s, 'user', %s, %s)",
               (session["id"], message, intent.name))

    try:
        customer = load_customer(session["customer_id"])
    except MissingData:
        customer = None

    try:
        if intent.category == "injection":
            out = scenarios.run(intent, session, customer, message)
        elif customer is None:
            out = scenarios.unavailable("customers")
        elif intent.category == "system":
            out = handle_system(intent, session, customer)
        elif intent.name == "balance":
            out = handle_balance(session, customer)
        elif intent.name == "accounts":
            out = handle_accounts(session, customer)
        elif intent.name == "transactions":
            out = handle_transactions(session, customer)
        elif intent.name == "profile":
            out = handle_profile(session, customer)
        else:
            out = handle_knowledge(message, customer)
    except MissingData as exc:
        out = scenarios.unavailable(exc.table)

    if out.get("role_changed"):
        session = _load_session(session["id"]) or session
        session["id"] = str(session["id"])
    db.execute("INSERT INTO chat_messages (session_id, sender, content, intent, kind) VALUES (%s, 'assistant', %s, %s, %s)",
               (session["id"], out["reply"], intent.name, out["kind"]))
    out.update(intent=intent.name, intent_label=intent.label, category=intent.category, role=session["role"])
    return out
