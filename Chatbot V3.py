"""
SAP HANA Chatbot v3 — Full O2C + P2P with Read/Write Operations
PrintWorks Global Solutions
No AI dependency — rule-based with comprehensive SAP workflow support
"""

import re
import json
import sys
import os
import random
from datetime import datetime, timedelta
from hdbcli import dbapi

HANA_CONFIG = {
    "address": "3e0addec-ef25-4880-8812-637e3d3a99f7.hna1.prod-us10.hanacloud.ondemand.com",
    "port": 443,
    "user": "DBADMIN",
    "password": "RamAI001Y@",  # ← SET YOUR PASSWORD
    "encrypt": True,
    "sslValidateCertificate": False,
    "sslCryptoProvider": "openssl",
}
SCHEMA = "PRINTWORKS"


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    # Try with openssl first, then without crypto provider, then with commoncrypto
    for attempt_config in [
        {**HANA_CONFIG},
        {k: v for k, v in HANA_CONFIG.items() if k != "sslCryptoProvider"},
        {**HANA_CONFIG, "sslCryptoProvider": "commoncrypto"},
    ]:
        try:
            return dbapi.connect(**attempt_config)
        except Exception:
            continue
    # Final attempt with minimal config
    return dbapi.connect(
        address=HANA_CONFIG["address"],
        port=HANA_CONFIG["port"],
        user=HANA_CONFIG["user"],
        password=HANA_CONFIG["password"],
        encrypt=True,
        sslValidateCertificate=False,
    )


def query(sql):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        if cur.description:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        return []
    except Exception as e:
        return {"error": str(e)}
    finally:
        cur.close()
        conn.close()


def execute(sql):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        affected = cur.rowcount
        conn.commit()
        return {"success": True, "rows_affected": affected}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        cur.close()
        conn.close()


S = SCHEMA  # shorthand


# ============================================================
# DISPLAY HELPERS
# ============================================================

