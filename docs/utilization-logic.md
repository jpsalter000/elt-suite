# Utilization and project-margin logic

This pipeline replaces a weekly Excel process: the *Utilization Report template* workbook, built from VBA macros, a Power Pivot data model with DAX measures, and pivot reports. NetSuite data now arrives daily through the API. dbt views in Postgres hold the business rules, and Power BI reads the `reporting` schema.

This page records where each rule came from and where it lives now. It also records every place the dbt models deliberately differ from the workbook. The dbt unit tests in `transform/models/**/_*.yml` pin each rule. `tests/test_transform.py` checks the views against an independent Python implementation, for every employee-week and every project.

## From weekly macro to daily pipeline

| Workbook step (weekly, manual) | Pipeline (daily, automated) |
| --- | --- |
| Paste a NetSuite saved-search export into *Raw Data* | `extract_netsuite.*` tasks pull ten SuiteQL tables through the REST API, incrementally where NetSuite has `lastmodifieddate` |
| VBA `transRaw`: split `"<id> <name>"` strings; Item = Project Item, else Internal Item | Staging joins on NetSuite internal ids; the time entry's item is a single field |
| VBA `allEmpWeeks`: *DateTable* = distinct logged dates; *All Weeks* = current employees × those dates | `int_calendar` (every day) and `int_employee_days` (every employee, every day employed) |
| *Task Type Breakdown* table: Item → Time Category | `seeds/time_categories.csv` |
| Power Pivot calculated columns and DAX measures | `int_*` models and `reporting.rpt_*` views; DAX measures in the Power BI model |
| Pivot tables, slicers, timelines, VBA collapse buttons and the Top-15 macro | Power BI pages, slicers and drill-down; `Client Rank` / `rpt_top_clients` |

## Utilization

**Categories.** `time_categories.csv` maps every time item to one category:

| Category | Counts as worked | Client-facing | Workbook category |
| --- | --- | --- | --- |
| Project Work | yes | yes | Project Work |
| Client - BD | yes | yes | Client - BD |
| Internal - Productive | yes | no | Internal - Productive |
| Internal - Admin | yes | no | Internal - ADMIN |
| Exempt | no | no | Exempt |

**Weekly measures.** Weeks start on Sunday (the workbook's `Calendar Week`).

| Measure | Definition | Workbook measure |
| --- | --- | --- |
| Expected hours | Weekdays while employed: `max(standard hours per day − exempt hours that day, 0)`. Weekends: 0. | All Weeks `Expected Hours` |
| Net Utilization % | worked hours ÷ expected hours | `Actual over Expected`, shown as "Net Utilization %" |
| Project Work % | project-work hours ÷ expected hours | `Project Work %` |
| Client BD % | client-BD hours ÷ expected hours | `Client BD %` |
| Internal % | (internal productive + internal admin hours) ÷ expected hours | `Internal %` |
| Exempt % | exempt hours ÷ standard weekday hours | `Exempt %` |

Hours logged at weekends count toward their week's worked hours, as in the workbook. Percentages are null when the denominator is zero.

## Project margin

**Line rules** (`int_project_financial_lines`):

- **Sign.** NetSuite records credits as negative. Amounts on income and deferred-revenue accounts are negated, so revenue is positive on sales documents and negative on credit memos. This replaces the workbook's `ABS()` on accounts 40110 and 40120.
- **Account class.** `seeds/account_classes.csv` assigns each account a class:
  - excluded: 32000, opening balances
  - revenue: 40000
  - pass-through travel: 40110
  - pass-through incentive: 40120
  - field cost: 50100, 50200
  - incentive cost: 50300
  - travel cost: 50400
- **Excluded lines.** A line contributes nothing when any of these apply, and the reason is kept on the line:
  - the account is excluded;
  - the transaction status is *Rejected by Supervisor*;
  - it is a purchase order no longer *Pending Supervisor Approval* or *Pending Billing* (once billed, the vendor bill carries the cost);
  - it posts to deferred revenue outside a sales order, invoice or credit memo;
  - it is a vendor return authorization.
- **Price.** For *4. Finalized* and *5. Closed* projects, price is invoices plus credit memos. For every other status, price is sales orders.

**Project totals** (`rpt_project_margin`):

| Field | Definition |
| --- | --- |
| Time cost | hours × the employee's NetSuite labor cost |
| Burdened time cost | hours × the employee's burdened rate (the workbook's "CB" rate) |
| Net revenue | price − pass-through travel revenue − pass-through incentive revenue |
| Project profit | price − (field cost + incentive cost + travel cost + time cost) |
| Contribution margin | the same, using burdened time cost |
| Profit % | profit ÷ net revenue |
| CM % | contribution margin ÷ net revenue |

## Deliberate differences from the workbook

| Topic | Workbook | dbt | Why |
| --- | --- | --- | --- |
| Calendar | Only dates on which someone logged time | Every day from `calendar_start` to the latest logged day | A weekday with no entries at all lost its expected hours |
| Employees | Only *Current* employees, from their start date | Everyone, from hire date to release date | Released staff vanished from history; their past weeks now keep their real denominator |
| Expected hours | `hours per day − exempt`, which could go negative (e.g. a 10-hour PTO day) | Floored at zero | A negative denominator understated utilization for the whole week |
| Exempt % | exempt ÷ (expected + exempt) | exempt ÷ standard weekday hours | Same intent, without the negative edge case |
| Dead categories | Flags for "Productive", "Non-Client", "Other Non-Productive" and "Non-Client: Productive"; no current item mapped to them | Dropped; unmapped items are reported by `assert_logged_items_are_categorized` (warn) | Silent zeros hid mapping gaps |
| Matching | Excel's case-insensitive text comparison on `"<id> <name>"` strings | NetSuite internal-id joins; category matching on `lower(item_name)` | Robust to renames and typos |
| Pass-through in margin | Travel and Incentive columns included pass-through *revenue* lines, then were subtracted from price, so pass-through came out twice | Pass-through revenue is shown and netted out of revenue only; travel and incentive are costs | Profit no longer penalises reimbursed costs twice |
| Division by zero | `DIVIDE` in some measures, raw `/` in `ContributionMarginPercentage` | `/ nullif(…, 0)` everywhere; null, not an error | Consistent behaviour |
| Cost rates | Typed into the *Employee Values* sheet | NetSuite `laborcost` and `custentity_burdened_cost` on the employee record | One source of truth, refreshed daily |
| Unmatched rows | Time for employees missing from *Employees* silently dropped out of the denominator | Kept; relationship tests warn | Visible data-quality gaps |

**Dropped as presentation-only:**
- the pivot collapse buttons;
- the Lower/Upper Bound macro;
- the orphaned *Calculated Financials* sheet (an older copy of the margin logic that nothing referenced);
- the external link to a 2015 workbook;
- workbook protection.
