# SAP HANA — PrintWorks Global Solutions
# Complete Business Flows Reference (BAPI-Enhanced)

## Database Overview

| Table | Rows | Description |
|---|---|---|
| CUSTOMERS | 90 | Customer master data |
| VENDORS | 33 | Vendor/supplier master data |
| MATERIALS | 71 | Products you sell |
| PROC_MATERIALS | 32 | Raw materials you buy |
| PLANTS | 7 | Factory/warehouse locations |
| SALES_ORGS | 3 | Sales regions (US, EMEA, India) |
| SALES_ORDERS | 4,855 | Customer sales orders |
| SALES_ORDER_ITEMS | 17,087 | Line items for sales orders |
| DELIVERIES | 3,868 | Shipments to customers |
| DELIVERY_ITEMS | 13,799 | Line items for deliveries |
| INVOICES | 3,158 | Customer invoices (AR) |
| INVOICE_ITEMS | 11,374 | Line items for customer invoices |
| PURCHASE_ORDERS | 2,012 | Purchase orders to vendors |
| PO_ITEMS | 7,148 | Line items for POs |
| GOODS_RECEIPTS | 1,782 | Goods received against POs |
| GR_ITEMS | 6,357 | Line items for goods receipts |
| VENDOR_INVOICES | 1,680 | Vendor/supplier invoices (AP) |
| VI_ITEMS | 0 | Line items for vendor invoices |

---

## BAPI Return Messages (BAPIRET2)

All write operations return structured SAP-style messages:

| Type | Symbol | Meaning |
|---|---|---|
| S | ✅ | Success |
| E | ❌ | Error |
| W | ⚠️ | Warning |
| I | ℹ️ | Information |

Message format: `[MSG_CLASS-MSG_NUMBER] Message text`

Message classes used:
| Class | Area |
|---|---|
| ME | Purchasing / Materials Management |
| VA | Sales & Distribution |
| FB | Finance / Accounting |
| FI | FI Posting Simulation |
| MB | Goods Movement / Inventory |
| MR | Invoice Verification |
| VL | Shipping / Delivery |

---

## Flow 1: P2P (Procure-to-Pay) — Buying Side

### Process

```
You need raw materials (ink, paper, printheads)
    ↓
1. Create Purchase Order    → PURCHASE_ORDERS + PO_ITEMS         (BAPI_PO_CREATE1)
    ↓
2. Approve PO               → PURCHASE_ORDERS (status update)    (Threshold + Budget check)
    ↓
3. Receive Goods            → GOODS_RECEIPTS + GR_ITEMS          (BAPI_GOODSMVT_CREATE — Mvt 101)
    ↓
4. Create Vendor Invoice    → VENDOR_INVOICES                    (BAPI_INCOMINGINVOICE_CREATE — 3-way match)
    ↓
5. Pay Vendor               → VENDOR_INVOICES (status update)    (BAPI_ACC_DOCUMENT_POST — KZ)
    ↓
6. Close PO                 → PURCHASE_ORDERS (status update)    (Completion validation)
```

### Step-by-Step: What happens and where data goes

#### Step 1 — Create PO (`BAPI_PO_CREATE1`)
- **Command** (3 ways to create):
  1. `create po` → guided conversational flow asks for fields one by one
  2. `create po for Sun Chemical, UV Ink Black, 50, US01, 120.00` → direct with prefix
  3. `Sun Chemical, UV Ink Black, 50, US01, 120.00` → raw comma input (auto-detects vendor → creates PO)
- **Validations**:
  - Vendor lookup (fuzzy match by name in VENDORS table)
  - Material lookup (fuzzy match by name in PROC_MATERIALS table)
  - Quantity > 0 (warns if > 10,000)
  - Plant must exist in BUKRS_MAP (US01, US02, NL01, NL02, IN01, IN02, IN03)
  - Unit price must be positive
- **Org determination**: Plant → Company Code → Currency → Purchasing Org
  - `US01/US02 → PW10 → USD → 1000`
  - `NL01/NL02 → PW20 → EUR → 2000`
  - `IN01/IN02/IN03 → PW30 → INR → 3000`
- **Approval threshold check**:
  - PW10: $100,000 | PW20: €92,000 | PW30: ₹8,350,000 (~$100K equivalent)
  - Under threshold → STATUS = **APPROVED** (auto-approved)
  - Over threshold → STATUS = **OPEN** (needs manual approval)