def fmt_table(results, max_rows=20):
    if isinstance(results, dict) and "error" in results:
        return f"❌ Error: {results['error']}"
    if not results:
        return "   (No results found)"

    cols = list(results[0].keys())
    widths = {}
    for col in cols:
        vals = [str(r.get(col, ""))[:35] for r in results[:max_rows]]
        widths[col] = max(len(col), max(len(v) for v in vals) if vals else 0)

    header = " | ".join(f"{c:<{widths[c]}}" for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines = [f"   {header}", f"   {sep}"]
    for r in results[:max_rows]:
        line = " | ".join(f"{str(r.get(c, ''))[:35]:<{widths[c]}}" for c in cols)
        lines.append(f"   {line}")
    if len(results) > max_rows:
        lines.append(f"   ... and {len(results) - max_rows} more rows")
    return "\n".join(lines)


def fmt_currency(val, curr="USD"):
    syms = {"USD": "$", "EUR": "€", "INR": "₹", "GBP": "£"}
    sym = syms.get(curr, curr + " ")
    if val is None:
        return f"{sym}0"
    return f"{sym}{val:,.2f}"


TODAY = datetime.now().strftime("%Y-%m-%d")


# ============================================================
# O2C (Order-to-Cash) READ OPERATIONS
# ============================================================

def read_sales_orders(args):
    """Show sales orders with filters"""
    w = []
    if args.get("customer"):
        w.append(f"""UPPER(c."NAME1") LIKE '%{args["customer"].upper()}%'""")
    if args.get("status"):
        sm = {"open": "A", "process": "B", "in process": "B", "completed": "C", "complete": "C"}
        s = sm.get(args["status"].lower(), args["status"].upper())
        w.append(f"""o."STATUS" = '{s}'""")
    if args.get("type"):
        tm = {"equipment": "ZOR", "consumable": "ZCO", "service": "ZSO", "amc": "ZSO",
              "spare": "ZSP", "return": "ZRE", "quotation": "ZQT"}
        t = tm.get(args["type"].lower(), args["type"].upper())
        w.append(f"""o."AUART" = '{t}'""")
    if args.get("region"):
        rm = {"us": "1000", "emea": "2000", "europe": "2000", "india": "3000", "apac": "3000"}
        w.append(f"""o."VKORG" = '{rm.get(args["region"].lower(), args["region"])}'""")
    if args.get("year"):
        w.append(f"""YEAR(o."ERDAT") = {args["year"]}""")

    where = "WHERE " + " AND ".join(w) if w else ""
    limit = args.get("limit", 15)

    sql = f"""
        SELECT o."VBELN" AS order_no, 
               CASE o."AUART" WHEN 'ZOR' THEN 'Equipment' WHEN 'ZCO' THEN 'Consumable' WHEN 'ZSO' THEN 'Service/AMC'
                    WHEN 'ZSP' THEN 'Spare Parts' WHEN 'ZRE' THEN 'Return' WHEN 'ZQT' THEN 'Quote' ELSE o."AUART" END AS type,
               c."NAME1" AS customer, c."LAND1" AS country,
               o."ERDAT" AS order_date, o."NETWR" AS net_value, o."WAERK" AS currency,
               CASE o."STATUS" WHEN 'A' THEN 'Open' WHEN 'B' THEN 'In Process' WHEN 'C' THEN 'Completed' END AS status,
               o."ERNAM" AS sales_rep
        FROM "{S}"."SALES_ORDERS" o
        JOIN "{S}"."CUSTOMERS" c ON o."KUNNR" = c."KUNNR"
        {where}
        ORDER BY o."ERDAT" DESC LIMIT {limit}
    """
    return query(sql)


def read_order_details(order_no):
    """Show full order with line items"""
    header = query(f"""
        SELECT o."VBELN", o."AUART", c."NAME1" AS customer, o."ERDAT", o."NETWR", o."WAERK", 
               o."BSTNK" AS po_number, o."STATUS", o."ERNAM" AS sales_rep
        FROM "{S}"."SALES_ORDERS" o JOIN "{S}"."CUSTOMERS" c ON o."KUNNR" = c."KUNNR"
        WHERE o."VBELN" = '{order_no}'
    """)
    items = query(f"""
        SELECT "POSNR" AS item, "MATNR" AS material, "ARKTX" AS description, 
               "KWMENG" AS qty, "NETPR" AS unit_price, "NETWR" AS value, "WAERK" AS curr
        FROM "{S}"."SALES_ORDER_ITEMS" WHERE "VBELN" = '{order_no}'
        ORDER BY "POSNR"
    """)
    return header, items


def read_deliveries(args):
    """Show deliveries"""
    w = []
    if args.get("customer"):
        w.append(f"""UPPER(c."NAME1") LIKE '%{args["customer"].upper()}%'""")
    if args.get("order"):
        w.append(f"""d."VGBEL" = '{args["order"]}'""")
    if args.get("status"):
        sm = {"open": "A", "transit": "B", "in transit": "B", "delivered": "C"}
        w.append(f"""d."GI_STATUS" = '{sm.get(args["status"].lower(), args["status"])}'""")

    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""
        SELECT d."VBELN" AS delivery_no, d."VGBEL" AS order_no, c."NAME1" AS customer,
               d."WADAT" AS ship_date,
               CASE d."GI_STATUS" WHEN 'A' THEN 'Open' WHEN 'B' THEN 'In Transit' WHEN 'C' THEN 'Delivered' END AS status,
               d."GI_DATE" AS gi_date, d."BTGEW" AS weight_kg
        FROM "{S}"."DELIVERIES" d JOIN "{S}"."CUSTOMERS" c ON d."KUNNR" = c."KUNNR"
        {where}
        ORDER BY d."WADAT" DESC LIMIT 15
    """
    return query(sql)


def read_invoices(args):
    """Show customer invoices"""
    w = []
    if args.get("customer"):
        w.append(f"""UPPER(c."NAME1") LIKE '%{args["customer"].upper()}%'""")
    if args.get("pay_status"):
        w.append(f"""i."PAY_STATUS" = '{args["pay_status"].upper()}'""")
    if args.get("region"):
        rm = {"us": "1000", "emea": "2000", "india": "3000"}
        w.append(f"""i."VKORG" = '{rm.get(args["region"].lower(), args["region"])}'""")

    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""
        SELECT i."VBELN" AS invoice_no, c."NAME1" AS customer, i."FKDAT" AS inv_date,
               i."NETWR" AS net, i."MWSBK" AS tax, i."TOTAL" AS total, i."WAERK" AS curr,
               i."DUE_DATE" AS due_date, i."PAY_STATUS" AS pay_status, i."VGBEL" AS order_no
        FROM "{S}"."INVOICES" i JOIN "{S}"."CUSTOMERS" c ON i."KUNNR" = c."KUNNR"
        {where}
        ORDER BY i."FKDAT" DESC LIMIT 15
    """
    return query(sql)


def read_revenue(args):
    """Revenue analytics"""
    gb = args.get("group_by", "region")
    w = ['o."STATUS" = \'C\'']
    if args.get("year"):
        w.append(f"""YEAR(o."ERDAT") = {args["year"]}""")
    if args.get("region"):
        rm = {"us": "1000", "emea": "2000", "india": "3000"}
        w.append(f"""o."VKORG" = '{rm.get(args["region"].lower(), "1000")}'""")
    where = "WHERE " + " AND ".join(w)

    if gb == "region":
        sql = f"""SELECT CASE o."VKORG" WHEN '1000' THEN 'US' WHEN '2000' THEN 'EMEA' WHEN '3000' THEN 'India/APAC' END AS region,
                   o."WAERK" AS currency, COUNT(*) AS orders, SUM(o."NETWR") AS revenue
            FROM "{S}"."SALES_ORDERS" o {where} GROUP BY o."VKORG", o."WAERK" ORDER BY o."VKORG" """
    elif gb == "customer":
        sql = f"""SELECT c."NAME1" AS customer, c."LAND1" AS country, o."WAERK" AS currency,
                   COUNT(*) AS orders, SUM(o."NETWR") AS revenue
            FROM "{S}"."SALES_ORDERS" o JOIN "{S}"."CUSTOMERS" c ON o."KUNNR" = c."KUNNR"
            {where} GROUP BY c."NAME1", c."LAND1", o."WAERK" ORDER BY revenue DESC LIMIT 10"""
    elif gb == "product":
        sql = f"""SELECT m."MAKTX" AS product, m."MATKL" AS category, SUM(oi."KWMENG") AS qty,
                   SUM(oi."NETWR") AS revenue
            FROM "{S}"."SALES_ORDER_ITEMS" oi 
            JOIN "{S}"."MATERIALS" m ON oi."MATNR" = m."MATNR"
            JOIN "{S}"."SALES_ORDERS" o ON oi."VBELN" = o."VBELN"
            {where} GROUP BY m."MAKTX", m."MATKL" ORDER BY revenue DESC LIMIT 10"""
    elif gb in ["month", "quarter"]:
        expr = f"""TO_VARCHAR(o."ERDAT", 'YYYY-MM')""" if gb == "month" else f"""TO_VARCHAR(o."ERDAT", 'YYYY') || '-Q' || QUARTER(o."ERDAT")"""
        sql = f"""SELECT {expr} AS period, o."WAERK" AS currency, COUNT(*) AS orders, SUM(o."NETWR") AS revenue
            FROM "{S}"."SALES_ORDERS" o {where} GROUP BY {expr}, o."WAERK" ORDER BY period DESC LIMIT 20"""
    else:
        return [{"error": f"Unknown group_by: {gb}"}]
    return query(sql)


def read_customers(args):
    """Customer details"""
    w = []
    if args.get("customer"):
        w.append(f"""UPPER(c."NAME1") LIKE '%{args["customer"].upper()}%'""")
    if args.get("country"):
        cm = {"us": "US", "india": "IN", "netherlands": "NL", "europe": "NL"}
        w.append(f"""c."LAND1" = '{cm.get(args["country"].lower(), args["country"].upper())}'""")
    if args.get("industry"):
        w.append(f"""c."BRSCH" = '{args["industry"].upper()}'""")
    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""SELECT c."KUNNR" AS id, c."NAME1" AS customer, c."ORT01" AS city, c."LAND1" AS country,
               c."BRSCH" AS industry, COUNT(o."VBELN") AS orders, SUM(o."NETWR") AS total_value
        FROM "{S}"."CUSTOMERS" c LEFT JOIN "{S}"."SALES_ORDERS" o ON c."KUNNR" = o."KUNNR"
        {where} GROUP BY c."KUNNR", c."NAME1", c."ORT01", c."LAND1", c."BRSCH"
        ORDER BY total_value DESC LIMIT 15"""
    return query(sql)


# ============================================================
# P2P (Procure-to-Pay) READ OPERATIONS
# ============================================================

def read_purchase_orders(args):
    """Show purchase orders"""
    w = []
    if args.get("vendor"):
        w.append(f"""UPPER(v."NAME1") LIKE '%{args["vendor"].upper()}%'""")
    if args.get("status"):
        w.append(f"""UPPER(po."STATUS") = '{args["status"].upper()}'""")
    if args.get("year"):
        w.append(f"""YEAR(po."ERDAT") = {args["year"]}""")
    if args.get("plant"):
        w.append(f"""po."WERKS" = '{args["plant"].upper()}'""")

    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""
        SELECT po."EBELN" AS po_number, 
               CASE po."BSART" WHEN 'NB' THEN 'Standard' WHEN 'FO' THEN 'Framework' 
                    WHEN 'UB' THEN 'Transfer' WHEN 'ZNB' THEN 'Subcontract' END AS po_type,
               v."NAME1" AS vendor, v."LAND1" AS country,
               po."ERDAT" AS po_date, po."EINDT" AS delivery_date,
               po."NETWR" AS net_value, po."WAERK" AS currency, po."STATUS" AS status, po."WERKS" AS plant
        FROM "{S}"."PURCHASE_ORDERS" po
        JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        {where}
        ORDER BY po."ERDAT" DESC LIMIT 15
    """
    return query(sql)


def read_po_details(po_number):
    """Show PO with line items"""
    header = query(f"""
        SELECT po."EBELN", po."BSART", v."NAME1" AS vendor, po."ERDAT", po."NETWR", po."WAERK", po."STATUS", po."WERKS"
        FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{po_number}'
    """)
    items = query(f"""
        SELECT "EBELP" AS item, "MATNR" AS material, "TXZ01" AS description,
               "MENGE" AS ordered_qty, "RECEIVED_QTY" AS received, "NETPR" AS unit_price, "NETWR" AS value, "WAERK" AS curr
        FROM "{S}"."PO_ITEMS" WHERE "EBELN" = '{po_number}' ORDER BY "EBELP"
    """)
    return header, items


def read_goods_receipts(args):
    """Show goods receipts"""
    w = []
    if args.get("vendor"):
        w.append(f"""UPPER(v."NAME1") LIKE '%{args["vendor"].upper()}%'""")
    if args.get("po"):
        w.append(f"""gr."EBELN" = '{args["po"]}'""")
    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""
        SELECT gr."MBLNR" AS gr_number, gr."EBELN" AS po_number, v."NAME1" AS vendor,
               gr."BLDAT" AS receipt_date, gr."WERKS" AS plant, gr."STATUS" AS status
        FROM "{S}"."GOODS_RECEIPTS" gr JOIN "{S}"."VENDORS" v ON gr."LIFNR" = v."LIFNR"
        {where} ORDER BY gr."BLDAT" DESC LIMIT 15
    """
    return query(sql)


def read_vendor_invoices(args):
    """Show vendor invoices"""
    w = []
    if args.get("vendor"):
        w.append(f"""UPPER(v."NAME1") LIKE '%{args["vendor"].upper()}%'""")
    if args.get("pay_status"):
        w.append(f"""vi."PAY_STATUS" = '{args["pay_status"].upper()}'""")
    if args.get("po"):
        w.append(f"""vi."EBELN" = '{args["po"]}'""")
    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""
        SELECT vi."BELNR" AS invoice_no, v."NAME1" AS vendor, vi."BLDAT" AS inv_date,
               vi."NETWR" AS net, vi."MWSBK" AS tax, vi."TOTAL" AS total, vi."WAERK" AS curr,
               vi."DUE_DATE" AS due_date, vi."PAY_STATUS" AS pay_status, vi."EBELN" AS po_number
        FROM "{S}"."VENDOR_INVOICES" vi JOIN "{S}"."VENDORS" v ON vi."LIFNR" = v."LIFNR"
        {where} ORDER BY vi."BLDAT" DESC LIMIT 15
    """
    return query(sql)


def read_vendors(args):
    """Show vendor master"""
    w = []
    if args.get("vendor"):
        w.append(f"""UPPER(v."NAME1") LIKE '%{args["vendor"].upper()}%'""")
    if args.get("country"):
        w.append(f"""v."LAND1" = '{args["country"].upper()}'""")
    if args.get("group"):
        w.append(f"""v."VENDOR_GROUP" = '{args["group"].upper()}'""")
    where = "WHERE " + " AND ".join(w) if w else ""
    sql = f"""
        SELECT v."LIFNR" AS id, v."NAME1" AS vendor, v."ORT01" AS city, v."LAND1" AS country,
               v."BRSCH" AS industry, v."VENDOR_GROUP" AS grp,
               COUNT(po."EBELN") AS total_pos, SUM(po."NETWR") AS total_value
        FROM "{S}"."VENDORS" v LEFT JOIN "{S}"."PURCHASE_ORDERS" po ON v."LIFNR" = po."LIFNR"
        {where} GROUP BY v."LIFNR", v."NAME1", v."ORT01", v."LAND1", v."BRSCH", v."VENDOR_GROUP"
        ORDER BY total_value DESC LIMIT 15
    """
    return query(sql)


def read_procurement_spend(args):
    """Procurement spend analytics"""
    gb = args.get("group_by", "vendor")
    w = ['po."STATUS" IN (\'RECEIVED\', \'CLOSED\')']
    if args.get("year"):
        w.append(f"""YEAR(po."ERDAT") = {args["year"]}""")
    where = "WHERE " + " AND ".join(w)

    if gb == "vendor":
        sql = f"""SELECT v."NAME1" AS vendor, v."LAND1" AS country, po."WAERK" AS currency,
                   COUNT(*) AS po_count, SUM(po."NETWR") AS total_spend
            FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
            {where} GROUP BY v."NAME1", v."LAND1", po."WAERK" ORDER BY total_spend DESC LIMIT 10"""
    elif gb == "category":
        sql = f"""SELECT pi."MATNR", pm."MATKL" AS category, SUM(pi."MENGE") AS qty, SUM(pi."NETWR") AS total_spend
            FROM "{S}"."PO_ITEMS" pi 
            JOIN "{S}"."PROC_MATERIALS" pm ON pi."MATNR" = pm."MATNR"
            JOIN "{S}"."PURCHASE_ORDERS" po ON pi."EBELN" = po."EBELN"
            {where} GROUP BY pi."MATNR", pm."MATKL" ORDER BY total_spend DESC LIMIT 10"""
    elif gb == "plant":
        sql = f"""SELECT po."WERKS" AS plant, p."name" AS plant_name, po."WAERK" AS currency,
                   COUNT(*) AS po_count, SUM(po."NETWR") AS total_spend
            FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."PLANTS" p ON po."WERKS" = p."WERKS"
            {where} GROUP BY po."WERKS", p."name", po."WAERK" ORDER BY total_spend DESC"""
    else:
        return [{"error": "Unknown group_by"}]
    return query(sql)


# ============================================================
# WRITE OPERATIONS — O2C
# ============================================================

def write_process_order(order_no):
    """Move sales order: Open→In Process or In Process→Completed"""
    current = query(f"""SELECT o."VBELN", o."STATUS", o."NETWR", o."WAERK", c."NAME1" 
        FROM "{S}"."SALES_ORDERS" o JOIN "{S}"."CUSTOMERS" c ON o."KUNNR" = c."KUNNR"
        WHERE o."VBELN" = '{order_no}'""")

    if not current or isinstance(current, dict):
        return f"❌ Order {order_no} not found"

    rec = current[0]
    old_status = rec["STATUS"]

    if old_status == "A":
        new_status, old_label, new_label = "B", "Open", "In Process"
    elif old_status == "B":
        new_status, old_label, new_label = "C", "In Process", "Completed"
    elif old_status == "C":
        return f"ℹ️  Order {order_no} is already Completed"
    else:
        return f"ℹ️  Order {order_no} has unknown status: {old_status}"

    result = execute(f"""UPDATE "{S}"."SALES_ORDERS" SET "STATUS" = '{new_status}' WHERE "VBELN" = '{order_no}'""")

    if result.get("success"):
        return f"""✅ Order {order_no} processed!
   Customer:  {rec['NAME1']}
   Value:     {fmt_currency(rec['NETWR'], rec['WAERK'])}
   Status:    {old_label} → {new_label}"""
    return f"❌ Failed: {result.get('error')}"


def write_record_payment(invoice_no, amount=None, status="PAID"):
    """Record payment for customer invoice"""
    current = query(f"""SELECT i."VBELN", i."TOTAL", i."WAERK", i."PAY_STATUS", c."NAME1"
        FROM "{S}"."INVOICES" i JOIN "{S}"."CUSTOMERS" c ON i."KUNNR" = c."KUNNR"
        WHERE i."VBELN" = '{invoice_no}'""")

    if not current or isinstance(current, dict):
        return f"❌ Invoice {invoice_no} not found"

    rec = current[0]
    old_status = rec["PAY_STATUS"]

    if old_status == "PAID":
        return f"ℹ️  Invoice {invoice_no} is already PAID"

    new_status = status.upper()
    result = execute(f"""UPDATE "{S}"."INVOICES" SET "PAY_STATUS" = '{new_status}' WHERE "VBELN" = '{invoice_no}'""")

    if result.get("success"):
        amt_str = fmt_currency(amount, rec['WAERK']) if amount else fmt_currency(rec['TOTAL'], rec['WAERK'])
        return f"""✅ Payment recorded!
   Invoice:   {invoice_no}
   Customer:  {rec['NAME1']}
   Amount:    {amt_str}
   Status:    {old_status} → {new_status}"""
    return f"❌ Failed: {result.get('error')}"


def write_update_delivery(delivery_no, new_status):
    """Update delivery status"""
    current = query(f"""SELECT d."VBELN", d."GI_STATUS", d."VGBEL", c."NAME1"
        FROM "{S}"."DELIVERIES" d JOIN "{S}"."CUSTOMERS" c ON d."KUNNR" = c."KUNNR"
        WHERE d."VBELN" = '{delivery_no}'""")

    if not current or isinstance(current, dict):
        return f"❌ Delivery {delivery_no} not found"

    rec = current[0]
    status_map = {"A": "Open", "B": "In Transit", "C": "Delivered"}
    old_label = status_map.get(rec["GI_STATUS"], rec["GI_STATUS"])
    new_label = status_map.get(new_status, new_status)

    gi_date_sql = f""", "GI_DATE" = '{TODAY}'""" if new_status == "C" else ""
    result = execute(
        f"""UPDATE "{S}"."DELIVERIES" SET "GI_STATUS" = '{new_status}'{gi_date_sql} WHERE "VBELN" = '{delivery_no}'""")

    if result.get("success"):
        return f"""✅ Delivery updated!
   Delivery:  {delivery_no}
   Order:     {rec['VGBEL']}
   Customer:  {rec['NAME1']}
   Status:    {old_label} → {new_label}"""
    return f"❌ Failed: {result.get('error')}"


# ============================================================
# WRITE OPERATIONS — P2P
# ============================================================

def write_approve_po(po_number):
    """Approve a purchase order"""
    current = query(f"""SELECT po."EBELN", po."STATUS", po."NETWR", po."WAERK", v."NAME1"
        FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{po_number}'""")

    if not current or isinstance(current, dict):
        return f"❌ PO {po_number} not found"

    rec = current[0]
    if rec["STATUS"] != "OPEN":
        return f"ℹ️  PO {po_number} is already {rec['STATUS']}"

    result = execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'APPROVED' WHERE "EBELN" = '{po_number}'""")
    if result.get("success"):
        return f"""✅ PO Approved!
   PO:       {po_number}
   Vendor:   {rec['NAME1']}
   Value:    {fmt_currency(rec['NETWR'], rec['WAERK'])}
   Status:   OPEN → APPROVED"""
    return f"❌ Failed: {result.get('error')}"


def write_receive_po(po_number):
    """Receive goods for a PO"""
    current = query(f"""SELECT po."EBELN", po."STATUS", po."NETWR", po."WAERK", v."NAME1"
        FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{po_number}'""")

    if not current or isinstance(current, dict):
        return f"❌ PO {po_number} not found"

    rec = current[0]
    if rec["STATUS"] in ["RECEIVED", "CLOSED"]:
        return f"ℹ️  PO {po_number} is already {rec['STATUS']}"

    # Update PO status
    execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'RECEIVED' WHERE "EBELN" = '{po_number}'""")
    # Update item received quantities
    execute(f"""UPDATE "{S}"."PO_ITEMS" SET "RECEIVED_QTY" = "MENGE" WHERE "EBELN" = '{po_number}'""")

    # Create GR record
    next_gr = query(f"""SELECT MAX(CAST("MBLNR" AS BIGINT)) + 1 AS next_id FROM "{S}"."GOODS_RECEIPTS" """)
    if isinstance(next_gr, list) and len(next_gr) > 0 and (next_gr[0].get("next_id") or next_gr[0].get("NEXT_ID")):
        val = next_gr[0].get("next_id") or next_gr[0].get("NEXT_ID");
        gr_num = str(int(val))
    else:
        gr_num = "5999999999"

    # Get vendor LIFNR
    po_data = query(f"""SELECT "LIFNR", "WERKS" FROM "{S}"."PURCHASE_ORDERS" WHERE "EBELN" = '{po_number}'""")
    if po_data:
        execute(f"""INSERT INTO "{S}"."GOODS_RECEIPTS" ("MBLNR","BLDAT","BUDAT","LIFNR","EBELN","WERKS","STATUS")
            VALUES ('{gr_num}', '{TODAY}', '{TODAY}', '{po_data[0]["LIFNR"]}', '{po_number}', '{po_data[0]["WERKS"]}', 'POSTED')""")

    return f"""✅ Goods Received!
   PO:       {po_number}
   GR:       {gr_num}
   Vendor:   {rec['NAME1']}
   Value:    {fmt_currency(rec['NETWR'], rec['WAERK'])}
   Status:   {rec['STATUS']} → RECEIVED
   GR Date:  {TODAY}"""


def write_pay_vendor(invoice_no, amount=None, status="PAID"):
    """Pay a vendor invoice"""
    current = query(f"""SELECT vi."BELNR", vi."TOTAL", vi."WAERK", vi."PAY_STATUS", v."NAME1"
        FROM "{S}"."VENDOR_INVOICES" vi JOIN "{S}"."VENDORS" v ON vi."LIFNR" = v."LIFNR"
        WHERE vi."BELNR" = '{invoice_no}'""")

    if not current or isinstance(current, dict):
        return f"❌ Vendor invoice {invoice_no} not found"

    rec = current[0]
    if rec["PAY_STATUS"] == "PAID":
        return f"ℹ️  Vendor invoice {invoice_no} is already PAID"

    result = execute(
        f"""UPDATE "{S}"."VENDOR_INVOICES" SET "PAY_STATUS" = '{status.upper()}' WHERE "BELNR" = '{invoice_no}'""")
    if result.get("success"):
        return f"""✅ Vendor Payment recorded!
   Invoice:  {invoice_no}
   Vendor:   {rec['NAME1']}
   Amount:   {fmt_currency(rec['TOTAL'], rec['WAERK'])}
   Status:   {rec['PAY_STATUS']} → {status.upper()}"""
    return f"❌ Failed: {result.get('error')}"


def write_close_po(po_number):
    """Close a PO (final step)"""
    result = execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'CLOSED' WHERE "EBELN" = '{po_number}'""")
    if result.get("success"):
        return f"✅ PO {po_number} closed."
    return f"❌ Failed: {result.get('error')}"


