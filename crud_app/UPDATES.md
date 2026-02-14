# PrintWorks SAP CRUD App — Update Log

---

## v1.0 — Initial Flask Connection (2026-02-13)
- Deleted old `chatbot1/` and `chatbot_CRUD/` directories
- Connected `Chatbot V3.py` (user's original file) to Flask app
- Used `importlib` to import from filename with space
- Basic `/api/chat` and `/api/health` endpoints
- `Chatbot V3.py` — zero changes, untouched

## v2.0 — UI Refresh: Lighter Dark Theme (2026-02-13)
- Lifted all colors ~15-20% lighter (less cave-dark)
- Gradient header with shadow
- Glowing status dot, gradient user bubbles
- Subtle radial gradient on chat background
- Custom scrollbar, chip hover lift, input focus ring
- Revamped welcome screen with branded icon, 3 colored badges, 4 quick-start cards

## v3.0 — iOS 26 Liquid Glass Theme (2026-02-13)
- Full glassmorphism: `backdrop-filter: blur(24px) saturate(180%)` on all panels
- 3 animated background color blobs (amber, purple, blue) drifting behind glass
- iOS spring physics: `cubic-bezier(0.34, 1.56, 0.64, 1)` on all interactions
- Bouncy hover (scale + translate), rubber-band press (`:active` snap-down)
- Messages fly in with spring animation
- Header & input bar use heavier `blur(40px)` like iOS nav bars
- Frosted glass bot bubbles, glowing accent send button

## v4.0 — DeepSeek NLP Layer (2026-02-13)
- Added Together AI (DeepSeek-V3) integration in `app.py` only
- **Pass 1:** Rule-based parser runs first (instant, no API call)
- **Pass 2:** If parser returns fallback → DeepSeek rephrases natural language into a command → retries parser
- **Pass 3:** If still no match → DeepSeek answers the question directly as Q&A
- Frontend shows "Interpreted as: ..." hint when DeepSeek rephrased
- Frontend shows purple "DeepSeek AI" badge for direct Q&A answers
- API key loaded from `.env` via `python-dotenv`
- `Chatbot V3.py` — still untouched

## v4.1 — Smarter Retry on Empty Results (2026-02-13)
- DeepSeek refinement now also triggers when parser returns "(No results found)"
- Previously only triggered on "I didn't understand that" fallback
- Fixes: natural phrases where parser matches keyword but `extract_name()` picks up leftover words as bad filters
- Example: "give me vendor details" → parser filters by name "details" → no results → DeepSeek rephrases to "vendors" → retry → works

## v4.2 — Full Table Access: Materials & Plants (2026-02-13)
- Added direct queries for 2 tables not covered by Chatbot V3's parser
- `materials` → full product catalog from `MATERIALS` table (id, description, category, division, UOM, price)
- `plants` → all manufacturing/warehouse locations from `PLANTS` table (id, name)
- Imported `query` and `fmt_table` from Chatbot V3 for reuse
- Added `handle_extra_commands()` pre-check — runs before `parse_and_execute`
- Updated DeepSeek REFINE_PROMPT to know about the new commands
- All 13 tables in PRINTWORKS schema are now accessible via commands
- `Chatbot V3.py` — still untouched

## v5.0 — Create Vendor/Supplier Invoice with Validation (2026-02-13)
- New command: `create vendor invoice [PO number]` (or `create supplier invoice`)
- **Check 1 — PO exists?** → If not: "PO not found. Create a PO first, then GR, then invoice."
- **Check 2 — PO status = RECEIVED?** → If OPEN/APPROVED: "Receive goods first." If CLOSED: "Cycle already complete."
- **Check 3 — GR exists?** → If no GR posted: "Receive goods first before creating invoice."
- **Check 4 — Duplicate invoice?** → If invoice already exists for PO: shows existing invoice ID
- All 4 checks pass → generates invoice number, inserts into `VENDOR_INVOICES`, returns invoice ID
- Tax auto-calculated by company code (US 8%, EU 21%, India 18%)
- Due date set to 30 days from creation
- Imported `execute` and `fmt_currency` from Chatbot V3
- "Supplier" works everywhere as alias for "vendor": create/generate/new/raise supplier invoice
- Updated DeepSeek REFINE_PROMPT with the new command (knows supplier = vendor)
- Added "Create Invoice" button on homepage between Receive Goods and Pay Vendor
- `Chatbot V3.py` — still untouched

## v5.1 — Dashboard Fix: HANA Uppercase Keys (2026-02-13)
- Fixed 500 error on dashboard — HANA returns column aliases in UPPERCASE (`OPEN_ORDERS`) but `show_dashboard()` in Chatbot V3 accesses lowercase (`open_orders`)
- Added `app_show_dashboard()` in `app.py` with case-insensitive key helper `_get()`
- Dashboard now intercepted by `handle_extra_commands()` before reaching Chatbot V3
- `Chatbot V3.py` — still untouched

## v6.0 — Lifecycle Views + Validations + Business Intelligence (2026-02-13)
8 new features added to `app.py` — organized into 5 groups:

**Group A: Pre-checks before creating PO**
- `check vendor [name]` — LIKE search on VENDORS, shows ID, city, country, industry, total POs, total value
- `check material [name]` — word-by-word LIKE search on PROC_MATERIALS, shows ID, category, UOM, price

**Group B: PO Lifecycle View**
- `po status [number]` — one command showing: PO header + vendor → line items → GR status → invoice/payment status → visual progress bar

**Group C: Application-layer Validations (intercept before Chatbot V3)**
- `receive goods` validation — blocks if PO is OPEN (must be APPROVED first), passes through on success
- `close po` validation — blocks if no vendor invoice or invoice not PAID, passes through on success

**Group D: Sales Order Lifecycle View**
- `order status [number]` — one command showing: order header + customer → line items → delivery status → invoice/payment → visual progress bar

**Group E: Business Intelligence**
- `overdue purchase orders` — POs where delivery date < today AND status = OPEN/APPROVED, shows days overdue
- `pending actions` — counts of: POs awaiting approval, POs awaiting GR, unpaid vendor invoices, overdue customer invoices, open deliveries

**Other changes:**
- Updated `handle_extra_commands()` routing for all 8 features (validations return None on pass-through)
- Updated DeepSeek REFINE_PROMPT with 6 new read commands
- Added 6 suggestion chips: Check Vendor, Check Material, PO Status, Order Status, Overdue POs, Pending Actions
- `Chatbot V3.py` — still untouched

## v6.1 — UX Polish (2026-02-13)

**Input bar glow animation:**
- Added rotating ambient light on the input bar border when focused — soft orange, 12s per revolution, barely perceptible
- Added soft orange aura (diffused `drop-shadow`) behind the bar on focus
- On loading: seamless handoff — light accelerates from 30 to 180 deg/sec, cross-fades from orange to multi-color (amber → purple → blue)
- On response: smooth deceleration back to slow orange, multi-color fades out
- JS-driven `requestAnimationFrame` loop so the angle never resets between state transitions
- Two gradient layers (`::before` for focus, `.glow-loading-ring` for loading) cross-faded via opacity transitions (0.8s ease-in-out)

**REFINE_PROMPT improvements:**
- Added "Natural language mapping examples" section with ~20 phrasings
- DeepSeek now correctly maps "is vendor X available", "do we have X", "what's pending", etc. to the right commands

**Bug fixes:**
- `check material CP-BELT-TIMING` now works — added `MATNR` (material ID) matching alongside `MAKTX` (description). Previously only searched description, so typing a material ID returned "not found"
- Fixed invoice creation routing — "create a supplier invoice", "create the vendor invoice", "invoice for [number]" now all work. Articles (a/an/the) are stripped before matching so natural phrasing no longer breaks the trigger
- Added natural language routing for vendor payments — "payment for", "payment to", "make a payment", "pay invoice", "pay supplier" now correctly map to `pay vendor [number]` via `parse_and_execute` passthrough
- `Chatbot V3.py` — still untouched

## v7.0 — BAPI Mode Toggle + Circular Theme Animation (2026-02-14)
- Added **BAPI mode** — toggle switch in header to switch between ChatV3 and BAPI mode
- Backend: `handle_bapi_mode()` in `crud_app/app.py` routes write commands to BAPI functions (`BAPI_PO_CREATE1`, `BAPI_SALESORDER_CREATEFROMDAT2`, `BAPI_GOODSMVT_CREATE`, `BAPI_INCOMINGINVOICE_CREATE`, `BAPI_ACC_DOCUMENT_POST`)
- Read commands fall through to ChatV3 in both modes
- Imported BAPI module via `importlib.util`
- **Circular theme animation**: Semi-transparent `clip-path: circle()` color wash expanding from toggle button, all themed elements morph in-place via CSS transitions (blobs, header, buttons, bubbles, chips, input glow, loading dots, welcome icon)
- Purple theme overrides for BAPI mode (`body.bapi-active` CSS class)
- Suggestion chips update dynamically per mode
- `Chatbot V3.py` — still untouched

## v7.1 — Chip Burst Animation (2026-02-14)
- Two-phase animation when switching modes:
  - **Exit**: Old chips collapse **TO center** of suggestions container (scale 0.7 + fade out, 30ms stagger, 0.3s)
  - **Enter**: New chips burst **FROM center** outward to natural flex positions (scale 0.7→1 + fade in, 50ms stagger, 0.45s)
- Uses FLIP technique (First, Last, Invert, Play)
- `.chip-animating` temporary CSS class (only `will-change`, removed after animation)
- Existing chip styles untouched
- Backup saved in `crud_app/chip-styling-backup.md`

## v7.2 — Optional PO Creation Parameters (2026-02-14)
- PO creation now accepts optional overrides: **price**, **date**, **plant**
- If omitted, defaults are used (material master price, today+30 days, vendor country plant)
- Format: `create po for [vendor] - [material] - [qty] - price [amt] - date [YYYY-MM-DD] - plant [code]`
- Works in both ChatV3 and BAPI modes
- **`Chatbot V3.py`** — `write_create_po()` updated with `price` and `date` params; parser extracts flags via regex
- **`BAPI/bapi_demo.py`** — `BAPI_PO_CREATE1` respects `POHEADER.DELIV_DATE` and `POHEADER.PLANT`
- Help text updated across all locations

## v7.3 — PO Creation Confirmation Step (2026-02-14)
- PO creation now shows a **preview** before inserting into the database
- Preview displays: vendor, material, quantity, unit price, total value, plant, company code, delivery date
- User must type **"yes"** to confirm or **"no"** to cancel
- Green **Confirm** and red **Cancel** buttons appear below the preview (clickable)
- Works in both ChatV3 and BAPI modes
- **`Chatbot V3.py`** — split `write_create_po()` into `preview_create_po()` (validate + preview) and `confirm_create_po()` (execute INSERT)
- **`crud_app/app.py`** — added `_pending_po` state dict; `chat()` intercepts yes/no when pending; handles preview tuples from both ChatV3 and BAPI parsers
- **`crud_app/templates/index.html`** — added `.confirm-actions`, `.confirm-btn`, `.confirm-yes`, `.confirm-no` CSS; added `sendConfirm()` JS function

## v7.4 — BAPI Mode: Full P2P Cycle with Custom Values (2026-02-14)
- In **BAPI mode**, confirming a PO now runs the **full P2P cycle** (not just PO creation)
- Cycle: `BAPI_PO_CREATE1` → `BAPI_GOODSMVT_CREATE` → `BAPI_INCOMINGINVOICE_CREATE` → `BAPI_ACC_DOCUMENT_POST` → Close PO
- All 4 BAPI functions execute with full validation rules and BAPIRET2 messages (same as `run p2p cycle`)
- Custom values (vendor, material, qty, price, date, plant) feed into Step 1; Steps 2-4 chain automatically using the created PO number
- **ChatV3 mode** unchanged — still creates only the PO on confirm (one-by-one execution)
- **`BAPI/bapi_demo.py`** — added `run_custom_p2p_cycle()` function
- **`crud_app/app.py`** — BAPI confirm handler now calls `run_custom_p2p_cycle` via `_capture_print`; stores `_custom_price` and `_custom_date` in pending data
- Also fixed KeyError bug when BAPI mode preview came via ChatV3 fallthrough (DeepSeek refinement path)

## v8.0 — Interactive P2P Cycle with Step Confirmation (2026-02-14)

**New P2P Flow (6 steps):**
1. Create PO (custom values) → 2. Approve PO (NEW) → 3. Receive Goods → 4. Supplier Invoice → 5. Payment → 6. Close PO

**Interactive step confirmation** — after each step, 3 buttons appear:
- **Continue** — run next step only, then ask again
- **Continue All** — run all remaining steps automatically (auto-mode)
- **Stop** — stop immediately, no rollback

**New BAPI function: `BAPI_PO_RELEASE`** (`BAPI/bapi_demo.py`):
- Logic replicated from ChatV3's `write_approve_po()` — same SQL, same status checks
- Wrapped in BAPI-style BAPIRET2 messages (vendor validation, threshold check, status update)
- Also available standalone: `approve po 4500000xxx` in BAPI mode

**Step-based architecture** (`BAPI/bapi_demo.py`):
- `P2P_STEPS` — list of step definitions (id, name, icon, bapi)
- 6 independent step functions: `p2p_step_create_po`, `p2p_step_approve_po`, `p2p_step_goods_receipt`, `p2p_step_invoice`, `p2p_step_payment`, `p2p_step_close_po`
- `run_p2p_step(index, ctx)` — runs single step, returns (success, step_info, output, updated_ctx)
- `run_p2p_remaining_steps(index, ctx)` — runs all steps from index onwards (auto-mode)
- Each step returns `(success, output, context_updates)` — easy to extend

**Backend flow controller** (`crud_app/app.py`):
- `_p2p_flow` state dict: `active`, `step_index`, `ctx`, `auto_mode`
- `chat()` intercepts continue/continue_all/stop when P2P flow is active
- On PO confirm in BAPI mode → runs Step 1 → starts interactive flow
- Response includes `p2p_pending`, `p2p_next_step`, `p2p_step_index`, `p2p_total_steps`

**Frontend** (`crud_app/templates/index.html`):
- 3 P2P action buttons: Continue (green), Continue All (blue), Stop (red)
- `sendP2PAction()` JS function, `_buildP2PButtons()` helper
- `.p2p-actions`, `.p2p-btn`, `.p2p-continue`, `.p2p-auto`, `.p2p-stop` CSS classes
- Next step hint shown above buttons

## v8.1 — Approve Step Auto-Skip (2026-02-14)
- `p2p_step_approve_po()` now checks PO status before calling `BAPI_PO_RELEASE`
- If PO was auto-approved at creation (value below threshold), step is **skipped** with a message instead of failing
- Fixes: low-value POs showing "PO is already APPROVED" error during P2P flow

## v8.2 — Standalone BAPI Script: Ajay.py (2026-02-14)
- Created `Ajay.py` — standalone script with all BAPI logic, no Flask/UI dependency
- Contains: HANA connection, BAPIRET2, all 6 BAPI functions, P2P step functions, O2C demo
- Interactive terminal menu: pick P2P or O2C, enter custom values, step-by-step confirmation via keyboard (`C`/`A`/`S`)
- Designed for sharing — `pip install hdbcli && python Ajay.py`

## v8.3 — Chip Animation & 3D Polish (2026-02-14)

**Simultaneous animation:**
- Removed stagger delay — all chips now exit and enter at the same time instead of one-by-one

**Spring physics:**
- Exit: `cubic-bezier(0.6, -0.28, 0.74, 0.05)` — slight pull-back before collapsing (wind-up)
- Enter: `cubic-bezier(0.34, 1.28, 0.64, 1)` — ~105% overshoot then settle back (spring bounce)

**3D chip styling:**
- Top-to-bottom gradient background (lighter on top)
- Bright top border edge (`rgba(255,255,255,0.25)`) simulating light source
- Multi-layer box-shadow: outer depth shadow + inner highlight
- Hover: lifts higher (`-3px`), deeper shadow for floating effect

**Rotation wobble:**
- Each chip gets a random rotation (-12deg to +12deg) during flight
- Exit: chips tumble as they collapse to center
- Enter: chips start tilted, straighten as they land — every toggle looks different

**Motion blur:**
- Chips blur to `4px` during flight, sharpen to `0` on landing
- Simulates cinematic motion blur — fast movement = out of focus

**Micro-stagger (15ms):**
- Subtle 15ms delay between each chip — feels simultaneous but organic
- Ripple/wave effect instead of robotic all-at-once snap

**FPS optimizations:**
- `transition: all` → specific properties only (`transform`, `opacity`, `filter`)
- `transform: translateZ(0)` on all chips — GPU compositing from the start
- `backdrop-filter: none` during animation via `.chip-animating` — eliminates per-frame blur recalculation (biggest FPS win)

## v8.4 — P2P Flow Natural Language Routing (2026-02-14)
- In BAPI mode, "p2p flow", "run p2p", "p2p", "procure to pay", "start p2p", "full cycle" etc. now trigger an **interactive P2P guide** instead of the old hardcoded demo
- Shows instructions to provide vendor/material/qty → user types `create po for ...` → confirms preview → full 6-step interactive P2P cycle starts with Continue/Continue All/Stop buttons
- Added more trigger phrases: `p2p flow`, `p2p`, `start p2p`, `procure to pay`, `run full cycle`, `full cycle`
- Updated DeepSeek REFINE_PROMPT with P2P and O2C natural language mappings ("procurement cycle", "sales cycle", etc.)
- `Chatbot V3.py` — still untouched