- **Budget check**: Simulated pass for company-code-OPS cost center
- **Inserts into**:
  - `PURCHASE_ORDERS` — 1 row (EBELN, BSART=NB, LIFNR, BUKRS, EKORG, NETWR, WAERK, STATUS)
  - `PO_ITEMS` — 1 row (EBELN, EBELP=00010, MATNR, TXZ01, MENGE, NETPR, NETWR, RECEIVED_QTY=0)
- **FI Posting**: Debit GR/IR Clearing, Credit Vendor Payable
- **Output**: PO number (e.g., 4500000123)
- **Next step**: `approve po` (if over threshold) or `receive goods` (if auto-approved)

#### Step 2 — Approve PO (Threshold + Budget Check)
- **Command**: `approve po 4500000123`
- **Validations**:
  - PO must be OPEN
  - Approval threshold check (warns if PO value exceeds company code threshold)
  - Budget availability check
- **Updates**: `PURCHASE_ORDERS` → STATUS: OPEN → APPROVED
- **Next step**: `receive goods 4500000123`

#### Step 3 — Receive Goods (`BAPI_GOODSMVT_CREATE` — Movement Type 101)
- **Command**: `receive goods 4500000123`
- **Validations**:
  - PO must exist (warns if still OPEN / not yet approved, but proceeds)
  - Item-by-item quantity tolerance check (ordered vs already received)
  - Warns if items already fully received
- **Updates**:
  - `PURCHASE_ORDERS` → STATUS → RECEIVED
  - `PO_ITEMS` → RECEIVED_QTY = MENGE (ordered quantity)
- **Inserts into**:
  - `GOODS_RECEIPTS` — 1 row (MBLNR, BLDAT, BUDAT, LIFNR, EBELN, WERKS, STATUS=POSTED)
  - `GR_ITEMS` — 1 row per PO item (MBLNR, ZEILE, MATNR, MENGE, MEINS, EBELN, EBELP, WERKS, LGORT=0001)
- **FI Posting**: Debit Stock Account, Credit GR/IR Clearing
- **Stock update**: Plant + Storage Location 0001
- **Output**: Material Document number (e.g., 5999999XXX)
- **Next step**: `create invoice 4500000123`

#### Step 4 — Create Vendor Invoice (`BAPI_INCOMINGINVOICE_CREATE` — 3-Way Match)
- **Command**: `create invoice 4500000123`
- **3-Way Match** (PO ↔ GR ↔ Invoice):
  1. Validates PO exists and gets vendor + company code
  2. Checks Goods Receipt exists and is POSTED (blocks if no GR)
  3. Amount tolerance check (±5% between PO value and invoice amount)
  4. Checks no duplicate invoice already exists for this PO
- **Tax auto-calculation** (from company code, no manual entry needed):
  - PW10 (USD): 10%
  - PW20 (EUR): 21%
  - PW30 (INR): 18% GST
- **Payment terms**: Default Net 30 days (configurable: 30, 45, 60, 90)
- **Inserts into**:
  - `VENDOR_INVOICES` — 1 row (BELNR, BLART=RE, LIFNR, BUKRS, NETWR, MWSBK, TOTAL, WAERK, ZTERM, DUE_DATE, PAY_STATUS=OPEN, EBELN)
- **FI Posting**: Debit GR/IR Clearing, Debit Input Tax, Credit Vendor Payable
- **Output**: Invoice number (e.g., 5100000XXX)
- **Next step**: `pay vendor 5100000XXX`

#### Step 5 — Pay Vendor (`BAPI_ACC_DOCUMENT_POST` — KZ)
- **Command**: `pay vendor 5100000XXX`
- **Validations**:
  - Vendor invoice must exist and not be already PAID
  - Supports partial payments (pay specific amount < total)
- **House bank determination**:
  - PW10 → Chase Manhattan — USD Account
  - PW20 → ING Bank — EUR Account
  - PW30 → HDFC Bank — INR Account
- **Updates**: `VENDOR_INVOICES` → PAY_STATUS: OPEN → PAID (or PARTIAL)
- **FI Posting**: Debit Vendor Payable, Credit Bank Account
- **Next step**: `close po 4500000123`

#### Step 6 — Close PO (Completion Validation)
- **Command**: `close po 4500000123`
- **Validations**:
  - PO must not already be CLOSED
  - Checks all PO items are fully received (RECEIVED_QTY >= MENGE) — warns if not
  - Checks vendor invoice exists and is PAID — warns if not
- **Updates**: `PURCHASE_ORDERS` → STATUS → CLOSED
- **Output**: "P2P flow complete for this PO."