def preview_create_po(vendor_name, material_keyword, qty=1, plant=None, price=None, date=None):
    """Validate and preview PO details without inserting. Returns (preview_text, po_data) or (error_text, None)."""
    # Find vendor
    vendors = query(f"""SELECT "LIFNR", "NAME1", "LAND1", "CURRENCY" FROM "{S}"."VENDORS"
        WHERE UPPER("NAME1") LIKE '%{vendor_name.upper()}%' LIMIT 5""")
    if not vendors or isinstance(vendors, dict):
        return f"❌ Vendor '{vendor_name}' not found. Try: vendors", None
    if len(vendors) > 1:
        lines = [f"   Multiple vendors found. Please be more specific:"]
        for v in vendors:
            lines.append(f"   • {v['NAME1']} ({v['LAND1']})")
        return "\n".join(lines), None
    vendor = vendors[0]

    # Find material — match each word separately for better results
    mat_words = [w for w in material_keyword.upper().split() if len(w) > 1]
    if mat_words:
        word_conditions = " AND ".join([f"""UPPER("MAKTX") LIKE '%{w}%'""" for w in mat_words])
        materials = query(f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "MEINS" FROM "{S}"."PROC_MATERIALS"
            WHERE {word_conditions} LIMIT 5""")
    else:
        materials = []
    if not materials or isinstance(materials, dict):
        # Fallback: try material ID
        materials = query(f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "MEINS" FROM "{S}"."PROC_MATERIALS"
            WHERE UPPER("MATNR") LIKE '%{material_keyword.upper()}%' LIMIT 5""")
    if not materials or isinstance(materials, dict):
        return f"❌ Material '{material_keyword}' not found. Available categories: RAW_INK, PAPER, SUBSTRATE, COMPONENT, MRO, SERVICE", None
    if len(materials) > 1:
        lines = [f"   Multiple materials found. Please be more specific:"]
        for m in materials:
            lines.append(f"   • {m['MATNR']} — {m['MAKTX']} (${m['BASE_PRICE_USD']})")
        return "\n".join(lines), None
    mat = materials[0]

    # Determine plant & company code
    if not plant:
        if vendor["LAND1"] in ["US"]:
            plant, bukrs = "US01", "PW10"
        elif vendor["LAND1"] in ["DE", "NL", "GB", "FI", "AT", "CH"]:
            plant, bukrs = "NL01", "PW20"
        elif vendor["LAND1"] in ["IN"]:
            plant, bukrs = "IN01", "PW30"
        else:
            plant, bukrs = "US01", "PW10"
    else:
        bukrs_map = {"US01": "PW10", "US02": "PW10", "NL01": "PW20", "NL02": "PW20", "IN01": "PW30", "IN02": "PW30",
                     "IN03": "PW30"}
        bukrs = bukrs_map.get(plant.upper(), "PW10")

    currency_map = {"PW10": "USD", "PW20": "EUR", "PW30": "INR"}
    currency = currency_map.get(bukrs, "USD")
    fx_rates = {"USD": 1.0, "EUR": 0.92, "INR": 83.5}
    fx = fx_rates.get(currency, 1.0)

    ekorg = {"PW10": "1000", "PW20": "2000", "PW30": "3000"}.get(bukrs, "1000")

    unit_price = round(price * fx, 2) if price is not None else round(float(mat["BASE_PRICE_USD"]) * fx, 2)
    total_val = round(unit_price * qty, 2)
    delivery_date = date if date else (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    po_data = {
        "vendor": vendor, "mat": mat, "qty": qty, "plant": plant,
        "bukrs": bukrs, "ekorg": ekorg, "currency": currency,
        "unit_price": unit_price, "total_val": total_val,
        "delivery_date": delivery_date,
    }

    preview = f"""📋 PO Preview
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Vendor:       {vendor['NAME1']} ({vendor['LAND1']})
   Material:     {mat['MAKTX']} ({mat['MATNR']})
   Quantity:     {qty} {mat['MEINS']}
   Unit Price:   {fmt_currency(unit_price, currency)}
   Total Value:  {fmt_currency(total_val, currency)}
   Plant:        {plant}
   Company Code: {bukrs}
   Delivery Due: {delivery_date}
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Type 'yes' to confirm or 'no' to cancel."""

    return preview, po_data


def confirm_create_po(po_data):
    """Execute the PO creation using validated data from preview_create_po."""
    vendor = po_data["vendor"]
    mat = po_data["mat"]
    qty = po_data["qty"]
    plant = po_data["plant"]
    bukrs = po_data["bukrs"]
    ekorg = po_data["ekorg"]
    currency = po_data["currency"]
    unit_price = po_data["unit_price"]
    total_val = po_data["total_val"]
    delivery_date = po_data["delivery_date"]

    # Generate next PO number
    next_po = query(f"""SELECT MAX(CAST("EBELN" AS BIGINT)) + 1 AS next_id FROM "{S}"."PURCHASE_ORDERS" """)
    if isinstance(next_po, list) and len(next_po) > 0 and (next_po[0].get("next_id") or next_po[0].get("NEXT_ID")):
        val = next_po[0].get("next_id") or next_po[0].get("NEXT_ID");
        po_num = str(int(val))
    else:
        po_num = "4500099999"

    # Insert PO header
    result = execute(f"""INSERT INTO "{S}"."PURCHASE_ORDERS"
        ("EBELN","BSART","LIFNR","BUKRS","EKORG","ERDAT","NETWR","WAERK","EINDT","STATUS","WERKS")
        VALUES ('{po_num}', 'NB', '{vendor["LIFNR"]}', '{bukrs}', '{ekorg}', '{TODAY}', {total_val}, '{currency}', '{delivery_date}', 'OPEN', '{plant}')""")

    if not result.get("success"):
        return f"❌ PO creation failed: {result.get('error')}"

    # Insert PO item
    execute(f"""INSERT INTO "{S}"."PO_ITEMS"
        ("EBELN","EBELP","MATNR","TXZ01","MENGE","MEINS","NETPR","NETWR","WAERK","WERKS","LGORT","RECEIVED_QTY")
        VALUES ('{po_num}', '00010', '{mat["MATNR"]}', '{mat["MAKTX"]}', {qty}, '{mat["MEINS"]}', {unit_price}, {total_val}, '{currency}', '{plant}', '0001', 0)""")

    return f"""✅ Purchase Order Created!
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   PO Number:    {po_num}
   Vendor:       {vendor['NAME1']} ({vendor['LAND1']})
   Material:     {mat['MAKTX']}
   Quantity:     {qty} {mat['MEINS']}
   Unit Price:   {fmt_currency(unit_price, currency)}
   Total Value:  {fmt_currency(total_val, currency)}
   Plant:        {plant}
   Delivery Due: {delivery_date}
   Status:       OPEN
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Next steps: approve po {po_num} → receive goods {po_num}"""


def write_create_sales_order(customer_name, material_keyword, qty=1):
    """Create a new sales order"""
    # Find customer
    customers = query(f"""SELECT "KUNNR", "NAME1", "LAND1", "BUKRS", "VKORG" FROM "{S}"."CUSTOMERS" 
        WHERE UPPER("NAME1") LIKE '%{customer_name.upper()}%' LIMIT 5""")
    if not customers or isinstance(customers, dict):
        return f"❌ Customer '{customer_name}' not found. Try: customers"
    if len(customers) > 1:
        lines = [f"   Multiple customers found. Please be more specific:"]
        for c in customers:
            lines.append(f"   • {c['NAME1']} ({c['LAND1']})")
        return "\n".join(lines)
    cust = customers[0]

    # Find material (from sales materials) — match each word separately
    mat_words = [w for w in material_keyword.upper().split() if len(w) > 1]
    if mat_words:
        word_conditions = " AND ".join([f"""UPPER("MAKTX") LIKE '%{w}%'""" for w in mat_words])
        materials = query(f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "SPART" FROM "{S}"."MATERIALS" 
            WHERE {word_conditions} LIMIT 5""")
    else:
        materials = []
    if not materials or isinstance(materials, dict):
        materials = query(f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "SPART" FROM "{S}"."MATERIALS" 
            WHERE UPPER("MATNR") LIKE '%{material_keyword.upper()}%' LIMIT 5""")
    if not materials or isinstance(materials, dict):
        return f"❌ Product '{material_keyword}' not found. Try: products"
    if len(materials) > 1:
        lines = [f"   Multiple products found. Please be more specific:"]
        for m in materials:
            lines.append(f"   • {m['MATNR']} — {m['MAKTX']} (${m['BASE_PRICE_USD']})")
        return "\n".join(lines)
    mat = materials[0]

    bukrs = cust["BUKRS"]
    vkorg = cust["VKORG"]
    currency_map = {"PW10": "USD", "PW20": "EUR", "PW30": "INR"}
    currency = currency_map.get(bukrs, "USD")
    fx_rates = {"USD": 1.0, "EUR": 0.92, "INR": 83.5}
    fx = fx_rates.get(currency, 1.0)

    # Determine order type from material division
    spart = str(mat.get("SPART", "10"))
    if spart == "10":
        auart = "ZOR"  # Equipment
    elif spart == "20":
        auart = "ZCO"  # Consumables
    elif spart == "30":
        auart = "ZCO"  # Software
    elif spart == "40":
        auart = "ZSO"  # Service
    else:
        auart = "ZCO"

    # Get plant based on region
    plant_map = {"PW10": "US01", "PW20": "NL01", "PW30": "IN01"}
    werks = plant_map.get(bukrs, "US01")

    # Generate next SO number
    next_so = query(f"""SELECT MAX(CAST("VBELN" AS BIGINT)) + 1 AS next_id FROM "{S}"."SALES_ORDERS" """)
    if isinstance(next_so, list) and len(next_so) > 0 and (next_so[0].get("next_id") or next_so[0].get("NEXT_ID")):
        val = next_so[0].get("next_id") or next_so[0].get("NEXT_ID");
        so_num = str(int(val))
    else:
        so_num = "4000099999"

    unit_price = round(float(mat["BASE_PRICE_USD"]) * fx, 2)
    total_val = round(unit_price * qty, 2)
    delivery_date = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")

    reps = ["Sarah Johnson", "Michael Chen", "Raj Patel", "Emma van der Berg", "Tom Williams"]
    rep = random.choice(reps)

    # Insert SO header
    result = execute(f"""INSERT INTO "{S}"."SALES_ORDERS" 
        ("VBELN","AUART","VKORG","KUNNR","ERDAT","ERNAM","NETWR","WAERK","BSTNK","VDATU","STATUS","BUKRS")
        VALUES ('{so_num}', '{auart}', '{vkorg}', '{cust["KUNNR"]}', '{TODAY}', '{rep}', {total_val}, '{currency}', 'PO-{so_num[-4:]}', '{delivery_date}', 'A', '{bukrs}')""")

    if not result.get("success"):
        return f"❌ Order creation failed: {result.get('error')}"

    # Insert SO item
    execute(f"""INSERT INTO "{S}"."SALES_ORDER_ITEMS" 
        ("VBELN","POSNR","MATNR","ARKTX","KWMENG","NETWR","NETPR","WAERK","WERKS","DISCOUNT_PCT")
        VALUES ('{so_num}', '000010', '{mat["MATNR"]}', '{mat["MAKTX"]}', {qty}, {total_val}, {unit_price}, '{currency}', '{werks}', 0)""")

    type_labels = {"ZOR": "Equipment", "ZCO": "Consumables", "ZSO": "Service/AMC", "ZSP": "Spare Parts"}

    return f"""✅ Sales Order Created!
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Order Number:  {so_num}
   Type:          {type_labels.get(auart, auart)}
   Customer:      {cust['NAME1']} ({cust['LAND1']})
   Product:       {mat['MAKTX']}
   Quantity:      {qty}
   Unit Price:    {fmt_currency(unit_price, currency)}
   Total Value:   {fmt_currency(total_val, currency)}
   Sales Rep:     {rep}
   Delivery Due:  {delivery_date}
   Status:        Open
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Next steps: process order {so_num}"""


# ============================================================
# DASHBOARD / SUMMARY
# ============================================================

def show_dashboard():
    """Show overall business dashboard"""
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
        "═" * 60,
        "📊 PRINTWORKS GLOBAL SOLUTIONS — BUSINESS DASHBOARD",
        "═" * 60,
        "",
        "📦 ORDER-TO-CASH (O2C):",
    ]
    if o2c and not isinstance(o2c, dict):
        r = o2c[0]
        lines.append(
            f"   Sales Orders:  {r['open_orders']} Open | {r['processing']} Processing | {r['completed']} Completed | {r['total']} Total")
    if dlv and not isinstance(dlv, dict):
        r = dlv[0]
        lines.append(
            f"   Deliveries:    {r['open_dlv']} Open | {r['in_transit']} In Transit | {r['delivered']} Delivered")

    lines.append(f"\n   Customer Invoices (AR):")
    if ar and not isinstance(ar, dict):
        for r in ar:
            lines.append(f"     {r['PAY_STATUS']:10s}: {r['cnt']:>5,} invoices")

    lines.append(f"\n🛒 PROCURE-TO-PAY (P2P):")
    if p2p and not isinstance(p2p, dict):
        for r in p2p:
            lines.append(f"   PO {r['STATUS']:20s}: {r['cnt']:>5,} orders")

    lines.append(f"\n   Vendor Invoices (AP):")
    if ap and not isinstance(ap, dict):
        for r in ap:
            lines.append(f"     {r['PAY_STATUS']:10s}: {r['cnt']:>5,} invoices")

    return "\n".join(lines)


