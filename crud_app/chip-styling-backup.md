# Chip Styling Backup — Pre-Burst Animation

**Date**: 2026-02-14
**Purpose**: Snapshot of chip/suggestion CSS & JS before adding chip burst animation. Use this to revert if needed.

---

## CSS — `.suggestions` & `.chip` (lines 510–549)

```css
/* ── Suggestion chips ── */
.suggestions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 12px;
}
.chip {
    font-size: 12px;
    padding: 6px 14px;
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: 20px;
    color: var(--text-secondary);
    cursor: pointer;
    transition: all 0.4s var(--spring);
    font-weight: 500;
}
.chip:hover {
    background: var(--glass-bg-hover);
    color: var(--text-primary);
    border-color: var(--glass-border-hover);
    transform: translateY(-2px) scale(1.04);
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
.chip:active {
    transform: translateY(0) scale(0.97);
    transition-duration: 0.1s;
}
.chip.write {
    background: rgba(245,158,11,0.1);
    border-color: rgba(245,158,11,0.2);
    color: #fbbf24;
}
.chip.write:hover {
    background: rgba(245,158,11,0.18);
    border-color: rgba(245,158,11,0.4);
    color: #fcd34d;
}
```

## CSS — BAPI-active chip overrides (lines 294–304)

```css
body.bapi-active .chip.write {
    background: rgba(139,92,246,0.1);
    border-color: rgba(139,92,246,0.2);
    color: #a78bfa;
}
body.bapi-active .chip.write:hover {
    background: rgba(139,92,246,0.18);
    border-color: rgba(139,92,246,0.4);
    color: #c4b5fd;
    box-shadow: 0 4px 20px rgba(139,92,246,0.15);
}
```

## JS — `updateSuggestions()` (lines 1019–1081)

```javascript
function updateSuggestions() {
    if (currentMode === 'bapi') {
        suggestionsDiv.innerHTML = `
            <span class="chip" data-q="dashboard">Dashboard</span>
            <span class="chip" data-q="orders">Orders</span>
            <span class="chip" data-q="purchase orders">Purchase Orders</span>
            <span class="chip" data-q="vendors">Vendors</span>
            <span class="chip" data-q="customers">Customers</span>
            <span class="chip write" data-q="create po for " data-focus="true">BAPI: Create PO</span>
            <span class="chip write" data-q="create order for " data-focus="true">BAPI: Create SO</span>
            <span class="chip write" data-q="receive goods " data-focus="true">BAPI: Goods Receipt</span>
            <span class="chip write" data-q="create vendor invoice " data-focus="true">BAPI: Invoice</span>
            <span class="chip write" data-q="pay vendor " data-focus="true">BAPI: Pay Vendor</span>
            <span class="chip write" data-q="run p2p cycle">Run P2P Cycle</span>
            <span class="chip write" data-q="run o2c cycle">Run O2C Cycle</span>
            <span class="chip write" data-q="run both">Run Both Demos</span>
            <span class="chip" data-q="help">Help</span>
        `;
    } else {
        suggestionsDiv.innerHTML = `
            <span class="chip" data-q="dashboard">Dashboard</span>
            <span class="chip" data-q="orders">Orders</span>
            <span class="chip" data-q="open orders">Open Orders</span>
            <span class="chip" data-q="customers">Customers</span>
            <span class="chip" data-q="deliveries">Deliveries</span>
            <span class="chip" data-q="invoices">Invoices</span>
            <span class="chip" data-q="overdue invoices">Overdue</span>
            <span class="chip" data-q="purchase orders">Purchase Orders</span>
            <span class="chip" data-q="open purchase orders">Open POs</span>
            <span class="chip" data-q="vendors">Vendors</span>
            <span class="chip" data-q="vendor invoices">Vendor Invoices</span>
            <span class="chip" data-q="goods receipts">Goods Receipts</span>
            <span class="chip" data-q="procurement materials">Proc. Materials</span>
            <span class="chip" data-q="products">Products</span>
            <span class="chip" data-q="revenue by region">Revenue</span>
            <span class="chip" data-q="check vendor " data-focus="true">Check Vendor</span>
            <span class="chip" data-q="check material " data-focus="true">Check Material</span>
            <span class="chip" data-q="po status " data-focus="true">PO Status</span>
            <span class="chip" data-q="order status " data-focus="true">Order Status</span>
            <span class="chip" data-q="overdue purchase orders">Overdue POs</span>
            <span class="chip" data-q="pending actions">Pending Actions</span>
            <span class="chip write" data-q="create order for " data-focus="true">+ Create Order</span>
            <span class="chip write" data-q="create po for " data-focus="true">+ Create PO</span>
            <span class="chip write" data-q="approve po " data-focus="true">Approve PO</span>
            <span class="chip write" data-q="receive goods " data-focus="true">Receive Goods</span>
            <span class="chip write" data-q="create vendor invoice " data-focus="true">Create Invoice</span>
            <span class="chip write" data-q="pay vendor " data-focus="true">Pay Vendor</span>
            <span class="chip write" data-q="close po " data-focus="true">Close PO</span>
            <span class="chip" data-q="help">Help</span>
        `;
    }
    // Re-attach chip listeners
    suggestionsDiv.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            msgInput.value = chip.dataset.q;
            if (chip.dataset.focus === 'true') {
                msgInput.focus();
            } else {
                sendMessage();
            }
        });
    });
}
```