### P2P Status Flow

```
PURCHASE_ORDERS.STATUS:        OPEN → APPROVED → RECEIVED → CLOSED
                               (auto-approve if under threshold)
GOODS_RECEIPTS.STATUS:         POSTED
VENDOR_INVOICES.PAY_STATUS:    OPEN → PAID (or PARTIAL)
```

---

## Flow 2: O2C (Order-to-Cash) — Selling Side

### Process

```
Customer wants to buy printers/ink/services
    ↓
1. Create Sales Order       → SALES_ORDERS + SALES_ORDER_ITEMS   (BAPI_SALESORDER_CREATEFROMDAT2)
    ↓
2. Process Order            → SALES_ORDERS (status: A → B)       (Status advance)
    ↓
3. Complete Order           → SALES_ORDERS (status: B → C)       (Same command, next step)
    ↓
4. Ship Delivery            → DELIVERIES (Goods Issue — Mvt 601)
    ↓
5. Confirm Delivery         → DELIVERIES (Proof of Delivery)
    ↓
6. Record Payment           → INVOICES (BAPI_ACC_DOCUMENT_POST — DZ)
```

### Step-by-Step: What happens and where data goes

#### Step 1 — Create Sales Order (`BAPI_SALESORDER_CREATEFROMDAT2`)
- **Command** (3 ways to create):
  1. `create order` → guided conversational flow asks for fields one by one
  2. `create order for ITC, OffsetMaster 6080, 10, IN01, 25000.00` → direct with prefix
  3. `ITC, OffsetMaster 6080, 10, IN01, 25000.00` → raw comma input (auto-detects customer → creates SO)
- **Validations**:
  - Customer lookup (fuzzy match by name in CUSTOMERS table)
  - Material lookup (fuzzy match by name in MATERIALS table)
  - Plant must exist in BUKRS_MAP
  - Unit price and quantity must be positive
- **Org determination**: Plant → Company Code → Currency; Customer → Sales Org
- **Pricing procedure**:
  - PR00 (Base Price): unit price as provided
  - K007 (Volume Discount):
    - qty >= 100 → 5% discount
    - qty >= 50 → 3% discount
    - qty >= 20 → 1% discount
  - Net price = unit price × (1 - discount%)
- **Credit check**:
  - Sums outstanding AR (OPEN + OVERDUE invoices) for the customer
  - Compares against credit limit: PW10 $5M | PW20 €4.6M | PW30 ₹415M
  - Warns if outstanding exceeds limit (does not block)
- **ATP check** (Available-to-Promise): Simulated — confirms delivery date (today + 14 days)
- **Tax calculation**: Auto from company code (10% / 21% / 18%)
- **Order type determination**: Based on material division (SPART)
  - 10 → ZOR (Equipment) | 20/30 → ZCO (Consumables) | 40 → ZSO (Service/AMC)
- **Inserts into**:
  - `SALES_ORDERS` — 1 row (VBELN, AUART, VKORG, KUNNR, NETWR, WAERK, STATUS=A, BUKRS)
  - `SALES_ORDER_ITEMS` — 1 row (VBELN, POSNR=000010, MATNR, KWMENG, NETWR, NETPR, DISCOUNT_PCT)
- **FI Posting**: Debit Customer Receivable (net + tax), Credit Revenue (net), Credit Tax Payable (tax)
- **Output**: SO number (e.g., 4000000XXX)
- **Next step**: `process order 4000000XXX`

#### Step 2 — Process Order
- **Command**: `process order 4000000XXX`
- **Validations**: Order must exist and have STATUS = A (Open)
- **Updates**: `SALES_ORDERS` → STATUS: A (Open) → B (In Process)
- **Shows**: Order value and customer name
- **Next step**: `process order 4000000XXX` (to complete)

#### Step 3 — Complete Order
- **Command**: `process order 4000000XXX` (same command, auto-advances)
- **Validations**: Order must have STATUS = B (In Process)
- **Updates**: `SALES_ORDERS` → STATUS: B (In Process) → C (Completed)
- **Next step**: `ship delivery 8000000XXX` (looks up linked delivery)

#### Step 4 — Ship Delivery (Goods Issue — Movement Type 601)
- **Command**: `ship delivery 8000000XXX`
- **Validations**: Delivery must exist
- **Movement Type 601**: Goods Issue for Delivery — stock deducted from shipping plant
- **Updates**: `DELIVERIES` → GI_STATUS: A (Open) → B (In Transit)
- **Next step**: `confirm delivery 8000000XXX`