# ============================================================
# INTENT PARSER
# ============================================================

def extract_number(text, prefix=""):
    """Extract document numbers like 4000000xxx, 4500000xxx, 8000000xxx, 9000000xxx, 5100000xxx"""
    matches = re.findall(r'\b(\d{10})\b', text)
    if matches:
        return matches[0]
    # Try partial numbers
    matches = re.findall(r'#?(\d{7,})', text)
    if matches:
        return matches[0]
    return None


def extract_name(text, keywords_to_remove):
    """Extract a name/entity from text"""
    clean = text.lower()
    for kw in keywords_to_remove:
        clean = clean.replace(kw, "")
    # Remove common words
    for w in ["show", "me", "the", "for", "from", "of", "all", "get", "find", "list", "display",
              "what", "are", "is", "in", "with", "by", "to", "and", "my", "our", "any",
              "read", "help", "please", "can", "you", "give", "tell", "about", "check", "look",
              "see", "up", "recent", "latest", "new", "old", "last", "first", "top", "best",
              "pending", "their", "those", "these", "that", "this", "some", "how", "many",
              "total", "current", "today", "now", "here", "there", "want", "need", "like"]:
        clean = re.sub(rf'\b{w}\b', '', clean)
    clean = clean.strip().strip("?").strip()
    if clean and len(clean) > 2:
        return clean
    return None


