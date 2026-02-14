"""
Flask web server for PrintWorks SAP CRUD Chatbot
Wraps Chatbot V3 (full O2C + P2P with read/write) with a browser-based UI
Uses Together AI (DeepSeek) to refine natural language when the rule-based parser can't understand.
"""

import sys
import os
import re
import io
import importlib.util
import requests
from datetime import datetime, timedelta

# Import "Chatbot V3.py" from parent directory (has a space in the filename)
_parent = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("chatbot_v3", os.path.join(_parent, "Chatbot V3.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Import BAPI module
_bapi_spec = importlib.util.spec_from_file_location("bapi_demo", os.path.join(_parent, "BAPI", "bapi_demo.py"))
_bapi_mod = importlib.util.module_from_spec(_bapi_spec)
_bapi_spec.loader.exec_module(_bapi_mod)

BAPI_PO_CREATE1 = _bapi_mod.BAPI_PO_CREATE1
BAPI_SALESORDER_CREATEFROMDAT2 = _bapi_mod.BAPI_SALESORDER_CREATEFROMDAT2
BAPI_GOODSMVT_CREATE = _bapi_mod.BAPI_GOODSMVT_CREATE
BAPI_INCOMINGINVOICE_CREATE = _bapi_mod.BAPI_INCOMINGINVOICE_CREATE
BAPI_ACC_DOCUMENT_POST = _bapi_mod.BAPI_ACC_DOCUMENT_POST
demo_full_p2p_cycle = _bapi_mod.demo_full_p2p_cycle
demo_full_o2c_cycle = _bapi_mod.demo_full_o2c_cycle
BAPI_PO_RELEASE = _bapi_mod.BAPI_PO_RELEASE
P2P_STEPS = _bapi_mod.P2P_STEPS
run_p2p_step = _bapi_mod.run_p2p_step
run_p2p_remaining_steps = _bapi_mod.run_p2p_remaining_steps

parse_and_execute = _mod.parse_and_execute
preview_create_po = _mod.preview_create_po
confirm_create_po = _mod.confirm_create_po
show_dashboard = _mod.show_dashboard
get_connection = _mod.get_connection
query = _mod.query
execute = _mod.execute
fmt_table = _mod.fmt_table
fmt_currency = _mod.fmt_currency
S = _mod.S
HELP_TEXT = _mod.HELP_TEXT
TODAY = datetime.now().strftime("%Y-%m-%d")

# Pending PO confirmation state (single-user; for multi-user use Flask session)
_pending_po = {"data": None, "mode": None}

# P2P interactive flow state
_p2p_flow = {"active": False, "step_index": 0, "ctx": {}, "auto_mode": False}

from dotenv import load_dotenv
load_dotenv(os.path.join(_parent, ".env"))

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")

from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# The fallback message from Chatbot V3 when it can't parse
FALLBACK_MSG = "I didn't understand that."
# The empty-results message from fmt_table() in Chatbot V3
NO_RESULTS_MSG = "(No results found)"

REFINE_PROMPT = """You are a command translator for a SAP business chatbot. The user typed a natural language message that the chatbot didn't understand. Your job is to rewrite it into a short command the chatbot CAN understand.

Available commands (pick the closest match):
READ:
  dashboard | summary | overview
  orders | open orders | completed orders | orders for [customer] | equipment orders
  order [10-digit number]          — order detail
  deliveries | open deliveries | deliveries for [customer]
  invoices | overdue invoices | open invoices | paid invoices
  revenue by region | revenue by customer | revenue by product | revenue by quarter
  customers | indian customers | pharma customers
  purchase orders | open purchase orders | approved purchase orders
  po [10-digit number]             — PO detail
  vendors | vendor invoices | open vendor invoices
  goods receipts
  procurement materials
  procurement spend by vendor | procurement spend by plant
  products
  materials                        — full product catalog
  plants                           — manufacturing/warehouse locations
  check vendor [name]              — look up vendor by name (details, city, country, PO stats)
  check material [name]            — look up procurement material by name (ID, category, price)
  po status [number]               — full PO lifecycle (items, GR, invoice, progress)
  order status [number]            — full sales order lifecycle (items, delivery, invoice, progress)
  overdue purchase orders          — POs past delivery date still open/approved
  pending actions                  — counts of items needing attention across the business

WRITE:
  process order [number]
  record payment [number]
  record payment [number] partial
  ship delivery [number]
  confirm delivery [number]
  approve po [number]
  receive goods [number]
  pay vendor [number]
  close po [number]
  create po for [vendor] - [material] - [quantity] (optional: - price [amt] - date [YYYY-MM-DD] - plant [code])
  create order for [customer] - [product] - [quantity]
  create vendor invoice [PO number]  — creates invoice after PO + GR check (also: create supplier invoice)

Natural language mapping examples (rewrite these patterns):
  "is vendor X available" / "do we have vendor X" / "vendor X details" / "who is vendor X" / "tell me about vendor X" → check vendor X
  "is material X available" / "do we have material X" / "find X material" / "X price" / "cost of X" → check material X
  "what is the status of PO X" / "where is PO X" / "track PO X" / "PO X lifecycle" / "PO X progress" → po status X
  "what is the status of order X" / "where is order X" / "track order X" / "order X progress" → order status X
  "any late POs" / "delayed purchase orders" / "which POs are overdue" / "late deliveries from vendors" → overdue purchase orders
  "what needs attention" / "what's pending" / "action items" / "what should I do" / "to do list" → pending actions
  "run p2p" / "p2p flow" / "start p2p" / "procure to pay" / "full p2p cycle" / "procurement cycle" / "run full cycle" → run p2p cycle
  "run o2c" / "o2c flow" / "order to cash" / "full o2c cycle" / "sales cycle" → run o2c cycle

Rules:
- Output ONLY the rewritten command, nothing else
- Keep document numbers exactly as the user typed them
- Keep names exactly as the user typed them
- If the user is asking something completely unrelated to SAP/business, output exactly: UNRELATED
- Do NOT add explanations"""


QA_PROMPT = """You are a helpful SAP business assistant for PrintWorks Global Solutions, a printing equipment company.
Answer the user's question concisely and clearly. Keep answers short (3-5 sentences max).
You can answer questions about:
- SAP concepts (PO, SO, GR, invoices, O2C, P2P, BAPI, etc.)
- Business processes (procure-to-pay, order-to-cash, 3-way match, etc.)
- PrintWorks context (printing equipment, inks, substrates, consumables)
- General business/ERP questions

If the question is completely unrelated to business/SAP, politely redirect them.
Do NOT use markdown formatting. Use plain text only."""


def _call_deepseek(messages, max_tokens=80, temperature=0.1, timeout=10):
    """Shared helper to call DeepSeek-V3 via Together AI."""
    resp = requests.post(
        "https://api.together.xyz/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {TOGETHER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def refine_with_llm(user_input):
    """Use DeepSeek to rephrase natural language into a chatbot command."""
    if not TOGETHER_API_KEY:
        return None
    try:
        refined = _call_deepseek([
            {"role": "system", "content": REFINE_PROMPT},
            {"role": "user", "content": user_input},
        ])
        if refined and refined != "UNRELATED" and refined.lower() != user_input.lower():
            return refined
    except Exception:
        pass
    return None


def answer_with_llm(user_input):
    """Use DeepSeek to directly answer general questions."""
    if not TOGETHER_API_KEY:
        return None
    try:
        answer = _call_deepseek(
            [
                {"role": "system", "content": QA_PROMPT},
                {"role": "user", "content": user_input},
            ],
            max_tokens=300,
            temperature=0.3,
            timeout=15,
        )
        return answer if answer else None
    except Exception:
        pass
    return None


# ── Helper: case-insensitive dict key access (HANA returns UPPERCASE) ──

def _get(row, key):
    """Get value from dict trying lowercase, UPPERCASE, and original key."""
    return row.get(key) or row.get(key.upper()) or row.get(key.lower(), 0)


def app_show_dashboard():
    """Fixed dashboard that handles HANA's uppercase column names."""
    o2c = query(f"""SELECT
        SUM(CASE WHEN "STATUS" = 'A' THEN 1 ELSE 0 END) AS open_orders,
        SUM(CASE WHEN "STATUS" = 'B' THEN 1 ELSE 0 END) AS processing,
        SUM(CASE WHEN "STATUS" = 'C' THEN 1 ELSE 0 END) AS completed,
        COUNT(*) AS total
    FROM "{S}"."SALES_ORDERS" """)

    dlv = query(f"""SELECT
        SUM(CASE WHEN "GI_STATUS" = 'A' THEN 1 ELSE 0 END) AS open_dlv,
        SUM(CASE WHEN "GI_STATUS" = 'B' THEN 1 ELSE 0 END) AS in_transit,
        SUM(CASE WHEN "GI_STATUS" = 'C' THEN 1 ELSE 0 END) AS delivered
    FROM "{S}"."DELIVERIES" """)

    ar = query(f"""SELECT "PAY_STATUS", COUNT(*) AS cnt, SUM("TOTAL") AS total_val
    FROM "{S}"."INVOICES" GROUP BY "PAY_STATUS" """)

    p2p = query(f"""SELECT "STATUS", COUNT(*) AS cnt, SUM("NETWR") AS total_val
    FROM "{S}"."PURCHASE_ORDERS" GROUP BY "STATUS" """)

    ap = query(f"""SELECT "PAY_STATUS", COUNT(*) AS cnt, SUM("TOTAL") AS total_val
    FROM "{S}"."VENDOR_INVOICES" GROUP BY "PAY_STATUS" """)

    lines = [
        "\u2550" * 60,
        "\U0001f4ca PRINTWORKS GLOBAL SOLUTIONS \u2014 BUSINESS DASHBOARD",
        "\u2550" * 60,
        "",
        "\U0001f4e6 ORDER-TO-CASH (O2C):",
    ]
    if o2c and not isinstance(o2c, dict):
        r = o2c[0]
        lines.append(
            f"   Sales Orders:  {_get(r, 'open_orders')} Open | {_get(r, 'processing')} Processing | {_get(r, 'completed')} Completed | {_get(r, 'total')} Total")
    if dlv and not isinstance(dlv, dict):
        r = dlv[0]
        lines.append(
            f"   Deliveries:    {_get(r, 'open_dlv')} Open | {_get(r, 'in_transit')} In Transit | {_get(r, 'delivered')} Delivered")

    lines.append(f"\n   Customer Invoices (AR):")
    if ar and not isinstance(ar, dict):
        for r in ar:
            lines.append(f"     {_get(r, 'PAY_STATUS'):10s}: {_get(r, 'cnt'):>5,} invoices")

    lines.append(f"\n\U0001f6d2 PROCURE-TO-PAY (P2P):")
    if p2p and not isinstance(p2p, dict):
        for r in p2p:
            lines.append(f"   PO {_get(r, 'STATUS'):20s}: {_get(r, 'cnt'):>5,} orders")

    lines.append(f"\n   Vendor Invoices (AP):")
    if ap and not isinstance(ap, dict):
        for r in ap:
            lines.append(f"     {_get(r, 'PAY_STATUS'):10s}: {_get(r, 'cnt'):>5,} invoices")

    return "\n".join(lines)


# ── Extra queries for tables not covered by Chatbot V3 ──

def query_materials():
    """Full product/materials catalog (MATERIALS table)."""
    sql = f"""SELECT "MATNR" AS material_id, "MAKTX" AS description, "MATKL" AS category,
               "SPART" AS division, "MEINS" AS uom, "BASE_PRICE_USD" AS price_usd
        FROM "{S}"."MATERIALS" ORDER BY "MATKL", "MATNR" """
    return fmt_table(query(sql), max_rows=35)


def query_plants():
    """All manufacturing/warehouse plants (PLANTS table)."""
    sql = f"""SELECT "WERKS" AS plant_id, "name" AS plant_name
        FROM "{S}"."PLANTS" ORDER BY "WERKS" """
    return fmt_table(query(sql))


def check_vendor(name):
    """Look up a vendor by name and show details + PO stats."""
    rows = query(f"""SELECT v."LIFNR", v."NAME1", v."ORT01", v."LAND1", v."BRSCH",
            COUNT(po."EBELN") AS total_pos,
            COALESCE(SUM(po."NETWR"), 0) AS total_value
        FROM "{S}"."VENDORS" v
        LEFT JOIN "{S}"."PURCHASE_ORDERS" po ON v."LIFNR" = po."LIFNR"
        WHERE UPPER(v."NAME1") LIKE UPPER('%{name}%')
        GROUP BY v."LIFNR", v."NAME1", v."ORT01", v."LAND1", v."BRSCH" """)
    if not rows or isinstance(rows, dict) or len(rows) == 0:
        return "❌ No vendor found matching '{}'.".format(name)
    lines = []
    for v in rows:
        lines.append("""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏭 Vendor: {name}
   Vendor ID:     {id}
   City:          {city}
   Country:       {country}
   Industry:      {industry}
   Total POs:     {pos}
   Total Value:   ${value:,.2f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""".format(
            name=v.get("NAME1", ""),
            id=v.get("LIFNR", ""),
            city=v.get("ORT01", "—"),
            country=v.get("LAND1", "—"),
            industry=v.get("BRSCH", "—"),
            pos=v.get("TOTAL_POS") or v.get("total_pos", 0),
            value=float(v.get("TOTAL_VALUE") or v.get("total_value", 0))))
    return "\n".join(lines)


def check_material(name):
    """Look up a procurement material by ID or name."""
    words = name.strip().split()
    desc_conditions = " AND ".join(
        [f"""UPPER("MAKTX") LIKE UPPER('%{w}%')""" for w in words])
    rows = query(f"""SELECT "MATNR", "MAKTX", "MATKL", "MEINS", "BASE_PRICE_USD"
        FROM "{S}"."PROC_MATERIALS"
        WHERE UPPER("MATNR") LIKE UPPER('%{name}%') OR ({desc_conditions})
        ORDER BY "MATNR" """)
    if not rows or isinstance(rows, dict) or len(rows) == 0:
        return "❌ No procurement material found matching '{}'.".format(name)
    lines = []
    for m in rows:
        lines.append("""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 Material: {desc}
   Material ID:   {id}
   Category:      {cat}
   UOM:           {uom}
   Price (USD):   ${price:,.2f}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""".format(
            desc=m.get("MAKTX", ""),
            id=m.get("MATNR", ""),
            cat=m.get("MATKL", "—"),
            uom=m.get("MEINS", "—"),
            price=float(m.get("BASE_PRICE_USD", 0))))
    return "\n".join(lines)


def po_lifecycle(po_number):
    """Full PO lifecycle view: header → items → GR → invoice → progress bar."""
    # Header + vendor
    po = query(f"""SELECT po."EBELN", po."BSART", po."ERDAT", po."EINDT", po."NETWR",
            po."WAERK", po."STATUS", v."NAME1" AS vendor, v."LIFNR"
        FROM "{S}"."PURCHASE_ORDERS" po
        JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{po_number}' """)
    if not po or isinstance(po, dict) or len(po) == 0:
        return "❌ PO {} not found.".format(po_number)
    po = po[0]

    # Line items
    items = query(f"""SELECT "EBELP", "TXZ01", "MENGE", "MEINS", "NETPR", "NETWR"
        FROM "{S}"."PO_ITEMS" WHERE "EBELN" = '{po_number}' ORDER BY "EBELP" """)

    # Goods receipts
    grs = query(f"""SELECT "MBLNR", "BLDAT", "STATUS"
        FROM "{S}"."GOODS_RECEIPTS" WHERE "EBELN" = '{po_number}' ORDER BY "BLDAT" """)

    # Vendor invoices
    vis = query(f"""SELECT "BELNR", "BLDAT", "TOTAL", "WAERK", "PAY_STATUS"
        FROM "{S}"."VENDOR_INVOICES" WHERE "EBELN" = '{po_number}' ORDER BY "BLDAT" """)

    # Build progress bar
    status = po.get("STATUS", "OPEN")
    steps = ["OPEN", "APPROVED", "RECEIVED", "INVOICED", "CLOSED"]
    status_map = {"OPEN": 0, "APPROVED": 1, "RECEIVED": 2, "CLOSED": 4}
    progress = status_map.get(status, 0)
    # If invoice exists, at least INVOICED
    if isinstance(vis, list) and len(vis) > 0 and progress < 3:
        progress = 3
    filled = "●" * (progress + 1) + "○" * (4 - progress)
    bar = " → ".join([f"[{filled[i]}] {steps[i]}" for i in range(5)])

    lines = [
        "══════════════════════════════════════════════════════",
        "📋 PO LIFECYCLE — {}".format(po_number),
        "══════════════════════════════════════════════════════",
        "",
        "🏭 Vendor:       {} ({})".format(po.get("vendor", ""), po.get("LIFNR", "")),
        "   Type:         {}".format(po.get("BSART", "")),
        "   Created:      {}".format(po.get("ERDAT", "")),
        "   Delivery Due: {}".format(po.get("EINDT", "")),
        "   Net Value:    {} {}".format(fmt_currency(float(po.get("NETWR", 0)), po.get("WAERK", "USD")), po.get("WAERK", "")),
        "   Status:       {}".format(status),
        "",
        "── Line Items ──────────────────────────────────────",
    ]
    if isinstance(items, list) and len(items) > 0:
        for it in items:
            lines.append("   #{}: {} — Qty {} {} @ {} = {}".format(
                it.get("EBELP", ""),
                it.get("TXZ01", ""),
                it.get("MENGE", ""),
                it.get("MEINS", ""),
                fmt_currency(float(it.get("NETPR", 0)), po.get("WAERK", "USD")),
                fmt_currency(float(it.get("NETWR", 0)), po.get("WAERK", "USD"))))
    else:
        lines.append("   (no line items)")

    lines.append("")
    lines.append("── Goods Receipts ──────────────────────────────────")
    if isinstance(grs, list) and len(grs) > 0:
        for gr in grs:
            lines.append("   GR {}: {} — Status: {}".format(
                gr.get("MBLNR", ""), gr.get("BLDAT", ""), gr.get("STATUS", "")))
    else:
        lines.append("   ⏳ No goods received yet")

    lines.append("")
    lines.append("── Vendor Invoices ─────────────────────────────────")
    if isinstance(vis, list) and len(vis) > 0:
        for vi in vis:
            lines.append("   Invoice {}: {} — {} — Payment: {}".format(
                vi.get("BELNR", ""), vi.get("BLDAT", ""),
                fmt_currency(float(vi.get("TOTAL", 0)), vi.get("WAERK", "USD")),
                vi.get("PAY_STATUS", "")))
    else:
        lines.append("   ⏳ No invoice created yet")

    lines.append("")
    lines.append("── Progress ────────────────────────────────────────")
    lines.append("   " + bar)
    lines.append("══════════════════════════════════════════════════════")
    return "\n".join(lines)


def validate_receive_goods(user_query):
    """Intercept 'receive goods' — block if PO is OPEN (must be APPROVED first)."""
    num = re.findall(r'\b(\d{7,10})\b', user_query)
    if not num:
        return None  # no number found, let Chatbot V3 handle
    po_number = num[0]
    po = query(f"""SELECT "STATUS" FROM "{S}"."PURCHASE_ORDERS" WHERE "EBELN" = '{po_number}' """)
    if not po or isinstance(po, dict) or len(po) == 0:
        return None  # PO not found, let Chatbot V3 give its own error
    status = po[0].get("STATUS", "")
    if status == "OPEN":
        return "❌ PO {} is still OPEN. You must approve it first before receiving goods.\n\n   → Try: approve po {}".format(po_number, po_number)
    return None  # status is APPROVED or other — let Chatbot V3 proceed


def validate_close_po(user_query):
    """Intercept 'close po' — block if no vendor invoice or invoice not PAID."""
    num = re.findall(r'\b(\d{7,10})\b', user_query)
    if not num:
        return None
    po_number = num[0]
    vi = query(f"""SELECT "BELNR", "PAY_STATUS" FROM "{S}"."VENDOR_INVOICES" WHERE "EBELN" = '{po_number}' """)
    if not vi or isinstance(vi, dict) or len(vi) == 0:
        return "❌ Cannot close PO {} — no vendor invoice found. Create the invoice first.\n\n   → Try: create vendor invoice {}".format(po_number, po_number)
    inv = vi[0]
    pay_status = inv.get("PAY_STATUS", "")
    if pay_status != "PAID":
        return "❌ Cannot close PO {} — vendor invoice {} is {} (not PAID). Pay the vendor first.\n\n   → Try: pay vendor {}".format(
            po_number, inv.get("BELNR", ""), pay_status, inv.get("BELNR", ""))
    return None  # invoice exists and is PAID — let Chatbot V3 close it


def order_lifecycle(order_number):
    """Full Sales Order lifecycle view: header → items → delivery → invoice → progress."""
    # Header + customer
    so = query(f"""SELECT o."VBELN", o."AUART", o."ERDAT", o."VDATU", o."NETWR",
            o."WAERK", o."STATUS", c."NAME1" AS customer, c."KUNNR"
        FROM "{S}"."SALES_ORDERS" o
        JOIN "{S}"."CUSTOMERS" c ON o."KUNNR" = c."KUNNR"
        WHERE o."VBELN" = '{order_number}' """)
    if not so or isinstance(so, dict) or len(so) == 0:
        return "❌ Sales Order {} not found.".format(order_number)
    so = so[0]

    # Line items
    items = query(f"""SELECT "POSNR", "ARKTX", "KWMENG", "NETPR", "NETWR", "WAERK"
        FROM "{S}"."SALES_ORDER_ITEMS" WHERE "VBELN" = '{order_number}' ORDER BY "POSNR" """)

    # Deliveries
    dlvs = query(f"""SELECT "VBELN", "WADAT", "GI_STATUS", "GI_DATE"
        FROM "{S}"."DELIVERIES" WHERE "VGBEL" = '{order_number}' ORDER BY "WADAT" """)

    # Customer invoices
    invs = query(f"""SELECT "VBELN", "FKDAT", "TOTAL", "WAERK", "PAY_STATUS"
        FROM "{S}"."INVOICES" WHERE "VGBEL" = '{order_number}' ORDER BY "FKDAT" """)

    # Progress bar
    status_code = so.get("STATUS", "A")
    status_labels = {"A": "Open", "B": "In Process", "C": "Completed"}
    status_text = status_labels.get(status_code, status_code)
    steps = ["CREATED", "DELIVERED", "INVOICED", "PAID"]
    progress = 0
    if isinstance(dlvs, list) and any(d.get("GI_STATUS") == "C" for d in dlvs):
        progress = 1
    if isinstance(invs, list) and len(invs) > 0:
        progress = 2
        if any(i.get("PAY_STATUS") == "PAID" for i in invs):
            progress = 3
    filled = "●" * (progress + 1) + "○" * (3 - progress)
    bar = " → ".join([f"[{filled[i]}] {steps[i]}" for i in range(4)])

    lines = [
        "══════════════════════════════════════════════════════",
        "📋 ORDER LIFECYCLE — {}".format(order_number),
        "══════════════════════════════════════════════════════",
        "",
        "👤 Customer:     {} ({})".format(so.get("customer", ""), so.get("KUNNR", "")),
        "   Type:         {}".format(so.get("AUART", "")),
        "   Created:      {}".format(so.get("ERDAT", "")),
        "   Delivery Due: {}".format(so.get("VDATU", "")),
        "   Net Value:    {} {}".format(fmt_currency(float(so.get("NETWR", 0)), so.get("WAERK", "USD")), so.get("WAERK", "")),
        "   Status:       {}".format(status_text),
        "",
        "── Line Items ──────────────────────────────────────",
    ]
    if isinstance(items, list) and len(items) > 0:
        for it in items:
            lines.append("   #{}: {} — Qty {} @ {} = {}".format(
                it.get("POSNR", ""),
                it.get("ARKTX", ""),
                it.get("KWMENG", ""),
                fmt_currency(float(it.get("NETPR", 0)), it.get("WAERK", "USD")),
                fmt_currency(float(it.get("NETWR", 0)), it.get("WAERK", "USD"))))
    else:
        lines.append("   (no line items)")

    lines.append("")
    lines.append("── Deliveries ──────────────────────────────────────")
    gi_labels = {"A": "Open", "B": "In Transit", "C": "Delivered"}
    if isinstance(dlvs, list) and len(dlvs) > 0:
        for d in dlvs:
            gi = gi_labels.get(d.get("GI_STATUS", ""), d.get("GI_STATUS", ""))
            lines.append("   Delivery {}: Ship {} — Status: {} {}".format(
                d.get("VBELN", ""), d.get("WADAT", ""), gi,
                "({})".format(d.get("GI_DATE", "")) if d.get("GI_DATE") else ""))
    else:
        lines.append("   ⏳ No delivery created yet")

    lines.append("")
    lines.append("── Invoices ────────────────────────────────────────")
    if isinstance(invs, list) and len(invs) > 0:
        for inv in invs:
            lines.append("   Invoice {}: {} — {} — Payment: {}".format(
                inv.get("VBELN", ""), inv.get("FKDAT", ""),
                fmt_currency(float(inv.get("TOTAL", 0)), inv.get("WAERK", "USD")),
                inv.get("PAY_STATUS", "")))
    else:
        lines.append("   ⏳ No invoice created yet")

    lines.append("")
    lines.append("── Progress ────────────────────────────────────────")
    lines.append("   " + bar)
    lines.append("══════════════════════════════════════════════════════")
    return "\n".join(lines)


def overdue_purchase_orders():
    """POs where delivery date < today AND status is OPEN or APPROVED."""
    rows = query(f"""SELECT po."EBELN", v."NAME1" AS vendor, po."EINDT", po."NETWR",
            po."WAERK", po."STATUS",
            DAYS_BETWEEN(po."EINDT", CURRENT_DATE) AS days_overdue
        FROM "{S}"."PURCHASE_ORDERS" po
        JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EINDT" < CURRENT_DATE
          AND po."STATUS" IN ('OPEN', 'APPROVED')
        ORDER BY po."EINDT" ASC """)
    if not rows or isinstance(rows, dict) or len(rows) == 0:
        return "✅ All purchase orders are on track — no overdue POs found."
    lines = [
        "══════════════════════════════════════════════════════",
        "⚠️  OVERDUE PURCHASE ORDERS ({} found)".format(len(rows)),
        "══════════════════════════════════════════════════════",
        "",
    ]
    for r in rows:
        days = r.get("DAYS_OVERDUE") or r.get("days_overdue", 0)
        lines.append("   PO {}  |  {} | {} days overdue | {} {} | Status: {}".format(
            r.get("EBELN", ""),
            (r.get("vendor") or r.get("VENDOR", ""))[:25],
            days,
            fmt_currency(float(r.get("NETWR", 0)), r.get("WAERK", "USD")),
            r.get("WAERK", ""),
            r.get("STATUS", "")))
    lines.append("")
    lines.append("══════════════════════════════════════════════════════")
    return "\n".join(lines)


def pending_actions():
    """Counts of key pending items across the business."""
    # POs awaiting approval (STATUS = OPEN)
    r1 = query(f"""SELECT COUNT(*) AS cnt FROM "{S}"."PURCHASE_ORDERS" WHERE "STATUS" = 'OPEN' """)
    po_approval = (r1[0].get("CNT") or r1[0].get("cnt", 0)) if isinstance(r1, list) and len(r1) > 0 else 0

    # POs awaiting GR (STATUS = APPROVED)
    r2 = query(f"""SELECT COUNT(*) AS cnt FROM "{S}"."PURCHASE_ORDERS" WHERE "STATUS" = 'APPROVED' """)
    po_gr = (r2[0].get("CNT") or r2[0].get("cnt", 0)) if isinstance(r2, list) and len(r2) > 0 else 0

    # Unpaid vendor invoices
    r3 = query(f"""SELECT COUNT(*) AS cnt FROM "{S}"."VENDOR_INVOICES" WHERE "PAY_STATUS" IN ('OPEN', 'PARTIAL', 'OVERDUE') """)
    vi_unpaid = (r3[0].get("CNT") or r3[0].get("cnt", 0)) if isinstance(r3, list) and len(r3) > 0 else 0

    # Overdue customer invoices
    r4 = query(f"""SELECT COUNT(*) AS cnt FROM "{S}"."INVOICES" WHERE "PAY_STATUS" = 'OVERDUE' """)
    inv_overdue = (r4[0].get("CNT") or r4[0].get("cnt", 0)) if isinstance(r4, list) and len(r4) > 0 else 0

    # Open deliveries
    r5 = query(f"""SELECT COUNT(*) AS cnt FROM "{S}"."DELIVERIES" WHERE "GI_STATUS" IN ('A', 'B') """)
    dlv_open = (r5[0].get("CNT") or r5[0].get("cnt", 0)) if isinstance(r5, list) and len(r5) > 0 else 0

    return """══════════════════════════════════════════════════════
📋 PENDING ACTIONS
══════════════════════════════════════════════════════

   🔴 POs awaiting approval:      {po_approval}
   🟡 POs awaiting goods receipt:  {po_gr}
   🟠 Unpaid vendor invoices:      {vi_unpaid}
   🔴 Overdue customer invoices:   {inv_overdue}
   🟡 Open/in-transit deliveries:  {dlv_open}

══════════════════════════════════════════════════════""".format(
        po_approval=po_approval, po_gr=po_gr, vi_unpaid=vi_unpaid,
        inv_overdue=inv_overdue, dlv_open=dlv_open)


def create_vendor_invoice(po_number):
    """Create a vendor invoice for a PO after validating PO, status, GR, and duplicates."""

    # ── Check 1: PO exists? ──
    po = query(f"""SELECT po."EBELN", po."STATUS", po."NETWR", po."WAERK", po."LIFNR", po."BUKRS", v."NAME1"
        FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{po_number}'""")
    if not po or isinstance(po, dict):
        return "❌ PO {} not found. Please create a PO first, then receive goods (GR), then proceed to create the invoice.".format(po_number)
    po = po[0]

    # ── Check 2: PO status = RECEIVED? ──
    if po["STATUS"] not in ("RECEIVED",):
        if po["STATUS"] in ("OPEN", "APPROVED"):
            return "❌ PO {} is still {}. Please receive goods first (receive goods {}) before creating the invoice.".format(
                po_number, po["STATUS"], po_number)
        if po["STATUS"] == "CLOSED":
            return "ℹ️  PO {} is already CLOSED. The full P2P cycle is complete.".format(po_number)
        return "❌ PO {} has unexpected status: {}".format(po_number, po["STATUS"])

    # ── Check 3: GR exists for this PO? ──
    gr = query(f"""SELECT COUNT(*) AS cnt FROM "{S}"."GOODS_RECEIPTS"
        WHERE "EBELN" = '{po_number}' AND "STATUS" = 'POSTED'""")
    if not gr or isinstance(gr, dict) or (gr[0].get("cnt") or gr[0].get("CNT", 0)) == 0:
        return "❌ No Goods Receipt found for PO {}. Please receive goods first (receive goods {}) before creating the invoice.".format(
            po_number, po_number)

    # ── Check 4: Duplicate invoice? ──
    existing = query(f"""SELECT "BELNR" FROM "{S}"."VENDOR_INVOICES" WHERE "EBELN" = '{po_number}'""")
    if existing and isinstance(existing, list) and len(existing) > 0:
        inv_id = existing[0].get("BELNR", "unknown")
        return "ℹ️  Vendor invoice already exists for PO {}: Invoice {}".format(po_number, inv_id)

    # ── All checks passed — create the invoice ──
    net_amount = float(po["NETWR"])
    tax_rate = {"PW10": 0.08, "PW20": 0.21, "PW30": 0.18}.get(po["BUKRS"], 0.10)
    tax = round(net_amount * tax_rate, 2)
    total = round(net_amount + tax, 2)
    due_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    # Generate next invoice number
    next_vi = query(f"""SELECT MAX(CAST("BELNR" AS BIGINT)) + 1 AS next_id FROM "{S}"."VENDOR_INVOICES" """)
    vi_num = None
    if isinstance(next_vi, list) and len(next_vi) > 0:
        val = next_vi[0].get("next_id") or next_vi[0].get("NEXT_ID")
        if val:
            vi_num = str(int(val))
    if not vi_num:
        vi_num = "5100199999"

    result = execute(f"""INSERT INTO "{S}"."VENDOR_INVOICES"
        ("BELNR","LIFNR","BLDAT","NETWR","MWSBK","TOTAL","WAERK","DUE_DATE","PAY_STATUS","EBELN")
        VALUES ('{vi_num}', '{po["LIFNR"]}', '{TODAY}', {net_amount}, {tax}, {total}, '{po["WAERK"]}', '{due_date}', 'OPEN', '{po_number}')""")

    if isinstance(result, dict) and result.get("error"):
        return "❌ Invoice creation failed: {}".format(result["error"])

    return """✅ Vendor Invoice Created!
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Invoice ID:   {vi_num}
   PO:           {po_number}
   Vendor:       {vendor}
   Net Amount:   {net}
   Tax ({tax_pct}%):    {tax_amt}
   Total:        {total}
   Currency:     {curr}
   Due Date:     {due}
   Status:       OPEN
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Next step: pay vendor {vi_num}""".format(
        vi_num=vi_num, po_number=po_number, vendor=po["NAME1"],
        net=fmt_currency(net_amount, po["WAERK"]),
        tax_pct=int(tax_rate * 100), tax_amt=fmt_currency(tax, po["WAERK"]),
        total=fmt_currency(total, po["WAERK"]),
        curr=po["WAERK"], due=due_date)


def _capture_print(func, *args, **kwargs):
    """Capture print output from BAPI demo functions."""
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    try:
        func(*args, **kwargs)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sys.stdout = old_stdout
    return buffer.getvalue()


def _format_bapi_result(result, bapi_name):
    """Format a BAPI result dict with BAPIRET2 messages into display text."""
    lines = [
        "=" * 55,
        f"BAPI: {bapi_name}",
        "=" * 55,
        "",
    ]

    # Show header fields (skip RETURN)
    for key, val in result.items():
        if key == "RETURN":
            continue
        if isinstance(val, dict):
            for k2, v2 in val.items():
                lines.append(f"   {k2}: {v2}")
        else:
            lines.append(f"   {key}: {val}")

    # Show BAPIRET2 messages
    ret = result.get("RETURN")
    if ret and hasattr(ret, "format"):
        lines.append("")
        lines.append("-- Messages " + "-" * 43)
        lines.append(ret.format())

    lines.append("=" * 55)
    return "\n".join(lines)


def handle_bapi_mode(user_input):
    """Handle commands in BAPI mode. Returns (result_text, is_write) or None to fall through."""
    q = user_input.lower().strip()

    # ── Run full P2P cycle (interactive) ──
    if any(w in q for w in ["p2p cycle", "p2p demo", "procure to pay cycle", "full p2p",
                            "run p2p", "demo p2p", "p2p flow", "p2p", "procure to pay",
                            "start p2p", "run full cycle", "full cycle"]):
        return ("📋 **Interactive P2P Cycle**\n\n"
                "To start the full Procure-to-Pay flow (6 steps), provide your PO details:\n\n"
                "```\ncreate po for [vendor] - [material] - [quantity]\n```\n\n"
                "**Optional flags:** `- price [amt] - date [YYYY-MM-DD] - plant [code]`\n\n"
                "**Example:**\n"
                "`create po for Sun Chemical - UV Ink Black - 50`\n\n"
                "After you confirm the PO preview, the full P2P cycle will run:\n"
                "1️⃣ Create PO → 2️⃣ Approve → 3️⃣ Goods Receipt → 4️⃣ Invoice → 5️⃣ Payment → 6️⃣ Close PO\n\n"
                "You'll get **Continue / Continue All / Stop** controls after each step."), False

    # ── Run full O2C cycle ──
    if any(w in q for w in ["o2c cycle", "o2c demo", "order to cash cycle", "full o2c",
                            "run o2c", "demo o2c"]):
        output = _capture_print(demo_full_o2c_cycle)
        return output, True

    # ── Run both cycles ──
    if any(w in q for w in ["full demo", "run both", "both cycles", "demo all"]):
        out1 = _capture_print(demo_full_p2p_cycle)
        out2 = _capture_print(demo_full_o2c_cycle)
        return out1 + "\n\n" + out2, True

    # ── BAPI_PO_RELEASE — Approve Purchase Order ──
    if any(w in q for w in ["approve po", "approve purchase", "release po", "po approved"]):
        num = re.findall(r'\b(\d{7,10})\b', user_input)
        if not num:
            return "Please provide the PO number. Example: approve po 4500000123", False
        result = BAPI_PO_RELEASE(PO_NUMBER=num[0])
        return _format_bapi_result(result, "BAPI_PO_RELEASE"), True

    # ── BAPI_PO_CREATE1 — Create Purchase Order ──
    if any(w in q for w in ["create po", "create purchase order", "new po", "new purchase order",
                            "raise po", "raise purchase"]):
        parts = re.split(r'\s+(?:for|from|to)\s+', q, maxsplit=1)
        if len(parts) < 2:
            clean = q
            for kw in ["create po", "create purchase order", "new po", "new purchase order",
                       "raise po", "raise purchase"]:
                clean = clean.replace(kw, "").strip()
            parts = [q, clean]

        detail = parts[1] if len(parts) > 1 else ""
        if not detail or len(detail) < 3:
            return ("Please specify vendor, material and quantity.\n"
                    "Format: create po for [vendor] - [material] - [quantity]\n"
                    "Optional: - price [amount] - date [YYYY-MM-DD] - plant [code]\n"
                    "Example: create po for Sun Chemical - UV Ink Black - 50\n"
                    "Example: create po for Sun Chemical - UV Ink Black - 50 - price 120 - date 2026-03-15"), False

        # Extract optional flags before main parsing
        po_price = None
        po_date = None
        po_plant = None

        price_m = re.search(r'\bprice\s+([\d.]+)', detail, re.IGNORECASE)
        if price_m:
            po_price = float(price_m.group(1))
            detail = detail[:price_m.start()] + detail[price_m.end():]

        date_m = re.search(r'\bdate\s+(\d{4}-\d{2}-\d{2})', detail, re.IGNORECASE)
        if date_m:
            po_date = date_m.group(1)
            detail = detail[:date_m.start()] + detail[date_m.end():]

        plant_m = re.search(r'\bplant\s+(\w+)', detail, re.IGNORECASE)
        if plant_m:
            po_plant = plant_m.group(1)
            detail = detail[:plant_m.start()] + detail[plant_m.end():]

        detail = re.sub(r'\s*-\s*$', '', detail).strip()

        qty_match = re.findall(r'\b(\d+)\b', detail)
        qty = int(qty_match[-1]) if qty_match else 1
        detail_clean = re.sub(r'\b\d+\b', '', detail).strip()
        detail_clean = re.sub(r'[\s\-]+$', '', detail_clean).strip()

        if ' - ' in detail_clean:
            segments = [s.strip().strip('-').strip() for s in detail_clean.split(' - ') if s.strip().strip('-').strip()]
        elif ',' in detail_clean:
            segments = [s.strip() for s in detail_clean.split(',') if s.strip()]
        else:
            words = detail_clean.split()
            if len(words) >= 4:
                segments = [' '.join(words[:2]), ' '.join(words[2:])]
            elif len(words) >= 2:
                segments = [words[0], ' '.join(words[1:])]
            else:
                return "Could not parse vendor and material. Try: create po for Sun Chemical - UV Ink Black - 50", False

        if len(segments) >= 2:
            vendor_name = segments[0].strip()
            material_kw = segments[1].strip()
        else:
            return "Please specify both vendor and material.\nExample: create po for Sun Chemical - UV Ink Black - 50", False

        # Use preview to validate and show confirmation
        preview_text, po_data = preview_create_po(vendor_name, material_kw, qty,
                                                   plant=po_plant, price=po_price, date=po_date)
        if po_data is None:
            # Error (vendor/material not found or ambiguous)
            return preview_text, False

        # Store BAPI-specific params in po_data for confirmation
        poheader = {"DOC_TYPE": "NB", "VENDOR": vendor_name, "COMP_CODE": ""}
        if po_plant:
            poheader["PLANT"] = po_plant
        poitem = {"PO_ITEM": "00010", "MATERIAL": material_kw, "QUANTITY": qty}
        if po_price is not None:
            poitem["NET_PRICE"] = po_price
        if po_date:
            poheader["DELIV_DATE"] = po_date

        po_data["_bapi_header"] = poheader
        po_data["_bapi_items"] = [poitem]
        po_data["_custom_price"] = po_price
        po_data["_custom_date"] = po_date

        # Return preview as a 3-tuple so chat() can detect it
        return preview_text, False, po_data

    # ── BAPI_SALESORDER_CREATEFROMDAT2 — Create Sales Order ──
    if any(w in q for w in ["create order", "create sales order", "new order", "new sales order",
                            "book order"]):
        parts = re.split(r'\s+(?:for|from|to)\s+', q, maxsplit=1)
        if len(parts) < 2:
            clean = q
            for kw in ["create order", "create sales order", "new order", "new sales order", "book order"]:
                clean = clean.replace(kw, "").strip()
            parts = [q, clean]

        detail = parts[1] if len(parts) > 1 else ""
        if not detail or len(detail) < 3:
            return ("Please specify customer, product and quantity.\n"
                    "Format: create order for [customer] - [product] - [qty]\n"
                    "Example: create order for 3M Company - ProJet X7 Digital Press - 2"), False

        qty_match = re.findall(r'\b(\d+)\b', detail)
        qty = int(qty_match[-1]) if qty_match else 1
        detail_clean = re.sub(r'\b\d+\b', '', detail).strip()
        detail_clean = re.sub(r'[\s\-]+$', '', detail_clean).strip()

        if ' - ' in detail_clean:
            segments = [s.strip().strip('-').strip() for s in detail_clean.split(' - ') if s.strip().strip('-').strip()]
        elif ',' in detail_clean:
            segments = [s.strip() for s in detail_clean.split(',') if s.strip()]
        else:
            words = detail_clean.split()
            if len(words) >= 4:
                segments = [' '.join(words[:2]), ' '.join(words[2:])]
            elif len(words) >= 2:
                segments = [words[0], ' '.join(words[1:])]
            else:
                return "Could not parse customer and product. Try: create order for 3M Company - ProJet X7 - 2", False

        if len(segments) >= 2:
            customer_name = segments[0].strip()
            material_kw = segments[1].strip()
        else:
            return "Please specify both customer and product.", False

        result = BAPI_SALESORDER_CREATEFROMDAT2(
            ORDER_HEADER_IN={"DOC_TYPE": "ZCO"},
            ORDER_ITEMS_IN=[{"ITM_NUMBER": "000010", "MATERIAL": material_kw, "TARGET_QTY": qty}],
            ORDER_PARTNERS=[{"PARTN_ROLE": "AG", "PARTN_NUMB": customer_name}]
        )
        return _format_bapi_result(result, "BAPI_SALESORDER_CREATEFROMDAT2"), True

    # ── BAPI_GOODSMVT_CREATE — Goods Receipt ──
    if any(w in q for w in ["receive goods", "receive po", "goods received", "gr for",
                            "post gr", "receive material"]):
        num = re.findall(r'\b(\d{7,10})\b', user_input)
        if not num:
            return "Please provide the PO number. Example: receive goods 4500000123", False

        result = BAPI_GOODSMVT_CREATE(
            GOODSMVT_HEADER={"PSTNG_DATE": TODAY, "DOC_DATE": TODAY},
            GOODSMVT_CODE={"GM_CODE": "01"},
            GOODSMVT_ITEM=[{"PO_NUMBER": num[0], "MOVE_TYPE": "101"}]
        )
        return _format_bapi_result(result, "BAPI_GOODSMVT_CREATE"), True

    # ── BAPI_INCOMINGINVOICE_CREATE — Vendor Invoice ──
    if any(w in q for w in ["create vendor invoice", "create supplier invoice",
                            "vendor invoice for", "invoice for po",
                            "invoice verification"]):
        num = re.findall(r'\b(\d{7,10})\b', user_input)
        if not num:
            return "Please provide the PO number. Example: create vendor invoice 4500000123", False

        result = BAPI_INCOMINGINVOICE_CREATE(
            HEADERDATA={"PO_NUMBER": num[0], "PMNTTRMS": "Z030"},
            ITEMDATA=[]
        )
        return _format_bapi_result(result, "BAPI_INCOMINGINVOICE_CREATE"), True

    # ── BAPI_ACC_DOCUMENT_POST — Pay Vendor ──
    if any(w in q for w in ["pay vendor", "vendor payment", "pay supplier"]):
        num = re.findall(r'\b(\d{7,10})\b', user_input)
        if not num:
            return "Please provide the vendor invoice number. Example: pay vendor 5100000123", False

        result = BAPI_ACC_DOCUMENT_POST(
            DOCUMENTHEADER={"DOC_TYPE": "KZ", "PSTNG_DATE": TODAY},
            ACCOUNTPAYABLE={"PAYMENT_REF": num[0]}
        )
        return _format_bapi_result(result, "BAPI_ACC_DOCUMENT_POST (Vendor Payment)"), True

    # ── BAPI_ACC_DOCUMENT_POST — Customer Payment ──
    if any(w in q for w in ["record payment", "customer paid", "payment received"]):
        num = re.findall(r'\b(\d{7,10})\b', user_input)
        if not num:
            return "Please provide the customer invoice number. Example: record payment 9000000123", False

        result = BAPI_ACC_DOCUMENT_POST(
            DOCUMENTHEADER={"DOC_TYPE": "DZ", "PSTNG_DATE": TODAY},
            ACCOUNTPAYABLE={"PAYMENT_REF": num[0]}
        )
        return _format_bapi_result(result, "BAPI_ACC_DOCUMENT_POST (Customer Payment)"), True

    # ── BAPI help ──
    if q in ["help", "?", "commands"]:
        return BAPI_HELP_TEXT, False

    # Not a BAPI write command — return None to fall through to ChatV3 for reads
    return None


BAPI_HELP_TEXT = """
======================================================
  BAPI MODE — SAP BAPI Simulation Commands
======================================================

WRITE OPERATIONS (via BAPI):
  create po for [vendor] - [material] - [qty] (optional: - price [amt] - date [YYYY-MM-DD] - plant [code])
      -> BAPI_PO_CREATE1

  create order for [customer] - [product] - [qty]
      -> BAPI_SALESORDER_CREATEFROMDAT2

  receive goods [PO number]
      -> BAPI_GOODSMVT_CREATE (Movement Type 101)

  create vendor invoice [PO number]
      -> BAPI_INCOMINGINVOICE_CREATE (3-Way Match)

  pay vendor [invoice number]
      -> BAPI_ACC_DOCUMENT_POST (KZ)

  record payment [invoice number]
      -> BAPI_ACC_DOCUMENT_POST (DZ)

DEMO CYCLES:
  run p2p cycle    Full Procure-to-Pay demo
  run o2c cycle    Full Order-to-Cash demo
  run both         Both cycles

READ OPERATIONS:
  All read commands work the same as ChatV3 mode
  (dashboard, orders, purchase orders, vendors, etc.)

======================================================
"""


def handle_extra_commands(q, raw_input=""):
    """Check for commands that Chatbot V3 doesn't handle. Returns result or None."""
    # ── Dashboard (fixed for HANA uppercase keys) ──
    if q in ["dashboard", "summary", "overview", "status", "home"]:
        return app_show_dashboard()

    if q in ["materials", "all materials", "material list", "show materials",
             "products list", "all products", "show products", "product list"]:
        return query_materials()
    if q in ["plants", "all plants", "show plants", "plant list", "factories",
             "warehouses", "locations", "manufacturing plants"]:
        return query_plants()

    # ── Pre-checks: check vendor / check material ──
    if q.startswith("check vendor ") or q.startswith("lookup vendor ") or q.startswith("find vendor "):
        name = re.sub(r'^(check|lookup|find) vendor\s+', '', q).strip()
        if name:
            return check_vendor(name)
        return "❓ Please provide a vendor name. Example: check vendor Sun Chemical"

    if q.startswith("check material ") or q.startswith("lookup material ") or q.startswith("find material "):
        name = re.sub(r'^(check|lookup|find) material\s+', '', q).strip()
        if name:
            return check_material(name)
        return "❓ Please provide a material name. Example: check material UV Ink"

    # ── Lifecycle views ──
    if q.startswith("po status ") or q.startswith("po lifecycle "):
        num = re.findall(r'\b(\d{7,10})\b', raw_input)
        if num:
            return po_lifecycle(num[0])
        return "❓ Please provide a PO number. Example: po status 4500000001"

    if q.startswith("order status ") or q.startswith("order lifecycle ") or q.startswith("so status "):
        num = re.findall(r'\b(\d{7,10})\b', raw_input)
        if num:
            return order_lifecycle(num[0])
        return "❓ Please provide an order number. Example: order status 0000000001"

    # ── Business intelligence ──
    if q in ["overdue purchase orders", "overdue pos", "overdue po",
             "late purchase orders", "late pos", "delayed pos"]:
        return overdue_purchase_orders()

    if q in ["pending actions", "pending tasks", "action items",
             "what needs attention", "to do", "todo"]:
        return pending_actions()

    # ── Workflow validations (return None to pass through to Chatbot V3) ──
    if any(w in q for w in ["receive goods", "receive po", "goods receipt for",
                            "gr for po", "post gr"]):
        result = validate_receive_goods(raw_input)
        if result:
            return result
        # None = validation passed, fall through to Chatbot V3

    if any(w in q for w in ["close po", "close purchase", "complete po"]):
        result = validate_close_po(raw_input)
        if result:
            return result
        # None = validation passed, fall through to Chatbot V3

    # ── WRITE: Pay vendor invoice (natural language variations) ──
    q_stripped = re.sub(r'\b(a|an|the)\b', '', q).replace('  ', ' ').strip()
    if any(w in q_stripped for w in ["payment for", "payment to", "make payment",
                                      "pay invoice", "pay vendor invoice",
                                      "pay supplier", "pay supplier invoice",
                                      "vendor payment", "supplier payment"]):
        num = re.findall(r'\b(\d{7,10})\b', raw_input)
        if num:
            return parse_and_execute("pay vendor " + num[0])
        return "❓ Please provide the invoice number. Example: pay vendor 5100100001"

    # ── WRITE: Create Vendor/Supplier Invoice ──
    if any(w in q_stripped for w in ["create vendor invoice", "create supplier invoice",
                            "generate vendor invoice", "generate supplier invoice",
                            "generate invoice for po", "vendor invoice for",
                            "supplier invoice for", "invoice for po",
                            "invoice for", "new vendor invoice", "new supplier invoice",
                            "raise vendor invoice", "raise supplier invoice"]):
        num = re.findall(r'\b(\d{7,10})\b', raw_input)
        if num:
            return create_vendor_invoice(num[0])
        return "❓ Please provide the PO number. Example: create supplier invoice 4500000123"

    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message", "").strip()
    mode = request.json.get("mode", "chatv3")  # "chatv3" or "bapi"
    if not user_input:
        return jsonify({"error": "Empty message"}), 400

    try:
        refined_query = None
        is_ai_answer = False
        q = user_input.lower().strip()

        # ── Handle active P2P flow (continue / continue_all / stop) ──
        if _p2p_flow["active"]:
            if q in ("continue", "next", "yes"):
                # Run next step only
                idx = _p2p_flow["step_index"]
                success, step, output, ctx = run_p2p_step(idx, _p2p_flow["ctx"])
                _p2p_flow["ctx"] = ctx

                if not success:
                    _p2p_flow["active"] = False
                    return jsonify({"result": output, "is_write": True, "refined_query": None,
                                    "is_ai_answer": False, "mode": "bapi", "p2p_failed": True})

                _p2p_flow["step_index"] = idx + 1

                # Check if that was the last step
                if _p2p_flow["step_index"] >= len(P2P_STEPS):
                    _p2p_flow["active"] = False
                    return jsonify({"result": output, "is_write": True, "refined_query": None,
                                    "is_ai_answer": False, "mode": "bapi"})

                # More steps remain — ask again
                next_step = P2P_STEPS[_p2p_flow["step_index"]]
                return jsonify({"result": output, "is_write": True, "refined_query": None,
                                "is_ai_answer": False, "mode": "bapi",
                                "p2p_pending": True,
                                "p2p_next_step": f"{next_step['icon']} Step {_p2p_flow['step_index'] + 1}: {next_step['name']}",
                                "p2p_step_index": _p2p_flow["step_index"],
                                "p2p_total_steps": len(P2P_STEPS)})

            elif q in ("continue all", "continue_all", "auto", "run all", "yes all"):
                # Run all remaining steps automatically
                idx = _p2p_flow["step_index"]
                success, output, ctx = run_p2p_remaining_steps(idx, _p2p_flow["ctx"])
                _p2p_flow["ctx"] = ctx
                _p2p_flow["active"] = False
                return jsonify({"result": output, "is_write": True, "refined_query": None,
                                "is_ai_answer": False, "mode": "bapi"})

            elif q in ("stop", "no", "cancel", "abort"):
                step_idx = _p2p_flow["step_index"]
                po_num = _p2p_flow["ctx"].get("po_num", "?")
                prev_step = P2P_STEPS[step_idx - 1] if step_idx > 0 else P2P_STEPS[0]
                _p2p_flow["active"] = False
                return jsonify({
                    "result": f"⏹ P2P flow stopped after Step {step_idx}/{len(P2P_STEPS)} ({prev_step['name']}).\n   PO {po_num} — no rollback, previous steps preserved.",
                    "is_write": False, "refined_query": None, "is_ai_answer": False, "mode": "bapi"})
            else:
                # User typed something else — clear flow and process normally
                _p2p_flow["active"] = False

        # ── Handle pending PO confirmation (yes/no) ──
        if _pending_po["data"] is not None:
            if q in ("yes", "y", "confirm", "ok", "proceed"):
                po_data = _pending_po["data"]
                pending_mode = _pending_po["mode"]
                _pending_po["data"] = None
                _pending_po["mode"] = None

                if "_bapi_header" in po_data:
                    # Confirm via BAPI — start interactive P2P flow (Step 1: Create PO)
                    ctx = {
                        "vendor_name": po_data["vendor"]["NAME1"],
                        "material_keyword": po_data["mat"]["MAKTX"],
                        "qty": po_data["qty"],
                        "price": po_data.get("_custom_price"),
                        "date": po_data.get("_custom_date"),
                        "plant": po_data["plant"],
                    }
                    success, step, output, ctx = run_p2p_step(0, ctx)

                    if not success:
                        return jsonify({"result": output, "is_write": True, "refined_query": None,
                                        "is_ai_answer": False, "mode": "bapi", "p2p_failed": True})

                    # Start interactive flow at step 1 (next step = index 1)
                    _p2p_flow["active"] = True
                    _p2p_flow["step_index"] = 1
                    _p2p_flow["ctx"] = ctx
                    _p2p_flow["auto_mode"] = False

                    next_step = P2P_STEPS[1]
                    return jsonify({"result": output, "is_write": True, "refined_query": None,
                                    "is_ai_answer": False, "mode": "bapi",
                                    "p2p_pending": True,
                                    "p2p_next_step": f"{next_step['icon']} Step 2: {next_step['name']}",
                                    "p2p_step_index": 1,
                                    "p2p_total_steps": len(P2P_STEPS)})
                else:
                    # Confirm via ChatV3 (just create PO, no cycle)
                    result_text = confirm_create_po(po_data)
                    return jsonify({"result": result_text, "is_write": True, "refined_query": None,
                                    "is_ai_answer": False, "mode": pending_mode, "confirmed": True})

            elif q in ("no", "n", "cancel", "abort", "nope"):
                _pending_po["data"] = None
                _pending_po["mode"] = None
                return jsonify({"result": "❌ PO creation cancelled.", "is_write": False,
                                "refined_query": None, "is_ai_answer": False, "mode": mode})
            else:
                # User typed something else — clear pending and process normally
                _pending_po["data"] = None
                _pending_po["mode"] = None

        # ── BAPI MODE ──
        if mode == "bapi":
            bapi_result = handle_bapi_mode(user_input)
            if bapi_result:
                # 3-tuple = PO preview with data for confirmation
                if len(bapi_result) == 3:
                    preview_text, _, po_data = bapi_result
                    _pending_po["data"] = po_data
                    _pending_po["mode"] = "bapi"
                    return jsonify({"result": preview_text, "is_write": False, "refined_query": None,
                                    "is_ai_answer": False, "mode": "bapi", "pending_confirm": True})

                result_text, is_write = bapi_result
                return jsonify({
                    "result": result_text,
                    "is_write": is_write,
                    "refined_query": None,
                    "is_ai_answer": False,
                    "mode": "bapi",
                })
            # BAPI mode didn't handle it — fall through to ChatV3 for read operations

        # ── CHATV3 MODE (also fallback for BAPI reads) ──

        # 0) Check extra commands (materials, plants, create vendor invoice) not in Chatbot V3
        extra = handle_extra_commands(user_input.lower().strip(), user_input)
        if extra:
            is_write = isinstance(extra, str) and any(extra.startswith(p) for p in ["\u2705", "\u274c", "\u2139\ufe0f", "\u2753"])
            return jsonify({"result": extra, "is_write": is_write, "refined_query": None, "is_ai_answer": False, "mode": mode})

        # 1) Try the rule-based parser first (fast, no API call)
        result = parse_and_execute(user_input)

        # 1.5) Check if result is a PO preview (tuple with po_data)
        if isinstance(result, tuple) and len(result) == 2:
            preview_text, po_data = result
            if po_data is not None:
                # It's a preview — store pending and return preview
                _pending_po["data"] = po_data
                _pending_po["mode"] = mode
                return jsonify({"result": preview_text, "is_write": False, "refined_query": None,
                                "is_ai_answer": False, "mode": mode, "pending_confirm": True})
            else:
                # It's an error (multiple vendors/materials found, etc.)
                result = preview_text

        # 2) If parser didn't understand OR returned empty results, ask DeepSeek to rephrase and retry
        needs_refinement = isinstance(result, str) and (FALLBACK_MSG in result or NO_RESULTS_MSG in result)
        if needs_refinement:
            refined = refine_with_llm(user_input)
            if refined:
                refined_query = refined
                result = parse_and_execute(refined)

                # Check if refined result is a PO preview too
                if isinstance(result, tuple) and len(result) == 2:
                    preview_text, po_data = result
                    if po_data is not None:
                        _pending_po["data"] = po_data
                        _pending_po["mode"] = mode
                        return jsonify({"result": preview_text, "is_write": False, "refined_query": refined_query,
                                        "is_ai_answer": False, "mode": mode, "pending_confirm": True})
                    else:
                        result = preview_text

            # 3) If still not understood, let DeepSeek answer the question directly
            still_stuck = isinstance(result, str) and (FALLBACK_MSG in result or NO_RESULTS_MSG in result)
            if still_stuck:
                answer = answer_with_llm(user_input)
                if answer:
                    result = answer
                    refined_query = None
                    is_ai_answer = True

        # Detect if it's a write operation
        is_write = False
        if isinstance(result, str) and not is_ai_answer:
            is_write = any(result.startswith(p) for p in ["\u2705", "\u274c", "\u2139\ufe0f", "\u2753"])

        return jsonify({
            "result": result,
            "is_write": is_write,
            "refined_query": refined_query,
            "is_ai_answer": is_ai_answer,
            "mode": mode,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{S}"."SALES_ORDERS"')
        so = cur.fetchone()[0]
        cur.execute(f'SELECT COUNT(*) FROM "{S}"."PURCHASE_ORDERS"')
        po = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({"status": "ok", "sales_orders": so, "purchase_orders": po})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5002)