#### Step 5 — Confirm Delivery (Proof of Delivery)
- **Command**: `confirm delivery 8000000XXX`
- **Validations**: Delivery must exist
- **Proof of Delivery**: Confirmed with today's date
- **Updates**: `DELIVERIES` → GI_STATUS: B → C (Delivered), GI_DATE = today
- **Next step**: `record payment 9000000XXX` (looks up linked open invoice)

#### Step 6 — Record Payment (`BAPI_ACC_DOCUMENT_POST` — DZ)
- **Command**: `record payment 9000000XXX`
- **Validations**:
  - Invoice must exist and not be already PAID
  - Supports partial payments (pay specific amount < total)
- **House bank determination**:
  - PW10 → Chase Manhattan — USD Account
  - PW20 → ING Bank — EUR Account
  - PW30 → HDFC Bank — INR Account
- **Updates**: `INVOICES` → PAY_STATUS: OPEN → PAID (or PARTIAL)
- **FI Posting**: Debit Bank Account, Credit Customer Receivable
- **Output**: "O2C flow complete for this invoice." (if fully paid)

### O2C Status Flow

```
SALES_ORDERS.STATUS:      A (Open) → B (In Process) → C (Completed)
DELIVERIES.GI_STATUS:     A (Open) → B (In Transit) → C (Delivered)
INVOICES.PAY_STATUS:      OPEN → PAID (or PARTIAL / OVERDUE)
```

---

## Dependencies — What needs to exist before what

### P2P Dependencies

```
VENDORS (master)          ← must exist first
PROC_MATERIALS (master)   ← must exist first
PLANTS (master)           ← must exist first
    │
    ▼
PURCHASE_ORDERS ──────────── depends on: VENDORS, PLANTS
PO_ITEMS ─────────────────── depends on: PURCHASE_ORDERS, PROC_MATERIALS
    │
    ▼  (PO must be APPROVED — or auto-approved if under threshold)
GOODS_RECEIPTS ───────────── depends on: PURCHASE_ORDERS, VENDORS
GR_ITEMS ─────────────────── depends on: GOODS_RECEIPTS, PO_ITEMS
    │
    ▼  (GR must exist — enforced by 3-way match)
VENDOR_INVOICES ──────────── depends on: PURCHASE_ORDERS, GOODS_RECEIPTS, VENDORS
    │
    ▼  (Invoice must be OPEN)
Payment (status update) ──── depends on: VENDOR_INVOICES
    │
    ▼  (All items received + invoice PAID — validated at close)
Close PO (status update) ─── depends on: GOODS_RECEIPTS, VENDOR_INVOICES
```

### O2C Dependencies

```
CUSTOMERS (master)        ← must exist first
MATERIALS (master)        ← must exist first
SALES_ORGS (master)       ← must exist first
    │
    ▼  (Credit check performed against outstanding AR)
SALES_ORDERS ─────────────── depends on: CUSTOMERS, SALES_ORGS
SALES_ORDER_ITEMS ────────── depends on: SALES_ORDERS, MATERIALS
    │
    ▼  (Order must be IN PROCESS or COMPLETED)
DELIVERIES ───────────────── depends on: SALES_ORDERS, CUSTOMERS
DELIVERY_ITEMS ───────────── depends on: DELIVERIES, SALES_ORDER_ITEMS
    │
    ▼  (Delivery must be DELIVERED)
INVOICES ─────────────────── depends on: SALES_ORDERS, CUSTOMERS
INVOICE_ITEMS ────────────── depends on: INVOICES, SALES_ORDER_ITEMS
    │
    ▼  (Invoice must be OPEN)
Payment (status update) ──── depends on: INVOICES
```

### Quick Reference

| I want to... | I need first... | BAPI Validation |
|---|---|---|
| Create a PO | Vendor + Material + Plant must exist | Approval threshold + budget check |
| Approve PO | PO must be OPEN | Threshold warning + budget check |
| Receive Goods | PO must exist (APPROVED preferred) | Item qty tolerance check |
| Create Vendor Invoice | PO must be RECEIVED + GR must exist | 3-way match (PO ↔ GR ↔ Invoice) |
| Pay Vendor | Vendor Invoice must exist and be OPEN | House bank determination |
| Close PO | All items received + invoice PAID | Completion validation warnings |
| Create Sales Order | Customer + Product + Plant must exist | Credit check + pricing + ATP |
| Process/Complete Order | Order must exist in correct status | Status chain validation |
| Ship Delivery | Delivery must exist | Movement Type 601 goods issue |
| Confirm Delivery | Delivery must be In Transit | Proof of Delivery + date |
| Record Customer Payment | Customer Invoice must exist and be OPEN | House bank determination |