def parse_and_execute(user_input):
    """Parse user intent and execute appropriate function"""
    q = user_input.lower().strip()

    # ── DASHBOARD ──
    if q in ["dashboard", "summary", "overview", "status", "home"]:
        return show_dashboard()

    # ── HELP ──
    if q in ["help", "?", "commands"]:
        return HELP_TEXT

    # ── WRITE: Process Order ──
    if any(w in q for w in ["process order", "advance order", "move order", "complete order"]):
        num = extract_number(user_input)
        if num:
            return write_process_order(num)
        return "❓ Please provide the order number. Example: process order 4000000123"

    # ── WRITE: Record Customer Payment ──
    if any(w in q for w in ["record payment", "mark paid", "payment received", "customer paid", "mark as paid"]):
        num = extract_number(user_input)
        if num:
            if "partial" in q:
                return write_record_payment(num, status="PARTIAL")
            return write_record_payment(num)
        return "❓ Please provide the invoice number. Example: record payment 9000000123"

    # ── WRITE: Update Delivery ──
    if any(w in q for w in
           ["ship delivery", "mark shipped", "mark delivered", "dispatch", "goods issue", "confirm delivery"]):
        num = extract_number(user_input)
        if num:
            if "deliver" in q or "confirm" in q:
                return write_update_delivery(num, "C")
            else:
                return write_update_delivery(num, "B")
        return "❓ Please provide the delivery number. Example: ship delivery 8000000123"

    # ── WRITE: Approve PO ──
    if any(w in q for w in ["approve po", "approve purchase", "po approved"]):
        num = extract_number(user_input)
        if num:
            return write_approve_po(num)
        return "❓ Please provide the PO number. Example: approve po 4500000123"

    # ── WRITE: Receive Goods ──
    if any(w in q for w in ["receive goods", "receive po", "goods received", "gr for", "post gr", "receive material"]):
        num = extract_number(user_input)
        if num:
            return write_receive_po(num)
        return "❓ Please provide the PO number. Example: receive goods 4500000123"

    # ── WRITE: Pay Vendor ──
    if any(w in q for w in ["pay vendor", "vendor payment", "pay supplier", "vendor paid", "pay invoice to"]):
        num = extract_number(user_input)
        if num:
            if "partial" in q:
                return write_pay_vendor(num, status="PARTIAL")
            return write_pay_vendor(num)
        return "❓ Please provide the vendor invoice number. Example: pay vendor 5100000123"

    # ── WRITE: Close PO ──
    if any(w in q for w in ["close po", "close purchase"]):
        num = extract_number(user_input)
        if num:
            return write_close_po(num)
        return "❓ Please provide the PO number."

    # ── WRITE: Create PO ──
    if any(w in q for w in
           ["create po", "create purchase order", "new po", "new purchase order", "raise po", "raise purchase"]):
        # Parse: create po for [vendor] - [material] - [qty] - price [p] - date [d] - plant [pl]
        # Examples:
        #   create po for Sun Chemical - UV Ink Black - 50
        #   create po for Sun Chemical - UV Ink Black - 50 - price 120 - date 2026-03-15 - plant NL01
        #   create po for Xaar - printhead - 10 - price 85
        parts = re.split(r'\s+(?:for|from|to)\s+', q, maxsplit=1)
        if len(parts) < 2:
            # Try without preposition
            clean = q
            for kw in ["create po", "create purchase order", "new po", "new purchase order", "raise po",
                       "raise purchase"]:
                clean = clean.replace(kw, "").strip()
            parts = [q, clean]

        detail = parts[1] if len(parts) > 1 else ""

        if not detail or len(detail) < 3:
            return """❓ Please specify vendor, material and quantity.
   Format: create po for [vendor] - [material] - [quantity]
   Optional: - price [amount] - date [YYYY-MM-DD] - plant [code]
   Example: create po for Sun Chemical - UV Ink Black - 50
   Example: create po for Sun Chemical - UV Ink Black - 50 - price 120 - date 2026-03-15 - plant NL01

   Tip: type 'vendors' to see available vendors
        type 'procurement materials' to see materials"""

        # Extract optional flags: price, date, plant (before main parsing)
        po_price = None
        po_date = None
        po_plant = None

        price_match = re.search(r'\bprice\s+([\d.]+)', detail, re.IGNORECASE)
        if price_match:
            po_price = float(price_match.group(1))
            detail = detail[:price_match.start()] + detail[price_match.end():]

        date_match = re.search(r'\bdate\s+(\d{4}-\d{2}-\d{2})', detail, re.IGNORECASE)
        if date_match:
            po_date = date_match.group(1)
            detail = detail[:date_match.start()] + detail[date_match.end():]

        plant_match = re.search(r'\bplant\s+(\w+)', detail, re.IGNORECASE)
        if plant_match:
            po_plant = plant_match.group(1)
            detail = detail[:plant_match.start()] + detail[plant_match.end():]

        # Clean up leftover dashes/spaces from extracted flags
        detail = re.sub(r'\s*-\s*$', '', detail).strip()

        # Try to extract quantity (last number in string)
        qty_match = re.findall(r'\b(\d+)\b', detail)
        qty = int(qty_match[-1]) if qty_match else 1

        # Split into vendor and material by common separators
        detail_clean = re.sub(r'\b\d+\b', '', detail).strip()

        # Clean up trailing/leading dashes and extra spaces
        detail_clean = re.sub(r'[\s\-]+$', '', detail_clean).strip()
        detail_clean = re.sub(r'\s*-\s*-\s*', ' - ', detail_clean)

        # Try splitting by dash, comma, or common patterns
        if ' - ' in detail_clean:
            segments = [s.strip().strip('-').strip() for s in detail_clean.split(' - ') if s.strip().strip('-').strip()]
        elif ',' in detail_clean:
            segments = [s.strip() for s in detail_clean.split(',') if s.strip()]
        else:
            # Heuristic: first 1-3 words = vendor, rest = material
            words = detail_clean.split()
            if len(words) >= 4:
                segments = [' '.join(words[:2]), ' '.join(words[2:])]
            elif len(words) >= 2:
                segments = [words[0], ' '.join(words[1:])]
            else:
                return f"❓ Could not parse vendor and material from: {detail}\n   Try: create po for Sun Chemical - UV Ink Black - 50"

        if len(segments) >= 2:
            vendor_name = segments[0].strip()
            material_kw = segments[1].strip()
        else:
            return f"❓ Please specify both vendor and material.\n   Example: create po for Sun Chemical - UV Ink Black - 50"

        return preview_create_po(vendor_name, material_kw, qty, plant=po_plant, price=po_price, date=po_date)

    # ── WRITE: Create Sales Order ──
    if any(w in q for w in ["create order", "create sales order", "new order", "new sales order", "book order"]):
        parts = re.split(r'\s+(?:for|from|to)\s+', q, maxsplit=1)
        if len(parts) < 2:
            clean = q
            for kw in ["create order", "create sales order", "new order", "new sales order", "book order"]:
                clean = clean.replace(kw, "").strip()
            parts = [q, clean]

        detail = parts[1] if len(parts) > 1 else ""

        if not detail or len(detail) < 3:
            return """❓ Please specify customer, product and quantity.
   Format: create order for [customer] - [product] - [quantity]
   Example: create order for ITC Limited - ProJet X7 Digital Press - 2
   Example: new order for 3M Company - UV Cyan Ink - 100

   Tip: type 'customers' to see available customers
        type 'products' to see materials"""

        qty_match = re.findall(r'\b(\d+)\b', detail)
        qty = int(qty_match[-1]) if qty_match else 1
        detail_clean = re.sub(r'\b\d+\b', '', detail).strip()
        detail_clean = re.sub(r'[\s\-]+$', '', detail_clean).strip()
        detail_clean = re.sub(r'\s*-\s*-\s*', ' - ', detail_clean)

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
                return f"❓ Could not parse customer and product.\n   Try: create order for ITC Limited - UV Cyan Ink - 100"

        if len(segments) >= 2:
            return write_create_sales_order(segments[0].strip(), segments[1].strip(), qty)
        return f"❓ Please specify both customer and product.\n   Example: create order for 3M - UV Cyan Ink - 50"

    # ── READ: Procurement Materials ──
    if any(w in q for w in
           ["procurement material", "proc material", "raw material", "what can i buy", "buying material"]):
        sql = f"""SELECT "MATNR" AS material_id, "MAKTX" AS description, "MATKL" AS category, 
                   "MEINS" AS uom, "BASE_PRICE_USD" AS price_usd 
            FROM "{S}"."PROC_MATERIALS" ORDER BY "MATKL", "MATNR" """
        return fmt_table(query(sql), max_rows=35)

    # ── READ: Revenue ──
    if any(w in q for w in ["revenue", "sales summary", "total sales", "how much"]):
        args = {}
        if "customer" in q or "client" in q:
            args["group_by"] = "customer"
        elif "product" in q:
            args["group_by"] = "product"
        elif "month" in q:
            args["group_by"] = "month"
        elif "quarter" in q:
            args["group_by"] = "quarter"
        else:
            args["group_by"] = "region"
        for y in ["2023", "2024", "2025", "2026"]:
            if y in q: args["year"] = y
        if "us" in q.split():
            args["region"] = "us"
        elif "emea" in q or "europe" in q:
            args["region"] = "emea"
        elif "india" in q:
            args["region"] = "india"
        return fmt_table(read_revenue(args))

    # ── READ: Procurement Spend ──
    if any(w in q for w in ["procurement spend", "spend by", "purchase spend", "buying spend"]):
        args = {}
        if "vendor" in q or "supplier" in q:
            args["group_by"] = "vendor"
        elif "category" in q:
            args["group_by"] = "category"
        elif "plant" in q:
            args["group_by"] = "plant"
        else:
            args["group_by"] = "vendor"
        return fmt_table(read_procurement_spend(args))

    # ── READ: Customer Invoices ──
    if any(w in q for w in ["invoice", "billing", "receivable", "ar "]) and "vendor" not in q:
        args = {}
        if "overdue" in q:
            args["pay_status"] = "OVERDUE"
        elif "open" in q or "outstanding" in q or "unpaid" in q:
            args["pay_status"] = "OPEN"
        elif "paid" in q:
            args["pay_status"] = "PAID"
        elif "partial" in q:
            args["pay_status"] = "PARTIAL"
        if "us" in q.split():
            args["region"] = "us"
        elif "emea" in q or "europe" in q:
            args["region"] = "emea"
        elif "india" in q:
            args["region"] = "india"
        name = extract_name(q, ["invoice", "billing", "overdue", "open", "paid", "partial"])
        if name and len(name) > 3: args["customer"] = name
        return fmt_table(read_invoices(args))

    # ── READ: Vendor Invoices ──
    if any(w in q for w in ["vendor invoice", "vendor bill", "ap ", "accounts payable", "supplier invoice"]):
        args = {}
        if "overdue" in q:
            args["pay_status"] = "OVERDUE"
        elif "open" in q or "unpaid" in q:
            args["pay_status"] = "OPEN"
        elif "paid" in q:
            args["pay_status"] = "PAID"
        return fmt_table(read_vendor_invoices(args))

    # ── READ: Deliveries ──
    if any(w in q for w in ["delivery", "deliveries", "shipment", "shipped", "dispatch"]):
        args = {}
        num = extract_number(user_input)
        if num: args["order"] = num
        if "open" in q or "pending" in q:
            args["status"] = "open"
        elif "transit" in q:
            args["status"] = "transit"
        elif "delivered" in q:
            args["status"] = "delivered"
        name = extract_name(q, ["delivery", "deliveries", "shipment", "open", "pending", "transit", "delivered"])
        if name and len(name) > 3: args["customer"] = name
        return fmt_table(read_deliveries(args))

    # ── READ: Purchase Orders ──
    if any(w in q for w in ["purchase order", "po ", "pos ", "procurement order", "buying order"]):
        args = {}
        if "open" in q:
            args["status"] = "OPEN"
        elif "approved" in q:
            args["status"] = "APPROVED"
        elif "received" in q:
            args["status"] = "RECEIVED"
        elif "closed" in q:
            args["status"] = "CLOSED"
        name = extract_name(q, ["purchase order", "po", "open", "approved", "received", "closed"])
        if name and len(name) > 3: args["vendor"] = name
        for y in ["2023", "2024", "2025", "2026"]:
            if y in q: args["year"] = y
        return fmt_table(read_purchase_orders(args))

    # ── READ: PO Details ──
    if q.startswith("po ") or q.startswith("po#"):
        num = extract_number(user_input)
        if num:
            header, items = read_po_details(num)
            result = f"📋 Purchase Order Details:\n{fmt_table(header)}\n\n   Line Items:\n{fmt_table(items)}"
            return result

    # ── READ: Order Details ──
    if q.startswith("order ") or q.startswith("order#"):
        num = extract_number(user_input)
        if num:
            header, items = read_order_details(num)
            result = f"📋 Sales Order Details:\n{fmt_table(header)}\n\n   Line Items:\n{fmt_table(items)}"
            return result

    # ── READ: Goods Receipts ──
    if any(w in q for w in ["goods receipt", "gr ", "material receipt"]):
        args = {}
        num = extract_number(user_input)
        if num: args["po"] = num
        name = extract_name(q, ["goods receipt", "gr", "material receipt"])
        if name and len(name) > 3: args["vendor"] = name
        return fmt_table(read_goods_receipts(args))

    # ── READ: Customers ──
    if any(w in q for w in ["customer", "client", "buyer", "account"]) and "invoice" not in q:
        args = {}
        if "india" in q:
            args["country"] = "in"
        elif "us" in q.split():
            args["country"] = "us"
        elif "europe" in q or "dutch" in q:
            args["country"] = "nl"
        if "pharma" in q:
            args["industry"] = "PHRM"
        elif "packaging" in q:
            args["industry"] = "PACK"
        elif "publishing" in q:
            args["industry"] = "PUBL"
        elif "fmcg" in q:
            args["industry"] = "FMCG"
        elif "textile" in q:
            args["industry"] = "TXTL"
        name = extract_name(q, ["customer", "client", "buyer", "india", "us", "europe", "pharma", "packaging",
                                "publishing", "fmcg"])
        if name and len(name) > 3: args["customer"] = name
        return fmt_table(read_customers(args))

    # ── READ: Vendors / Suppliers ──
    if any(w in q for w in ["vendor", "supplier"]) and "invoice" not in q and "pay" not in q:
        args = {}
        name = extract_name(q, ["vendor", "supplier", "show", "list"])
        if name and len(name) > 3: args["vendor"] = name
        return fmt_table(read_vendors(args))

    # ── READ: Sales Orders (default) ──
    if any(w in q for w in ["order", "orders", "sales"]):
        args = {}
        if "open" in q:
            args["status"] = "open"
        elif "process" in q:
            args["status"] = "process"
        elif "completed" in q or "complete" in q:
            args["status"] = "completed"
        if "equipment" in q:
            args["type"] = "equipment"
        elif "consumable" in q:
            args["type"] = "consumable"
        elif "service" in q or "amc" in q:
            args["type"] = "service"
        elif "spare" in q:
            args["type"] = "spare"
        name = extract_name(q, ["order", "orders", "open", "completed", "equipment", "consumable", "service", "spare",
                                "sales"])
        if name and len(name) > 3: args["customer"] = name
        if "us" in q.split():
            args["region"] = "us"
        elif "emea" in q or "europe" in q:
            args["region"] = "emea"
        elif "india" in q:
            args["region"] = "india"
        for y in ["2023", "2024", "2025", "2026"]:
            if y in q: args["year"] = y
        return fmt_table(read_sales_orders(args))

    # ── READ: Products ──
    if any(w in q for w in ["product", "material", "printer", "ink", "toner", "top selling", "best selling"]):
        args = {"group_by": "product"}
        return fmt_table(read_revenue({"group_by": "product"}))

    # ── FALLBACK ──
    return "❓ I didn't understand that. Type 'help' for available commands or 'dashboard' for overview."


