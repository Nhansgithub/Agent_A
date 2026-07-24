# final_PRD_Expense Tracker

## Problem
Field employees submit expenses by emailing photos of receipts to finance, who re-key them by hand.
This is slow (5-7 days to reimbursement) and error-prone, and employees have no visibility into where
a claim is.

## Solution
A mobile-first expense tracker. An employee photographs a receipt; OCR extracts amount, date, and
merchant; the employee confirms and submits. Managers approve or reject in one tap. Finance sees an
export-ready ledger.

## Requirements
- Capture a receipt photo and auto-extract amount, date, merchant.
- Let the employee edit the extracted fields before submitting.
- Route each claim to the employee's manager for approval.
- Notify the employee at each state change (submitted, approved, rejected, paid).
- Export approved claims as CSV for the accounting system.

## Scope
In scope: capture, approval, notification, export. Out of scope for v1: multi-currency, corporate
card reconciliation, per-project budget tracking.