---

## Linking Keys — How tables connect

| Key | Format | Links |
|---|---|---|
| EBELN | 4500000XXX | PO → PO_ITEMS → GOODS_RECEIPTS → GR_ITEMS → VENDOR_INVOICES |
| VBELN | varies | SALES_ORDERS → ORDER_ITEMS → DELIVERIES → INVOICES |
| LIFNR | 5000000XXX | VENDORS → PURCHASE_ORDERS → VENDOR_INVOICES |
| KUNNR | 1000000XXX | CUSTOMERS → SALES_ORDERS → INVOICES |
| MATNR | varies | MATERIALS / PROC_MATERIALS → line items |
| MBLNR | 5999999XXX | GOODS_RECEIPTS → GR_ITEMS |
| BELNR | 5100000XXX | VENDOR_INVOICES → VI_ITEMS |

---

## Master Data (pre-loaded, no dependencies)

| Table | Count | Description |
|---|---|---|
| CUSTOMERS | 90 | Companies you sell to (ITC, 3M, FedEx, etc.) |
| VENDORS | 33 | Suppliers you buy from (Sun Chemical, Xaar, etc.) |
| MATERIALS | 71 | Products you sell (printers, ink, software) |
| PROC_MATERIALS | 32 | Raw materials you buy (UV ink, paper, printheads) |
| PLANTS | 7 | US01, US02, NL01, NL02, IN01, IN02, IN03 |
| SALES_ORGS | 3 | 1000 (US), 2000 (EMEA), 3000 (India/APAC) |

---

## Company Codes & Currencies

| Company Code | Region | Currency | Plants |
|---|---|---|---|
| PW10 | US | USD ($) | US01, US02 |
| PW20 | EMEA/Europe | EUR | NL01, NL02 |
| PW30 | India/APAC | INR | IN01, IN02, IN03 |

## Purchasing Organizations

| Purch. Org | Company Code | Region |
|---|---|---|
| 1000 | PW10 | US |
| 2000 | PW20 | EMEA |
| 3000 | PW30 | India/APAC |

## Tax Rates (auto-calculated from company code)

| Company Code | Currency | Tax Rate |
|---|---|---|
| PW10 | USD | 10% |
| PW20 | EUR | 21% |
| PW30 | INR | 18% (GST) |

## Approval Thresholds (~$100K equivalent)

| Company Code | Currency | Threshold |
|---|---|---|
| PW10 | USD | $100,000 |
| PW20 | EUR | €92,000 |
| PW30 | INR | ₹8,350,000 |

POs under threshold are **auto-approved** (STATUS = APPROVED).
POs over threshold are set to **OPEN** (needs `approve po`).

## Credit Limits (~$5M equivalent)

| Company Code | Currency | Limit |
|---|---|---|
| PW10 | USD | $5,000,000 |
| PW20 | EUR | €4,600,000 |
| PW30 | INR | ₹415,000,000 |

Warning issued if customer outstanding AR exceeds limit; does not block order creation.

## House Banks

| Company Code | Bank |
|---|---|
| PW10 | Chase Manhattan — USD Account |
| PW20 | ING Bank — EUR Account |
| PW30 | HDFC Bank — INR Account |

## Pricing Procedure (Sales Orders)

| Condition | Type | Logic |
|---|---|---|
| PR00 | Base Price | Unit price as provided |
| K007 | Volume Discount | qty >= 20: 1%, qty >= 50: 3%, qty >= 100: 5% |

Net price = Base Price × (1 - Discount%)

## Order Type Determination (Sales Orders)

| Material Division (SPART) | Order Type | Description |
|---|---|---|
| 10 | ZOR | Equipment |
| 20, 30 | ZCO | Consumables |
| 40 | ZSO | Service / AMC |

## FI Posting Summary

### P2P Postings

| Step | Debit | Credit |
|---|---|---|
| Create PO | GR/IR Clearing | Vendor Payable |
| Receive Goods (Mvt 101) | Stock Account | GR/IR Clearing |
| Vendor Invoice | GR/IR Clearing + Input Tax | Vendor Payable |
| Pay Vendor | Vendor Payable | Bank Account |

### O2C Postings

| Step | Debit | Credit |
|---|---|---|
| Create Sales Order | Customer Receivable | Revenue + Tax Payable |
| Record Payment | Bank Account | Customer Receivable |