# ============================================================
# HELP TEXT
# ============================================================

HELP_TEXT = """
╔══════════════════════════════════════════════════════════════════╗
║  PRINTWORKS SAP ASSISTANT — COMMAND GUIDE                       ║
╚══════════════════════════════════════════════════════════════════╝

📊 DASHBOARD
   dashboard / summary / overview

═══ ORDER-TO-CASH (O2C) — READ ═══
   orders                          → Recent sales orders
   open orders                     → Open sales orders
   orders for ITC Limited          → Orders by customer
   equipment orders in US          → Filter by type & region
   order 4000000123                → Order detail with line items
   deliveries                      → Recent deliveries
   open deliveries                 → Pending shipments
   invoices                        → Customer invoices
   overdue invoices                → Past-due customer invoices
   revenue by region               → Revenue analytics
   revenue by customer 2025        → Top customers
   revenue by quarter              → Quarterly trends
   customers                       → Customer list
   indian pharma customers         → Filter customers

═══ ORDER-TO-CASH (O2C) — WRITE ═══
   process order 4000000123        → Open → In Process → Completed
   record payment 9000000123       → Mark invoice as PAID
   record payment 9000000123 partial → Mark as PARTIAL
   ship delivery 8000000123        → Mark as In Transit
   confirm delivery 8000000123     → Mark as Delivered
   create order for ITC - UV Ink - 50 → Create new sales order

═══ PROCURE-TO-PAY (P2P) — READ ═══
   purchase orders                 → Recent POs
   open purchase orders            → Pending POs
   po 4500000123                   → PO detail with line items
   vendors                         → Vendor list
   vendor invoices                 → AP invoices
   open vendor invoices            → Unpaid vendor bills
   goods receipts                  → GR list
   procurement spend by vendor     → Spend analytics
   procurement spend by plant      → Spend by location
   procurement materials           → Materials you can buy

═══ PROCURE-TO-PAY (P2P) — WRITE ═══
   create po for Sun Chemical - UV Ink - 50 → Create new PO
   approve po 4500000123           → OPEN → APPROVED
   receive goods 4500000123        → Receive + create GR
   pay vendor 5100000123           → Mark vendor invoice PAID
   close po 4500000123             → Close completed PO

Type 'quit' to exit, 'clear' to start fresh
"""


