# Test Data & Script — Tire and Battery Transactions

Companion to `full-flow-masterdata-to-maintenance.md`. This walks through
the Tire and Battery Management modules end-to-end: master data setup,
mount/dismount/retread/dispose transactions, and verifying the master
record's status stays in sync throughout.

---

## Step 1 — Master Data Prerequisites

**1a. Branch** (skip if you already have one from the earlier guide)
Sidebar → Master Data → Branches → New
- Code: HQ
- Name: Head Office

**1b. Vehicle Type**
Sidebar → Master Data → Vehicle Types → New
- Code: SEDAN
- Name: Sedan
- Category: LIGHT

**1c. Vehicle Brand & Model** (now required — see the recent Brand/Model
master enhancement)
Sidebar → Master Data → Vehicle Brands → New → "Toyota"
Sidebar → Master Data → Vehicle Models → New → Brand: Toyota, Name: "Vios"

**1d. Vendor**
Sidebar → Master Data → Vendors → New
- Code: TIRE-SVC
- Name: ABC Tire & Battery Center
- Type: Services

**1e. Vehicle**
Sidebar → Master Data → Vehicles → New
- Conduction Number: TB-2026-001
- Vehicle Type: Sedan, Brand: Toyota, Model: Vios (select from the new dropdowns)
- Year: 2024, Branch: Head Office
- Current Odometer: 15000

**1f. Tire**
Sidebar → Master Data → Tires → New
- Serial Number: TIRE-001
- Brand: Bridgestone
- Size: 185/65R15
- Type: Radial
- Vendor: ABC Tire & Battery Center
- Status should show IN_STOCK after creation

**1g. Battery**
Sidebar → Master Data → Batteries → New
- Serial Number: BATT-001
- Brand: Motolite
- Capacity: 45 Ah, Voltage: 12
- Vendor: ABC Tire & Battery Center
- Status should show IN_STOCK after creation

---

## Step 2 — System Administration (numbering, if not already set up)

- Document Types → create TIR (Tire Transaction) and BAT (Battery
  Transaction) if they don't already exist, Auto Numbering checked
  (these are NOT auto-seeded by `flask seed all` — same as every other
  document type, they're created once via this screen)
- Numbering → create schemes for both (prefix TIR / BAT, year, 6 digits)
- Neither requires approval by default — leave "Requires Approval"
  unchecked for both unless you want to test the approval path too (see
  Step 6)

---

## Step 3 — Tire Transaction Lifecycle

**3a. Mount**
Sidebar → Transactions → Tire Transactions → New
- Tire: search "TIRE-001" (use the searchable field)
- Action: Mount
- Vehicle: search "TB-2026-001" or "Toyota Vios"
- Transaction Date: today
- Odometer at Service: 15000
- Save

Verify: Master Data → Tires → TIRE-001 → status is now **MOUNTED**, and the
tire shows linked to vehicle TB-2026-001.

**3b. Dismount**
New Tire Transaction:
- Tire: TIRE-001
- Action: Dismount
- Vehicle: leave blank (dismounting frees it back to stock)
- Transaction Date: today + a few weeks
- Save

Verify: Tire status is back to **IN_STOCK**, no vehicle link.

**3c. Retread**
New Tire Transaction:
- Tire: TIRE-001
- Action: Retread
- Save

Verify: Tire status is now **RETREADED**.

**3d. Mount again after retread**
New Tire Transaction: Tire TIRE-001, Action Mount, Vehicle TB-2026-001 →
verify status returns to MOUNTED (retreaded tires can still be mounted).

**3e. Dispose**
New Tire Transaction:
- Tire: TIRE-001
- Action: Dispose
- Save

Verify: Tire status is now **DISPOSED**, and its Active badge on the Tires
list shows "No" (soft-deleted via `is_active=False` — it stays visible in
the list rather than disappearing, same convention as every other master
record's deactivation in this system).

**Negative test:** try creating a Tire Transaction with an invalid action
(if testing via API/shell) — should be rejected with a clear error message
listing the valid actions (MOUNT, DISMOUNT, RETREAD, DISPOSE).

---

## Step 4 — Battery Transaction Lifecycle

**4a. Mount**
Sidebar → Transactions → Battery Transactions → New
- Battery: search "BATT-001"
- Action: Mount
- Vehicle: search "TB-2026-001"
- Transaction Date: today
- Save

Verify: Master Data → Batteries → BATT-001 → status is now **MOUNTED**.

**4b. Dismount**
New Battery Transaction: Battery BATT-001, Action Dismount, no vehicle →
Verify: status back to **IN_STOCK**.

**4c. Dispose**
New Battery Transaction: Battery BATT-001, Action Dispose →
Verify: status is now **DISPOSED**, Active badge shows "No" on the
Batteries list (stays visible, same soft-delete convention as Tires).

(Battery has no Retread action — only MOUNT/DISMOUNT/DISPOSE, unlike Tire.)

---

## Step 5 — Cross-Cutting Checks

**Attachments:** on any Tire/Battery Transaction detail page, upload a
photo (e.g. tread-wear photo, battery test slip) via the Attachments
panel — confirm thumbnail preview and download work.

**Print:** open a Tire or Battery Transaction detail page → Print → confirm
the letterhead pulls from Company Profile and the action/date/vehicle
details are correct.

**Audit Trail:** Sidebar → Audit Trail → filter by table `tire_transactions`
or `battery_transactions` → confirm every action above is logged
automatically with old/new status values.

**Vehicle detail page:** open the Toyota Vios (TB-2026-001) detail page —
there's currently no dedicated "Tire/Battery History" tab (only Maintenance
History and Registration History exist from earlier phases). If you'd like
one added showing mount/dismount history per vehicle, similar to the
existing Maintenance History tab, let me know and I'll build it — it's a
small, well-understood addition following the same pattern.

---

## Step 6 — Optional: Approval-Gated Tire/Battery Transactions

By default TIR/BAT document types don't require approval. If your actual
process requires sign-off (e.g. for high-cost tire replacements), you can
flip "Requires Approval" on for TIR/BAT in Document Types, then set up an
Approval Path + Approval Matrix the same way as Maintenance Orders. The
Tire/Battery Transaction service already supports the full submit → approve
→ reject → return → cancel lifecycle via the shared Approval Engine — it's
just not exercised by default since these are typically low-risk,
frequent transactions.

---

## Known Gaps to Flag if You Hit Them

- No dedicated "Tire History" / "Battery History" tab on the Vehicle detail
  page yet (only Maintenance + Registration history exist)
- No dashboard KPI for tire/battery stock levels yet (Phase 4 — Dashboard)
- Tire tread-depth tracking over time (initial vs. current) exists on the
  Tire master record but isn't yet surfaced as a wear-trend chart anywhere