---

## AI Features (DeepSeek-V3 via Together AI)

### LLM Query Refinement
- Natural language input is rewritten into structured keywords before routing
- Example: `"what do we owe vendors?"` → `"vendor invoices open"`
- Example: `"move order 4000000001 forward"` → `"process order 4000000001"`

### LLM Bypass (no API call needed)
The following inputs skip the LLM and go directly to the keyword router:
- **Exact match**: `help`, `dashboard`, `tables`, `vendors`, `customers`, `products`, `plants`, `sales orgs`, `create po`, `create order`, `create invoice`, etc.
- **Prefix match**: `approve po `, `receive goods `, `process order `, `ship delivery `, `pay vendor `, `close po `, `create po for`, `create order for`, etc.
- **Contains match**: `purchase order`, `vendor invoice`, `goods receipt`, `create po`, `approve po`, etc.
- **Comma-separated data**: Any input with 2+ comma-separated fields (conversational flow replies)

### LLM Result Summarization
- After every query result, an AI summary is generated asynchronously
- Highlights key numbers, totals, and trends in plain English
- For write operations: confirms what was done

### Raw Comma Input (Auto-Detection)
When a user types 5 comma-separated fields (`Name, Material, Qty, Plant, Price`):
1. Bypasses LLM (no rewriting)
2. Checks if the name is a **vendor** → routes to `write_create_po`
3. Checks if the name is a **customer** → routes to `write_create_sales_order`
4. If both → asks user to specify with `create po for` or `create order for`

### Natural Language Search
- `"do i have vendor Sun Chemical"` → searches VENDORS table
- `"find customer ITC"` → searches CUSTOMERS table
- `"is there a material UV Ink"` → searches all master data tables (VENDORS, CUSTOMERS, MATERIALS, PROC_MATERIALS)

---

## Complete Command Reference

### General
| Command | Description |
|---|---|
| `dashboard` / `summary` | Business overview with counts |
| `tables` | All database tables with row counts |
| `plants` | Factory/warehouse locations |
| `sales orgs` | Sales regions |
| `help` | Full command guide |

### Natural Language Search
| Command | Description |
|---|---|
| `do i have vendor Sun Chemical` | Search vendors by name |
| `find customer ITC` | Search customers by name |
| `is there a material UV Ink` | Search across all master data |

### O2C Read
| Command | Description |
|---|---|
| `orders` / `open orders` / `completed orders` | Sales orders by status |
| `order 4000000001` | Order details |
| `deliveries` / `in transit` / `delivered` | Delivery status |
| `invoices` / `overdue invoices` / `paid invoices` | Customer invoices |
| `revenue by region/customer/product/month/quarter` | Revenue reports |
| `customers` | Customer master list |
| `products` | Product catalog with revenue |

### O2C Write
| Command | Description |
|---|---|
| `create order` | Create sales order (guided) |
| `create order for ITC, OffsetMaster 6080, 10, IN01, 25000` | Create sales order (direct) |
| `process order 4000000001` | Advance status: Open → In Process → Completed |
| `ship delivery 8000000001` | Ship: Open → In Transit |
| `confirm delivery 8000000001` | Confirm: In Transit → Delivered |
| `record payment 9000000001` | Mark customer invoice PAID |
| `record payment 9000000001 partial` | Mark partial payment |

### P2P Read
| Command | Description |
|---|---|
| `purchase orders` / `open purchase orders` | PO list |
| `po vendors` | Vendors with active POs |
| `vendor invoices` / `open vendor invoices` | AP invoices |
| `goods receipts` | GR documents |
| `procurement materials` | Raw materials catalog |
| `procurement spend by vendor/category/plant` | Spend reports |
| `vendors` | Vendor master list |

### P2P Write
| Command | Description |
|---|---|
| `create po` | Create purchase order (guided) |
| `create po for Sun Chemical, UV Ink Black, 50, US01, 120` | Create PO (direct) |
| `Sun Chemical, UV Ink Black, 50, US01, 120` | Create PO (raw comma — auto-detected) |
| `approve po 4500000001` | Approve: OPEN → APPROVED |
| `receive goods 4500000001` | Receive goods + create GR + GR items |
| `create invoice 4500000001` | Create vendor invoice (3-way match, auto tax) |
| `create invoice 4500000001, 45` | Create vendor invoice with 45-day payment terms |
| `pay vendor 5100000001` | Mark vendor invoice PAID |
| `close po 4500000001` | Close completed PO |
