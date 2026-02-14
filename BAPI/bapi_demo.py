"""
SAP BAPI Simulation Layer for PrintWorks Global Solutions
Mimics real SAP BAPI interfaces with validation, document flow, and BAPIRET2 messages.

In production, these functions are replaced with PyRFC calls to real SAP system:
    from pyrfc import Connection
    conn = Connection(ashost='sap-server', ...)
    result = conn.call('BAPI_PO_CREATE1', ...)

For demo, they execute equivalent logic against HANA Cloud directly.
"""

from datetime import datetime, timedelta
from hdbcli import dbapi
import random

# ============================================================
# CONFIG
# ============================================================

HANA_CONFIG = {
    "address": "3e0addec-ef25-4880-8812-637e3d3a99f7.hna1.prod-us10.hanacloud.ondemand.com",
    "port": 443,
    "user": "DBADMIN",
    "password": "RamAI001Y@",  # ← SET YOUR PASSWORD
    "encrypt": True,
    "sslValidateCertificate": False,
}

SCHEMA = "PRINTWORKS"
TODAY = datetime.now().strftime("%Y-%m-%d")


def get_connection():
    return dbapi.connect(
        address=HANA_CONFIG["address"], port=HANA_CONFIG["port"],
        user=HANA_CONFIG["user"], password=HANA_CONFIG["password"],
        encrypt=True, sslValidateCertificate=False,
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


S = SCHEMA


# ============================================================
# BAPIRET2 — Standard SAP Return Message Structure
# ============================================================

class BAPIRET2:
    """SAP standard return message format"""

    def __init__(self):
        self.messages = []

    def success(self, msg, id="", number=""):
        self.messages.append({"TYPE": "S", "ID": id, "NUMBER": number, "MESSAGE": msg})

    def error(self, msg, id="", number=""):
        self.messages.append({"TYPE": "E", "ID": id, "NUMBER": number, "MESSAGE": msg})

    def warning(self, msg, id="", number=""):
        self.messages.append({"TYPE": "W", "ID": id, "NUMBER": number, "MESSAGE": msg})

    def info(self, msg, id="", number=""):
        self.messages.append({"TYPE": "I", "ID": id, "NUMBER": number, "MESSAGE": msg})

    def has_errors(self):
        return any(m["TYPE"] == "E" for m in self.messages)

    def format(self):
        icons = {"S": "✅", "E": "❌", "W": "⚠️", "I": "ℹ️"}
        lines = []
        for m in self.messages:
            icon = icons.get(m["TYPE"], "")
            prefix = f"[{m['ID']}-{m['NUMBER']}] " if m["ID"] else ""
            lines.append(f"   {icon} {prefix}{m['MESSAGE']}")
        return "\n".join(lines)


# ============================================================
# BAPI_PO_CREATE1 — Create Purchase Order
# ============================================================

def BAPI_PO_CREATE1(POHEADER, POITEM, POSCHEDULE=None, POACCOUNT=None):
    """
    Create Purchase Order

    Real SAP: conn.call('BAPI_PO_CREATE1', POHEADER={...}, POITEM=[{...}])

    Parameters (mirroring real BAPI):
        POHEADER: dict with DOC_TYPE, VENDOR, PURCH_ORG, PUR_GROUP, COMP_CODE
        POITEM: list of dicts with PO_ITEM, MATERIAL, QUANTITY, PLANT, NET_PRICE

    Returns:
        dict with EXPHEADER (PO number), RETURN (BAPIRET2 messages)
    """
    ret = BAPIRET2()

    # ── Validation 1: Vendor exists ──
    vendor = query(f"""SELECT "LIFNR", "NAME1", "LAND1", "VENDOR_GROUP" 
        FROM "{S}"."VENDORS" WHERE "LIFNR" = '{POHEADER.get("VENDOR", "")}'""")
    if not vendor or isinstance(vendor, dict):
        # Try by name
        vendor = query(f"""SELECT "LIFNR", "NAME1", "LAND1", "VENDOR_GROUP"
            FROM "{S}"."VENDORS" WHERE UPPER("NAME1") LIKE '%{POHEADER.get("VENDOR", "").upper()}%' LIMIT 1""")
    if not vendor or isinstance(vendor, dict):
        ret.error(f"Vendor '{POHEADER.get('VENDOR')}' not found in vendor master", "ME", "006")
        return {"EXPHEADER": {}, "RETURN": ret}
    vendor = vendor[0]
    ret.info(f"Vendor {vendor['LIFNR']} - {vendor['NAME1']} validated", "ME", "001")

    # ── Validation 2: Company code valid ──
    comp_code = POHEADER.get("COMP_CODE", "")
    valid_cc = {"PW10": "USD", "PW20": "EUR", "PW30": "INR"}
    if comp_code not in valid_cc:
        # Auto-determine from vendor country
        cc_map = {"US": "PW10", "DE": "PW20", "NL": "PW20", "GB": "PW20", "IN": "PW30", "JP": "PW10"}
        comp_code = cc_map.get(vendor["LAND1"], "PW10")
        ret.info(f"Company code auto-determined: {comp_code}", "ME", "002")

    currency = valid_cc[comp_code]
    ekorg = {"PW10": "1000", "PW20": "2000", "PW30": "3000"}[comp_code]
    fx = {"USD": 1.0, "EUR": 0.92, "INR": 83.5}.get(currency, 1.0)

    # ── Validation 3: Purchasing org matches company code ──
    req_ekorg = POHEADER.get("PURCH_ORG", ekorg)
    if req_ekorg != ekorg:
        ret.warning(f"Purchasing org {req_ekorg} overridden to {ekorg} for company code {comp_code}", "ME", "003")

    # ── Validation 4: Document type valid ──
    doc_type = POHEADER.get("DOC_TYPE", "NB")
    valid_types = ["NB", "FO", "UB", "ZNB"]
    if doc_type not in valid_types:
        ret.error(f"Invalid document type '{doc_type}'. Valid: {', '.join(valid_types)}", "ME", "007")
        return {"EXPHEADER": {}, "RETURN": ret}

    # ── Validate line items ──
    if not POITEM or len(POITEM) == 0:
        ret.error("No line items provided", "ME", "010")
        return {"EXPHEADER": {}, "RETURN": ret}

    validated_items = []
    total_value = 0

    for idx, item in enumerate(POITEM):
        item_no = item.get("PO_ITEM", f"{(idx + 1) * 10:05d}")

        # Material validation
        mat_id = item.get("MATERIAL", "")
        mat = query(f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "MEINS" FROM "{S}"."PROC_MATERIALS"
            WHERE "MATNR" = '{mat_id}'""")
        if not mat or isinstance(mat, dict):
            # Try by description
            mat_words = [w for w in mat_id.upper().split() if len(w) > 1]
            if mat_words:
                wcond = " AND ".join([f"""UPPER("MAKTX") LIKE '%{w}%'""" for w in mat_words])
                mat = query(
                    f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "MEINS" FROM "{S}"."PROC_MATERIALS" WHERE {wcond} LIMIT 1""")
        if not mat or isinstance(mat, dict):
            ret.error(f"Item {item_no}: Material '{mat_id}' not found in procurement catalog", "ME", "011")
            continue
        mat = mat[0]

        # Quantity validation
        qty = item.get("QUANTITY", 0)
        if qty <= 0:
            ret.error(f"Item {item_no}: Quantity must be positive", "ME", "012")
            continue
        if qty > 10000:
            ret.warning(f"Item {item_no}: Large quantity ({qty}). Please verify.", "ME", "013")

        # Price determination
        net_price = item.get("NET_PRICE", round(float(mat["BASE_PRICE_USD"]) * fx, 2))
        item_value = round(net_price * qty, 2)
        total_value += item_value

        # Plant validation (item-level > header-level > auto)
        plant = item.get("PLANT", "") or POHEADER.get("PLANT", "")
        if not plant:
            plant = {"PW10": "US01", "PW20": "NL01", "PW30": "IN01"}[comp_code]
            ret.info(f"Item {item_no}: Plant auto-assigned: {plant}", "ME", "014")

        validated_items.append({
            "PO_ITEM": item_no, "MATNR": mat["MATNR"], "MAKTX": mat["MAKTX"],
            "QUANTITY": qty, "MEINS": mat["MEINS"], "NET_PRICE": net_price,
            "ITEM_VALUE": item_value, "PLANT": plant,
        })
        ret.info(f"Item {item_no}: {mat['MAKTX']} — {qty} x {currency} {net_price:,.2f} = {currency} {item_value:,.2f}",
                 "ME", "015")

    if not validated_items:
        ret.error("No valid line items after validation", "ME", "016")
        return {"EXPHEADER": {}, "RETURN": ret}

    # ── Approval check (like SAP release strategy) ──
    approval_limit = {"PW10": 100000, "PW20": 92000, "PW30": 8350000}  # ~$100K equivalent
    needs_approval = total_value > approval_limit.get(comp_code, 100000)
    if needs_approval:
        ret.warning(f"PO value {currency} {total_value:,.2f} exceeds approval threshold. Release required.", "ME",
                    "020")

    # ── Budget check simulation ──
    ret.info(f"Budget availability check passed for cost center {comp_code}-OPS", "ME", "021")

    # ── Generate PO number ──
    next_po = query(f"""SELECT MAX(CAST("EBELN" AS BIGINT)) + 1 AS next_id FROM "{S}"."PURCHASE_ORDERS" """)
    po_num = None
    if isinstance(next_po, list) and len(next_po) > 0:
        val = next_po[0].get("next_id") or next_po[0].get("NEXT_ID")
        if val:
            po_num = str(int(val))
    if not po_num:
        import random
        po_num = str(4500100000 + random.randint(1, 99999))

    delivery_date = POHEADER.get("DELIV_DATE") or (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    status = "OPEN" if needs_approval else "APPROVED"

    # ── Create PO Header ──
    result = execute(f"""INSERT INTO "{S}"."PURCHASE_ORDERS"
        ("EBELN","BSART","LIFNR","BUKRS","EKORG","ERDAT","NETWR","WAERK","EINDT","STATUS","WERKS")
        VALUES ('{po_num}', '{doc_type}', '{vendor["LIFNR"]}', '{comp_code}', '{ekorg}', 
                '{TODAY}', {total_value}, '{currency}', '{delivery_date}', '{status}', '{validated_items[0]["PLANT"]}')""")

    if not result.get("success"):
        ret.error(f"Database error: {result.get('error')}", "ME", "099")
        return {"EXPHEADER": {}, "RETURN": ret}

    # ── Create PO Items ──
    for vi in validated_items:
        execute(f"""INSERT INTO "{S}"."PO_ITEMS"
            ("EBELN","EBELP","MATNR","TXZ01","MENGE","MEINS","NETPR","NETWR","WAERK","WERKS","LGORT","RECEIVED_QTY")
            VALUES ('{po_num}', '{vi["PO_ITEM"]}', '{vi["MATNR"]}', '{vi["MAKTX"]}', 
                    {vi["QUANTITY"]}, '{vi["MEINS"]}', {vi["NET_PRICE"]}, {vi["ITEM_VALUE"]}, 
                    '{currency}', '{vi["PLANT"]}', '0001', 0)""")

    # ── Accounting entry simulation (FI posting) ──
    ret.info(f"FI Document: Debit GR/IR Clearing {currency} {total_value:,.2f}", "FI", "001")
    ret.info(f"FI Document: Credit Vendor Payable {currency} {total_value:,.2f}", "FI", "002")

    # ── Success ──
    ret.success(f"Purchase Order {po_num} created successfully", "ME", "100")
    if needs_approval:
        ret.warning(f"PO {po_num} requires release. Use: approve po {po_num}", "ME", "101")

    return {
        "EXPHEADER": {
            "PO_NUMBER": po_num,
            "VENDOR": vendor["LIFNR"],
            "VENDOR_NAME": vendor["NAME1"],
            "COMP_CODE": comp_code,
            "DOC_TYPE": doc_type,
            "CURRENCY": currency,
            "TOTAL_VALUE": total_value,
            "STATUS": status,
            "DELIVERY_DATE": delivery_date,
            "ITEMS": len(validated_items),
        },
        "RETURN": ret,
    }


# ============================================================
# BAPI_SALESORDER_CREATEFROMDAT2 — Create Sales Order
# ============================================================

def BAPI_SALESORDER_CREATEFROMDAT2(ORDER_HEADER_IN, ORDER_ITEMS_IN, ORDER_PARTNERS=None):
    """
    Create Sales Order

    Real SAP: conn.call('BAPI_SALESORDER_CREATEFROMDAT2', ORDER_HEADER_IN={...}, ...)

    Parameters:
        ORDER_HEADER_IN: dict with DOC_TYPE, SALES_ORG, DISTR_CHAN, DIVISION
        ORDER_ITEMS_IN: list with ITM_NUMBER, MATERIAL, TARGET_QTY
        ORDER_PARTNERS: list with PARTN_ROLE, PARTN_NUMB (sold-to, ship-to, bill-to)
    """
    ret = BAPIRET2()

    # ── Get customer from partners ──
    customer_id = ""
    if ORDER_PARTNERS:
        for p in ORDER_PARTNERS:
            if p.get("PARTN_ROLE") == "AG":  # Sold-to party
                customer_id = p.get("PARTN_NUMB", "")

    if not customer_id:
        customer_id = ORDER_HEADER_IN.get("CUSTOMER", "")

    # Validate customer
    cust = query(f"""SELECT "KUNNR", "NAME1", "LAND1", "BUKRS", "VKORG" FROM "{S}"."CUSTOMERS" 
        WHERE "KUNNR" = '{customer_id}'""")
    if not cust or isinstance(cust, dict):
        cust = query(f"""SELECT "KUNNR", "NAME1", "LAND1", "BUKRS", "VKORG" FROM "{S}"."CUSTOMERS" 
            WHERE UPPER("NAME1") LIKE '%{customer_id.upper()}%' LIMIT 1""")
    if not cust or isinstance(cust, dict):
        ret.error(f"Customer '{customer_id}' not found", "VA", "006")
        return {"SALESDOCUMENT": "", "RETURN": ret}
    cust = cust[0]
    ret.info(f"Customer {cust['KUNNR']} - {cust['NAME1']} validated", "VA", "001")

    # ── Determine org data ──
    bukrs = cust["BUKRS"]
    vkorg = cust["VKORG"]
    currency = {"PW10": "USD", "PW20": "EUR", "PW30": "INR"}[bukrs]
    fx = {"USD": 1.0, "EUR": 0.92, "INR": 83.5}[currency]

    # ── Document type ──
    doc_type = ORDER_HEADER_IN.get("DOC_TYPE", "ZCO")

    # ── Credit check simulation ──
    outstanding = query(f"""SELECT SUM("TOTAL") AS total FROM "{S}"."INVOICES" 
        WHERE "KUNNR" = '{cust["KUNNR"]}' AND "PAY_STATUS" IN ('OPEN', 'OVERDUE')""")
    if outstanding and isinstance(outstanding, list) and outstanding[0].get("total"):
        outs_val = float(outstanding[0]["total"])
        credit_limit = {"PW10": 5000000, "PW20": 4600000, "PW30": 415000000}  # ~$5M equivalent
        if outs_val > credit_limit.get(bukrs, 5000000):
            ret.warning(f"Customer has outstanding AR of {currency} {outs_val:,.2f}. Credit check warning.", "VA",
                        "050")
        else:
            ret.info(f"Credit check passed. Outstanding: {currency} {outs_val:,.2f}", "VA", "051")

    # ── Validate items ──
    validated_items = []
    total_value = 0

    for idx, item in enumerate(ORDER_ITEMS_IN):
        item_no = item.get("ITM_NUMBER", f"{(idx + 1) * 10:06d}")

        mat_id = item.get("MATERIAL", "")
        mat = query(f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "SPART" FROM "{S}"."MATERIALS"
            WHERE "MATNR" = '{mat_id}'""")
        if not mat or isinstance(mat, dict):
            mat_words = [w for w in mat_id.upper().split() if len(w) > 1]
            if mat_words:
                wcond = " AND ".join([f"""UPPER("MAKTX") LIKE '%{w}%'""" for w in mat_words])
                mat = query(
                    f"""SELECT "MATNR", "MAKTX", "BASE_PRICE_USD", "SPART" FROM "{S}"."MATERIALS" WHERE {wcond} LIMIT 1""")
        if not mat or isinstance(mat, dict):
            ret.error(f"Item {item_no}: Material '{mat_id}' not found", "VA", "011")
            continue
        mat = mat[0]

        qty = item.get("TARGET_QTY", 1)
        if qty <= 0:
            ret.error(f"Item {item_no}: Quantity must be positive", "VA", "012")
            continue

        # ── Pricing procedure simulation ──
        base_price = round(float(mat["BASE_PRICE_USD"]) * fx, 2)

        # PR00 - Base price
        ret.info(f"Item {item_no}: Condition PR00 (Base Price): {currency} {base_price:,.2f}", "VA", "030")

        # Discount simulation (volume based)
        discount_pct = 0
        if qty >= 100:
            discount_pct = 5
        elif qty >= 50:
            discount_pct = 3
        elif qty >= 20:
            discount_pct = 1

        if discount_pct > 0:
            ret.info(f"Item {item_no}: Condition K007 (Volume Discount): {discount_pct}%", "VA", "031")

        net_price = round(base_price * (1 - discount_pct / 100), 2)
        item_value = round(net_price * qty, 2)
        total_value += item_value

        # ── Availability check (ATP) simulation ──
        ret.info(
            f"Item {item_no}: ATP check — confirmed for {(datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')}",
            "VA", "040")

        plant = {"PW10": "US01", "PW20": "NL01", "PW30": "IN01"}[bukrs]
        spart = str(mat.get("SPART", "10"))

        validated_items.append({
            "ITM_NUMBER": item_no, "MATNR": mat["MATNR"], "MAKTX": mat["MAKTX"],
            "QUANTITY": qty, "NET_PRICE": net_price, "ITEM_VALUE": item_value,
            "PLANT": plant, "SPART": spart, "DISCOUNT_PCT": discount_pct,
        })

    if not validated_items:
        ret.error("No valid line items", "VA", "016")
        return {"SALESDOCUMENT": "", "RETURN": ret}

    # ── Tax calculation simulation ──
    tax_rate = {"PW10": 0.08, "PW20": 0.21, "PW30": 0.18}[bukrs]
    tax_amount = round(total_value * tax_rate, 2)
    ret.info(f"Tax calculation: {tax_rate * 100:.0f}% = {currency} {tax_amount:,.2f}", "VA", "060")

    # ── Determine order type ──
    spart = validated_items[0]["SPART"]
    auart = {"10": "ZOR", "20": "ZCO", "30": "ZCO", "40": "ZSO"}.get(spart, "ZCO")
    if doc_type != "ZCO":
        auart = doc_type

    # ── Generate SO number ──
    next_so = query(f"""SELECT MAX(CAST("VBELN" AS BIGINT)) + 1 AS next_id FROM "{S}"."SALES_ORDERS" """)
    so_num = None
    if isinstance(next_so, list) and len(next_so) > 0:
        val = next_so[0].get("next_id") or next_so[0].get("NEXT_ID")
        if val:
            so_num = str(int(val))
    if not so_num:
        import random as rnd
        so_num = str(4000100000 + rnd.randint(1, 99999))

    delivery_date = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    reps = ["Sarah Johnson", "Michael Chen", "Raj Patel", "Emma van der Berg", "Tom Williams"]
    rep = random.choice(reps)

    # ── Create SO Header ──
    result = execute(f"""INSERT INTO "{S}"."SALES_ORDERS"
        ("VBELN","AUART","VKORG","KUNNR","ERDAT","ERNAM","NETWR","WAERK","BSTNK","VDATU","STATUS","BUKRS")
        VALUES ('{so_num}', '{auart}', '{vkorg}', '{cust["KUNNR"]}', '{TODAY}', '{rep}', 
                {total_value}, '{currency}', 'PO-{so_num[-4:]}', '{delivery_date}', 'A', '{bukrs}')""")

    if not result.get("success"):
        ret.error(f"Database error: {result.get('error')}", "VA", "099")
        return {"SALESDOCUMENT": "", "RETURN": ret}

    # ── Create SO Items ──
    for vi in validated_items:
        execute(f"""INSERT INTO "{S}"."SALES_ORDER_ITEMS"
            ("VBELN","POSNR","MATNR","ARKTX","KWMENG","NETWR","NETPR","WAERK","WERKS","DISCOUNT_PCT")
            VALUES ('{so_num}', '{vi["ITM_NUMBER"]}', '{vi["MATNR"]}', '{vi["MAKTX"]}',
                    {vi["QUANTITY"]}, {vi["ITEM_VALUE"]}, {vi["NET_PRICE"]}, '{currency}', '{vi["PLANT"]}', {vi["DISCOUNT_PCT"]})""")

    # ── FI posting simulation ──
    ret.info(f"FI Document: Debit Customer Receivable {currency} {total_value + tax_amount:,.2f}", "FI", "001")
    ret.info(f"FI Document: Credit Revenue {currency} {total_value:,.2f}", "FI", "002")
    ret.info(f"FI Document: Credit Tax Payable {currency} {tax_amount:,.2f}", "FI", "003")

    ret.success(f"Sales Order {so_num} created successfully", "VA", "100")

    return {
        "SALESDOCUMENT": so_num,
        "RETURN": ret,
        "DETAILS": {
            "CUSTOMER": cust["NAME1"],
            "DOC_TYPE": auart,
            "CURRENCY": currency,
            "NET_VALUE": total_value,
            "TAX": tax_amount,
            "TOTAL": total_value + tax_amount,
            "SALES_REP": rep,
            "DELIVERY_DATE": delivery_date,
            "ITEMS": len(validated_items),
        }
    }


# ============================================================
# BAPI_PO_RELEASE — Approve / Release Purchase Order
# (Logic replicated from ChatV3 write_approve_po)
# ============================================================

def BAPI_PO_RELEASE(PO_NUMBER):
    """
    Approve / Release a Purchase Order

    Real SAP: conn.call('BAPI_PO_RELEASE1', PURCHASEORDER=...)

    Parameters:
        PO_NUMBER: str — PO number to approve

    Returns:
        dict with RETURN (BAPIRET2 messages)
    """
    ret = BAPIRET2()

    # ── Validation 1: PO exists ──
    current = query(f"""SELECT po."EBELN", po."STATUS", po."NETWR", po."WAERK", v."NAME1"
        FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{PO_NUMBER}'""")

    if not current or isinstance(current, dict):
        ret.error(f"PO {PO_NUMBER} not found", "ME", "200")
        return {"RETURN": ret}

    rec = current[0]
    ret.info(f"PO {PO_NUMBER} found — Vendor: {rec['NAME1']}, Value: {rec['WAERK']} {float(rec['NETWR']):,.2f}", "ME", "201")

    # ── Validation 2: Status must be OPEN ──
    if rec["STATUS"] != "OPEN":
        if rec["STATUS"] == "APPROVED":
            ret.warning(f"PO {PO_NUMBER} is already APPROVED", "ME", "202")
        else:
            ret.error(f"PO {PO_NUMBER} has status '{rec['STATUS']}' — cannot approve", "ME", "203")
        return {"PO_NUMBER": PO_NUMBER, "STATUS": rec["STATUS"], "RETURN": ret}

    # ── Approval threshold check ──
    value = float(rec["NETWR"])
    currency = rec["WAERK"]
    approval_limit = {"USD": 100000, "EUR": 92000, "INR": 8350000}.get(currency, 100000)
    if value > approval_limit:
        ret.info(f"PO value {currency} {value:,.2f} exceeds standard limit — escalated approval applied", "ME", "204")
    else:
        ret.info(f"PO value {currency} {value:,.2f} within approval limit", "ME", "205")

    # ── Execute approval (same as ChatV3 write_approve_po) ──
    result = execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'APPROVED' WHERE "EBELN" = '{PO_NUMBER}'""")

    if not result.get("success"):
        ret.error(f"Database error: {result.get('error')}", "ME", "299")
        return {"PO_NUMBER": PO_NUMBER, "RETURN": ret}

    ret.success(f"PO {PO_NUMBER} approved successfully — Status: OPEN → APPROVED", "ME", "210")
    return {"PO_NUMBER": PO_NUMBER, "STATUS": "APPROVED", "RETURN": ret}


# ============================================================
# BAPI_GOODSMVT_CREATE — Post Goods Movement (GR/GI)
# ============================================================

def BAPI_GOODSMVT_CREATE(GOODSMVT_HEADER, GOODSMVT_CODE, GOODSMVT_ITEM):
    """
    Post Goods Movement (Goods Receipt 101, Goods Issue 601)

    Parameters:
        GOODSMVT_HEADER: dict with PSTNG_DATE, DOC_DATE
        GOODSMVT_CODE: dict with GM_CODE ('01'=GR, '03'=GI)
        GOODSMVT_ITEM: list with MATERIAL, PLANT, MOVE_TYPE, ENTRY_QNT, PO_NUMBER
    """
    ret = BAPIRET2()
    gm_code = GOODSMVT_CODE.get("GM_CODE", "01")

    if gm_code == "01":
        # Goods Receipt against PO
        po_number = GOODSMVT_ITEM[0].get("PO_NUMBER", "") if GOODSMVT_ITEM else ""
        if not po_number:
            ret.error("PO number required for goods receipt", "MB", "001")
            return {"MATERIALDOCUMENT": "", "RETURN": ret}

        # Validate PO exists
        po = query(f"""SELECT po."EBELN", po."STATUS", po."NETWR", po."WAERK", v."NAME1" AS vendor, po."LIFNR", po."WERKS"
            FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
            WHERE po."EBELN" = '{po_number}'""")
        if not po or isinstance(po, dict):
            ret.error(f"PO {po_number} not found", "MB", "002")
            return {"MATERIALDOCUMENT": "", "RETURN": ret}
        po = po[0]

        if po["STATUS"] in ["RECEIVED", "CLOSED"]:
            ret.error(f"PO {po_number} already {po['STATUS']}. Cannot post GR.", "MB", "003")
            return {"MATERIALDOCUMENT": "", "RETURN": ret}

        if po["STATUS"] == "OPEN":
            ret.warning(f"PO {po_number} is still OPEN (not approved). Posting GR anyway.", "MB", "004")

        ret.info(f"PO {po_number} validated — Vendor: {po.get('VENDOR', po.get('vendor', po.get('NAME1', '')))}", "MB",
                 "010")

        # Validate items against PO items
        po_items = query(f"""SELECT "EBELP", "MATNR", "TXZ01", "MENGE", "RECEIVED_QTY"
            FROM "{S}"."PO_ITEMS" WHERE "EBELN" = '{po_number}'""")

        # Quantity tolerance check (SAP standard: ±10%)
        for pi in po_items:
            ordered = float(pi["MENGE"])
            already_received = float(pi.get("RECEIVED_QTY", 0))
            remaining = ordered - already_received
            if remaining <= 0:
                ret.warning(f"Item {pi['EBELP']}: Already fully received ({ordered} {pi['TXZ01'][:30]})", "MB", "020")
            else:
                ret.info(f"Item {pi['EBELP']}: Receiving {remaining} of {ordered} — {pi['TXZ01'][:40]}", "MB", "021")

        # Generate GR number
        next_gr = query(f"""SELECT MAX(CAST("MBLNR" AS BIGINT)) + 1 AS next_id FROM "{S}"."GOODS_RECEIPTS" """)
        gr_num = None
        if isinstance(next_gr, list) and len(next_gr) > 0:
            val = next_gr[0].get("next_id") or next_gr[0].get("NEXT_ID")
            if val:
                gr_num = str(int(val))
        if not gr_num:
            import random
            gr_num = str(5000100000 + random.randint(1, 99999))

        # Post GR
        execute(f"""INSERT INTO "{S}"."GOODS_RECEIPTS" ("MBLNR","BLDAT","BUDAT","LIFNR","EBELN","WERKS","STATUS")
            VALUES ('{gr_num}', '{TODAY}', '{TODAY}', '{po["LIFNR"]}', '{po_number}', '{po["WERKS"]}', 'POSTED')""")

        # Update PO items received qty
        execute(f"""UPDATE "{S}"."PO_ITEMS" SET "RECEIVED_QTY" = "MENGE" WHERE "EBELN" = '{po_number}'""")

        # Update PO status
        execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'RECEIVED' WHERE "EBELN" = '{po_number}'""")

        # Create GR items
        for pi in po_items:
            execute(f"""INSERT INTO "{S}"."GR_ITEMS" ("MBLNR","ZEILE","MATNR","MENGE","MEINS","EBELN","EBELP","WERKS","LGORT")
                VALUES ('{gr_num}', '{pi["EBELP"]}', '{pi["MATNR"]}', {pi["MENGE"]}, 'EA', '{po_number}', '{pi["EBELP"]}', '{po["WERKS"]}', '0001')""")

        # FI posting simulation
        ret.info(f"Movement Type 101: Goods Receipt for PO", "MB", "030")
        ret.info(f"FI: Debit Stock Account {po['WAERK']} {float(po['NETWR']):,.2f}", "FI", "001")
        ret.info(f"FI: Credit GR/IR Clearing {po['WAERK']} {float(po['NETWR']):,.2f}", "FI", "002")

        # Stock update simulation
        ret.info(f"Stock updated in plant {po['WERKS']}, storage location 0001", "MB", "040")

        ret.success(f"Material Document {gr_num} posted for PO {po_number}", "MB", "100")

        return {
            "MATERIALDOCUMENT": gr_num,
            "MATDOCUMENTYEAR": datetime.now().strftime("%Y"),
            "RETURN": ret,
        }

    else:
        ret.error(f"GM_CODE '{gm_code}' not implemented in demo", "MB", "099")
        return {"MATERIALDOCUMENT": "", "RETURN": ret}


# ============================================================
# BAPI_INCOMINGINVOICE_CREATE — Vendor Invoice Verification
# ============================================================

def BAPI_INCOMINGINVOICE_CREATE(HEADERDATA, ITEMDATA):
    """
    Create Vendor Invoice with 3-Way Match

    Parameters:
        HEADERDATA: dict with INVOICE_IND, DOC_TYPE, COMP_CODE, GROSS_AMOUNT, CURRENCY, PO_NUMBER
        ITEMDATA: list of items
    """
    ret = BAPIRET2()

    po_number = HEADERDATA.get("PO_NUMBER", "")

    # Validate PO
    po = query(f"""SELECT po."EBELN", po."STATUS", po."NETWR", po."WAERK", po."LIFNR", po."BUKRS", v."NAME1"
        FROM "{S}"."PURCHASE_ORDERS" po JOIN "{S}"."VENDORS" v ON po."LIFNR" = v."LIFNR"
        WHERE po."EBELN" = '{po_number}'""")
    if not po or isinstance(po, dict):
        ret.error(f"PO {po_number} not found", "MR", "001")
        return {"INVOICEDOCNUMBER": "", "RETURN": ret}
    po = po[0]

    ret.info(f"PO {po_number} found — Vendor: {po['NAME1']}", "MR", "010")

    # ── 3-Way Match ──
    ret.info("Performing 3-way match (PO ↔ GR ↔ Invoice)...", "MR", "020")

    # Check GR exists
    gr = query(
        f"""SELECT COUNT(*) AS cnt FROM "{S}"."GOODS_RECEIPTS" WHERE "EBELN" = '{po_number}' AND "STATUS" = 'POSTED'""")
    if not gr or isinstance(gr, dict) or (gr[0].get("cnt") or gr[0].get("CNT", 0)) == 0:
        ret.error(f"No Goods Receipt found for PO {po_number}. Cannot verify invoice.", "MR", "021")
        return {"INVOICEDOCNUMBER": "", "RETURN": ret}
    ret.info(f"✓ Goods Receipt found for PO {po_number}", "MR", "022")

    # Check PO quantity vs GR quantity
    po_items = query(
        f"""SELECT "EBELP", "MENGE", "RECEIVED_QTY", "NETWR" FROM "{S}"."PO_ITEMS" WHERE "EBELN" = '{po_number}'""")
    po_total = sum(float(p["NETWR"]) for p in po_items) if po_items else 0

    # Invoice amount tolerance check (±5%)
    invoice_amount = HEADERDATA.get("GROSS_AMOUNT", po_total)
    tolerance = po_total * 0.05
    if abs(invoice_amount - po_total) > tolerance:
        ret.error(
            f"Invoice amount {po['WAERK']} {invoice_amount:,.2f} deviates from PO {po['WAERK']} {po_total:,.2f} by more than 5%",
            "MR", "030")
        return {"INVOICEDOCNUMBER": "", "RETURN": ret}

    ret.info(f"✓ Amount match: Invoice {po['WAERK']} {invoice_amount:,.2f} ≈ PO {po['WAERK']} {po_total:,.2f}", "MR",
             "031")
    ret.info(f"✓ 3-way match successful", "MR", "032")

    # Tax calculation
    tax_rate = {"PW10": 0.08, "PW20": 0.21, "PW30": 0.18}.get(po["BUKRS"], 0.10)
    tax = round(invoice_amount * tax_rate, 2)
    total = round(invoice_amount + tax, 2)

    ret.info(f"Tax calculated: {tax_rate * 100:.0f}% = {po['WAERK']} {tax:,.2f}", "MR", "040")

    # Payment terms
    zterm = HEADERDATA.get("PMNTTRMS", "Z030")
    days_map = {"Z030": 30, "Z045": 45, "Z060": 60, "Z090": 90}
    due_date = (datetime.now() + timedelta(days=days_map.get(zterm, 30))).strftime("%Y-%m-%d")

    # Generate invoice number
    next_vi = query(f"""SELECT MAX(CAST("BELNR" AS BIGINT)) + 1 AS next_id FROM "{S}"."VENDOR_INVOICES" """)
    vi_num = None
    if isinstance(next_vi, list) and len(next_vi) > 0:
        val = next_vi[0].get("next_id") or next_vi[0].get("NEXT_ID")
        if val:
            vi_num = str(int(val))
    if not vi_num:
        import random
        vi_num = str(5100100000 + random.randint(1, 99999))

    # Create vendor invoice
    execute(f"""INSERT INTO "{S}"."VENDOR_INVOICES"
        ("BELNR","BLART","LIFNR","BUKRS","BLDAT","BUDAT","NETWR","MWSBK","TOTAL","WAERK","ZTERM","DUE_DATE","PAY_STATUS","EBELN")
        VALUES ('{vi_num}', 'RE', '{po["LIFNR"]}', '{po["BUKRS"]}', '{TODAY}', '{TODAY}', 
                {invoice_amount}, {tax}, {total}, '{po["WAERK"]}', '{zterm}', '{due_date}', 'OPEN', '{po_number}')""")

    # FI posting
    ret.info(f"FI: Debit GR/IR Clearing {po['WAERK']} {invoice_amount:,.2f}", "FI", "001")
    ret.info(f"FI: Debit Input Tax {po['WAERK']} {tax:,.2f}", "FI", "002")
    ret.info(f"FI: Credit Vendor Payable {po['WAERK']} {total:,.2f}", "FI", "003")

    ret.success(f"Vendor Invoice {vi_num} posted for PO {po_number}", "MR", "100")

    return {
        "INVOICEDOCNUMBER": vi_num,
        "RETURN": ret,
        "DETAILS": {
            "VENDOR": po["NAME1"],
            "NET_AMOUNT": invoice_amount,
            "TAX": tax,
            "TOTAL": total,
            "DUE_DATE": due_date,
            "PAYMENT_TERMS": zterm,
        }
    }


# ============================================================
# BAPI_ACC_DOCUMENT_POST — Post Payment
# ============================================================

def BAPI_ACC_DOCUMENT_POST(DOCUMENTHEADER, ACCOUNTPAYABLE):
    """
    Post Payment Document (Customer or Vendor)

    Parameters:
        DOCUMENTHEADER: dict with DOC_DATE, PSTNG_DATE, DOC_TYPE, COMP_CODE
        ACCOUNTPAYABLE: dict with VENDOR/CUSTOMER, AMOUNT, PAYMENT_REF
    """
    ret = BAPIRET2()

    doc_type = DOCUMENTHEADER.get("DOC_TYPE", "KZ")  # KZ=Vendor payment, DZ=Customer payment

    if doc_type == "KZ":
        # Vendor payment
        invoice_no = ACCOUNTPAYABLE.get("PAYMENT_REF", "")
        vi = query(f"""SELECT vi."BELNR", vi."TOTAL", vi."WAERK", vi."PAY_STATUS", vi."BUKRS", v."NAME1"
            FROM "{S}"."VENDOR_INVOICES" vi JOIN "{S}"."VENDORS" v ON vi."LIFNR" = v."LIFNR"
            WHERE vi."BELNR" = '{invoice_no}'""")
        if not vi or isinstance(vi, dict):
            ret.error(f"Vendor invoice {invoice_no} not found", "FB", "001")
            return {"RETURN": ret}
        vi = vi[0]

        if vi["PAY_STATUS"] == "PAID":
            ret.error(f"Invoice {invoice_no} already paid", "FB", "002")
            return {"RETURN": ret}

        # Bank determination simulation
        bank_map = {"PW10": "Chase Manhattan - USD Account", "PW20": "ING Bank - EUR Account",
                    "PW30": "HDFC Bank - INR Account"}
        bank = bank_map.get(vi["BUKRS"], "Unknown")
        ret.info(f"House bank determined: {bank}", "FB", "010")

        # Payment amount
        amount = ACCOUNTPAYABLE.get("AMOUNT", float(vi["TOTAL"]))
        partial = amount < float(vi["TOTAL"])
        new_status = "PARTIAL" if partial else "PAID"

        # Execute payment
        execute(f"""UPDATE "{S}"."VENDOR_INVOICES" SET "PAY_STATUS" = '{new_status}' WHERE "BELNR" = '{invoice_no}'""")

        ret.info(f"FI: Debit Vendor Payable {vi['WAERK']} {amount:,.2f}", "FI", "001")
        ret.info(f"FI: Credit Bank Account {vi['WAERK']} {amount:,.2f}", "FI", "002")

        if partial:
            ret.warning(f"Partial payment: {vi['WAERK']} {amount:,.2f} of {vi['WAERK']} {float(vi['TOTAL']):,.2f}",
                        "FB", "020")

        ret.success(f"Payment posted for vendor invoice {invoice_no} — {vi['NAME1']}", "FB", "100")
        return {"RETURN": ret}

    elif doc_type == "DZ":
        # Customer payment
        invoice_no = ACCOUNTPAYABLE.get("PAYMENT_REF", "")
        inv = query(f"""SELECT i."VBELN", i."TOTAL", i."WAERK", i."PAY_STATUS", i."BUKRS", c."NAME1"
            FROM "{S}"."INVOICES" i JOIN "{S}"."CUSTOMERS" c ON i."KUNNR" = c."KUNNR"
            WHERE i."VBELN" = '{invoice_no}'""")
        if not inv or isinstance(inv, dict):
            ret.error(f"Customer invoice {invoice_no} not found", "FB", "001")
            return {"RETURN": ret}
        inv = inv[0]

        if inv["PAY_STATUS"] == "PAID":
            ret.error(f"Invoice {invoice_no} already paid", "FB", "002")
            return {"RETURN": ret}

        amount = ACCOUNTPAYABLE.get("AMOUNT", float(inv["TOTAL"]))
        partial = amount < float(inv["TOTAL"])
        new_status = "PARTIAL" if partial else "PAID"

        execute(f"""UPDATE "{S}"."INVOICES" SET "PAY_STATUS" = '{new_status}' WHERE "VBELN" = '{invoice_no}'""")

        bank_map = {"PW10": "Chase Manhattan - USD Account", "PW20": "ING Bank - EUR Account",
                    "PW30": "HDFC Bank - INR Account"}
        bank = bank_map.get(inv["BUKRS"], "Unknown")
        ret.info(f"House bank: {bank}", "FB", "010")
        ret.info(f"FI: Debit Bank Account {inv['WAERK']} {amount:,.2f}", "FI", "001")
        ret.info(f"FI: Credit Customer Receivable {inv['WAERK']} {amount:,.2f}", "FI", "002")

        ret.success(f"Payment received for invoice {invoice_no} — {inv['NAME1']}", "FB", "100")
        return {"RETURN": ret}


# ============================================================
# DEMO: Run full P2P cycle
# ============================================================

def demo_full_p2p_cycle():
    """Demonstrate a complete Procure-to-Pay cycle using BAPIs"""

    print("\n" + "═" * 70)
    print("  BAPI DEMO — Full Procure-to-Pay Cycle")
    print("═" * 70)

    # Step 1: Create PO
    print("\n📋 STEP 1: BAPI_PO_CREATE1 — Create Purchase Order")
    print("-" * 50)
    result = BAPI_PO_CREATE1(
        POHEADER={
            "DOC_TYPE": "NB",
            "VENDOR": "Sun Chemical",
            "COMP_CODE": "PW10",
            "PURCH_ORG": "1000",
        },
        POITEM=[
            {"PO_ITEM": "00010", "MATERIAL": "UV Ink Black", "QUANTITY": 50},
            {"PO_ITEM": "00020", "MATERIAL": "Pigment Cyan", "QUANTITY": 25},
        ]
    )
    print(result["RETURN"].format())
    po_num = result["EXPHEADER"].get("PO_NUMBER", "")
    if not po_num:
        print("❌ PO creation failed. Stopping demo.")
        return
    print(f"\n   📄 PO Number: {po_num}")

    # Step 2: Goods Receipt
    print(f"\n📦 STEP 2: BAPI_GOODSMVT_CREATE — Receive Goods for PO {po_num}")
    print("-" * 50)
    gr_result = BAPI_GOODSMVT_CREATE(
        GOODSMVT_HEADER={"PSTNG_DATE": TODAY, "DOC_DATE": TODAY},
        GOODSMVT_CODE={"GM_CODE": "01"},
        GOODSMVT_ITEM=[{"PO_NUMBER": po_num, "MOVE_TYPE": "101"}]
    )
    print(gr_result["RETURN"].format())
    gr_num = gr_result.get("MATERIALDOCUMENT", "")
    print(f"\n   📄 GR Number: {gr_num}")

    # Step 3: Invoice Verification
    print(f"\n🧾 STEP 3: BAPI_INCOMINGINVOICE_CREATE — 3-Way Match & Invoice")
    print("-" * 50)
    iv_result = BAPI_INCOMINGINVOICE_CREATE(
        HEADERDATA={"PO_NUMBER": po_num, "PMNTTRMS": "Z030"},
        ITEMDATA=[]
    )
    print(iv_result["RETURN"].format())
    vi_num = iv_result.get("INVOICEDOCNUMBER", "")
    print(f"\n   📄 Vendor Invoice: {vi_num}")

    # Step 4: Payment
    if vi_num:
        print(f"\n💰 STEP 4: BAPI_ACC_DOCUMENT_POST — Pay Vendor Invoice {vi_num}")
        print("-" * 50)
        pay_result = BAPI_ACC_DOCUMENT_POST(
            DOCUMENTHEADER={"DOC_TYPE": "KZ", "PSTNG_DATE": TODAY},
            ACCOUNTPAYABLE={"PAYMENT_REF": vi_num}
        )
        print(pay_result["RETURN"].format())

    # Close PO
    execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'CLOSED' WHERE "EBELN" = '{po_num}'""")

    print("\n" + "═" * 70)
    print(f"  ✅ COMPLETE P2P CYCLE:")
    print(f"     PO {po_num} → GR {gr_num} → Invoice {vi_num} → Payment → Closed")
    print("═" * 70)


# ============================================================
# P2P Step Functions — each step is independent and returns
# (success: bool, output: str, context_updates: dict)
# ============================================================

P2P_STEPS = [
    {"id": "create_po",  "name": "Create PO",          "icon": "📋", "bapi": "BAPI_PO_CREATE1"},
    {"id": "approve_po", "name": "Approve PO",          "icon": "✅", "bapi": "BAPI_PO_RELEASE"},
    {"id": "goods_receipt", "name": "Receive Goods",     "icon": "📦", "bapi": "BAPI_GOODSMVT_CREATE"},
    {"id": "invoice",    "name": "Supplier Invoice",     "icon": "🧾", "bapi": "BAPI_INCOMINGINVOICE_CREATE"},
    {"id": "payment",    "name": "Payment",              "icon": "💰", "bapi": "BAPI_ACC_DOCUMENT_POST"},
    {"id": "close_po",   "name": "Close PO",             "icon": "🔒", "bapi": "—"},
]


def p2p_step_create_po(ctx):
    """Step 1: Create PO with custom values."""
    poheader = {"DOC_TYPE": "NB", "VENDOR": ctx["vendor_name"], "COMP_CODE": ""}
    if ctx.get("plant"):
        poheader["PLANT"] = ctx["plant"]
    if ctx.get("date"):
        poheader["DELIV_DATE"] = ctx["date"]

    poitem = {"PO_ITEM": "00010", "MATERIAL": ctx["material_keyword"], "QUANTITY": ctx["qty"]}
    if ctx.get("price") is not None:
        poitem["NET_PRICE"] = ctx["price"]

    result = BAPI_PO_CREATE1(POHEADER=poheader, POITEM=[poitem])
    output = result["RETURN"].format()

    po_num = result["EXPHEADER"].get("PO_NUMBER", "")
    if not po_num:
        return False, output + "\n❌ PO creation failed.", {}

    output += f"\n\n   📄 PO Number: {po_num}"
    return True, output, {"po_num": po_num}


def p2p_step_approve_po(ctx):
    """Step 2: Approve PO. Skips automatically if already approved (low-value auto-approval)."""
    po_num = ctx["po_num"]

    # Check if PO was already auto-approved at creation (below threshold)
    po_check = query(f"""SELECT "STATUS" FROM "{S}"."PURCHASE_ORDERS" WHERE "EBELN" = '{po_num}'""")
    if po_check and po_check[0].get("STATUS") == "APPROVED":
        return True, f"   ⏭️ PO {po_num} was auto-approved at creation (value below approval threshold) — skipping.", {}

    result = BAPI_PO_RELEASE(PO_NUMBER=po_num)
    output = result["RETURN"].format()

    has_error = any(m["TYPE"] == "E" for m in result["RETURN"].messages)
    if has_error:
        return False, output, {}

    return True, output, {}


def p2p_step_goods_receipt(ctx):
    """Step 3: Goods Receipt."""
    po_num = ctx["po_num"]
    gr_result = BAPI_GOODSMVT_CREATE(
        GOODSMVT_HEADER={"PSTNG_DATE": TODAY, "DOC_DATE": TODAY},
        GOODSMVT_CODE={"GM_CODE": "01"},
        GOODSMVT_ITEM=[{"PO_NUMBER": po_num, "MOVE_TYPE": "101"}]
    )
    output = gr_result["RETURN"].format()
    gr_num = gr_result.get("MATERIALDOCUMENT", "")

    has_error = any(m["TYPE"] == "E" for m in gr_result["RETURN"].messages)
    if has_error:
        return False, output, {}

    output += f"\n\n   📄 GR Number: {gr_num}"
    return True, output, {"gr_num": gr_num}


def p2p_step_invoice(ctx):
    """Step 4: Supplier Invoice."""
    po_num = ctx["po_num"]
    iv_result = BAPI_INCOMINGINVOICE_CREATE(
        HEADERDATA={"PO_NUMBER": po_num, "PMNTTRMS": "Z030"},
        ITEMDATA=[]
    )
    output = iv_result["RETURN"].format()
    vi_num = iv_result.get("INVOICEDOCNUMBER", "")

    has_error = any(m["TYPE"] == "E" for m in iv_result["RETURN"].messages)
    if has_error:
        return False, output, {}

    output += f"\n\n   📄 Vendor Invoice: {vi_num}"
    return True, output, {"vi_num": vi_num}


def p2p_step_payment(ctx):
    """Step 5: Payment."""
    vi_num = ctx.get("vi_num", "")
    if not vi_num:
        return False, "❌ No vendor invoice to pay.", {}

    pay_result = BAPI_ACC_DOCUMENT_POST(
        DOCUMENTHEADER={"DOC_TYPE": "KZ", "PSTNG_DATE": TODAY},
        ACCOUNTPAYABLE={"PAYMENT_REF": vi_num}
    )
    output = pay_result["RETURN"].format()

    has_error = any(m["TYPE"] == "E" for m in pay_result["RETURN"].messages)
    if has_error:
        return False, output, {}

    return True, output, {}


def p2p_step_close_po(ctx):
    """Step 6: Close PO."""
    po_num = ctx["po_num"]
    result = execute(f"""UPDATE "{S}"."PURCHASE_ORDERS" SET "STATUS" = 'CLOSED' WHERE "EBELN" = '{po_num}'""")

    if not result.get("success"):
        return False, f"❌ Failed to close PO: {result.get('error')}", {}

    gr_num = ctx.get("gr_num", "—")
    vi_num = ctx.get("vi_num", "—")
    output = f"""PO {po_num} closed successfully.

═══════════════════════════════════════════════════════
  ✅ COMPLETE P2P CYCLE:
     PO {po_num} → Approved → GR {gr_num} → Invoice {vi_num} → Payment → Closed
═══════════════════════════════════════════════════════"""
    return True, output, {}


# Map step IDs to their functions
P2P_STEP_FUNCTIONS = {
    "create_po": p2p_step_create_po,
    "approve_po": p2p_step_approve_po,
    "goods_receipt": p2p_step_goods_receipt,
    "invoice": p2p_step_invoice,
    "payment": p2p_step_payment,
    "close_po": p2p_step_close_po,
}


def run_p2p_step(step_index, ctx):
    """
    Run a single P2P step by index.
    Returns: (success, step_info, output, updated_ctx)
    """
    if step_index < 0 or step_index >= len(P2P_STEPS):
        return False, None, "Invalid step index.", ctx

    step = P2P_STEPS[step_index]
    func = P2P_STEP_FUNCTIONS[step["id"]]

    header = f"\n{step['icon']} STEP {step_index + 1}/{len(P2P_STEPS)}: {step['bapi']} — {step['name']}\n" + "-" * 55
    success, output, updates = func(ctx)

    ctx.update(updates)
    full_output = header + "\n" + output

    return success, step, full_output, ctx


def run_p2p_remaining_steps(step_index, ctx):
    """
    Run all steps from step_index onwards (auto-mode).
    Returns: (success, output, updated_ctx)
    """
    outputs = []
    for i in range(step_index, len(P2P_STEPS)):
        success, step, output, ctx = run_p2p_step(i, ctx)
        outputs.append(output)
        if not success:
            outputs.append(f"\n❌ P2P flow stopped at Step {i + 1} due to error.")
            return False, "\n".join(outputs), ctx

    return True, "\n".join(outputs), ctx


def demo_full_o2c_cycle():
    """Demonstrate a complete Order-to-Cash cycle using BAPIs"""

    print("\n" + "═" * 70)
    print("  BAPI DEMO — Full Order-to-Cash Cycle")
    print("═" * 70)

    # Step 1: Create Sales Order
    print("\n📋 STEP 1: BAPI_SALESORDER_CREATEFROMDAT2 — Create Sales Order")
    print("-" * 50)
    result = BAPI_SALESORDER_CREATEFROMDAT2(
        ORDER_HEADER_IN={"DOC_TYPE": "ZOR"},
        ORDER_ITEMS_IN=[
            {"ITM_NUMBER": "000010", "MATERIAL": "ProJet X7 Digital Press", "TARGET_QTY": 2},
        ],
        ORDER_PARTNERS=[
            {"PARTN_ROLE": "AG", "PARTN_NUMB": "3M Company"},
        ]
    )
    print(result["RETURN"].format())
    so_num = result.get("SALESDOCUMENT", "")
    if not so_num:
        print("❌ SO creation failed.")
        return
    details = result.get("DETAILS", {})
    print(f"\n   📄 Sales Order: {so_num}")
    print(f"   💰 Net: {details.get('CURRENCY', '')} {details.get('NET_VALUE', 0):,.2f}")
    print(f"   💰 Total (incl tax): {details.get('CURRENCY', '')} {details.get('TOTAL', 0):,.2f}")

    print("\n" + "═" * 70)
    print(f"  ✅ Sales Order {so_num} created for {details.get('CUSTOMER', '')}")
    print(f"     Next: process order {so_num} → ship → invoice → collect payment")
    print("═" * 70)


# ============================================================
# INTERACTIVE MODE
# ============================================================

if __name__ == "__main__":
    print("═" * 70)
    print("🖨️  PrintWorks — SAP BAPI Simulation Layer")
    print("═" * 70)

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{S}"."PURCHASE_ORDERS"')
        cnt = cur.fetchone()[0]
        cur.close()
        conn.close()
        print(f"\n   ✅ HANA Cloud connected — {cnt:,} purchase orders")
    except Exception as e:
        print(f"\n   ❌ HANA connection failed: {e}")
        exit(1)

    print(f"\n   Choose a demo:")
    print(f"   1. Full P2P Cycle (Create PO → Receive → Invoice → Pay)")
    print(f"   2. Full O2C Cycle (Create Sales Order with pricing & credit check)")
    print(f"   3. Both")

    # Accept choice from command line arg or interactive input
    import sys as _sys
    if len(_sys.argv) > 1:
        choice = _sys.argv[1].strip()
    else:
        choice = input("\n   Enter 1, 2, or 3: ").strip()

    if choice in ["1", "3"]:
        demo_full_p2p_cycle()
    if choice in ["2", "3"]:
        demo_full_o2c_cycle()

    print("\n\n💡 These BAPI simulations demonstrate the validation, document flow,")
    print("   accounting entries, and business logic that SAP BAPIs provide.")
    print("   In production, replace with PyRFC calls to real SAP system.")