# ============================================================
# MAIN
# ============================================================

def main():
    print("═" * 65)
    print("🖨️  PrintWorks Global Solutions — SAP Business Assistant")
    print("   Full O2C (Order-to-Cash) + P2P (Procure-to-Pay)")
    print("═" * 65)

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{S}"."SALES_ORDERS"')
        so = cur.fetchone()[0]
        cur.execute(f'SELECT COUNT(*) FROM "{S}"."PURCHASE_ORDERS"')
        po = cur.fetchone()[0]
        cur.close()
        conn.close()
        print(f"\n   ✅ HANA Cloud: {so:,} sales orders + {po:,} purchase orders loaded")
    except Exception as e:
        print(f"\n   ❌ HANA connection failed: {e}")
        return

    print(f"   📅 Today: {TODAY}")
    print(f"\n   Type 'help' for commands, 'dashboard' for overview\n")

    while True:
        try:
            user_input = input("🧑 You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye! 👋")
            break

        if not user_input:
            continue
        if user_input.lower() in ["quit", "exit", "bye"]:
            print("Bye! 👋")
            break
        if user_input.lower() == "clear":
            print("🔄 Cleared.\n")
            continue

        print()
        result = parse_and_execute(user_input)
        print(result)
        print()


if __name__ == "__main__":
    main()