#!/usr/bin/env python3
"""
stocky_export.py — Export your Stocky data before Shopify shuts it down.

Stocky is discontinued on 31 August 2026. Suppliers cannot be exported from
Stocky's UI, and historical purchase orders cannot be imported into Shopify's
native inventory management. This script pulls everything the Stocky API
exposes and writes it to plain JSON + CSV that you keep forever.

Read-only. This script only issues GET requests. It cannot modify or delete
anything in Stocky or in your Shopify store.

No dependencies. Python 3.8+ standard library only. No pip install required.

Usage:
    python3 stocky_export.py --store yourstore.myshopify.com --key YOUR_API_KEY

Or create a file named `.env` next to this script:
    STOCKY_STORE=yourstore.myshopify.com
    STOCKY_API_KEY=your_api_key_here

Then just:
    python3 stocky_export.py

MIT licensed. Issues and PRs welcome.
"""

import argparse
import csv
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://stocky.shopifyapps.com/api/v2"

# Endpoints exposed by the Stocky v2 API.
# Docs: https://stocky.shopifyapps.com/api/docs/v2.html
ENDPOINTS = [
    "suppliers",
    "purchase_orders",
    "stock_adjustments",
    "stock_adjustment_items",
    "tax_types",
]

PAGE_LIMIT = 250        # requested page size; server may cap this lower
MAX_PAGES = 2000        # safety valve against an infinite pagination loop
RETRIES = 4
BACKOFF = 2.0           # seconds, doubled per retry
THROTTLE = 0.35         # polite delay between requests


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def build_request(url, store, key):
    req = urllib.request.Request(url)
    # Stocky expects these two headers exactly. The "API KEY=" prefix is not a typo.
    req.add_header("Store-Name", store)
    req.add_header("Authorization", f"API KEY={key}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "stocky-export/1.0 (+https://github.com/)")
    return req


def fetch(url, store, key):
    """GET a URL, returning parsed JSON. Retries on transient failures."""
    ctx = ssl.create_default_context()
    last_err = None

    for attempt in range(RETRIES):
        try:
            req = build_request(url, store, key)
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return None
                return json.loads(raw)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass

            if e.code in (401, 403):
                raise SystemExit(
                    f"\n  Authentication failed (HTTP {e.code}).\n\n"
                    f"  Check that:\n"
                    f"    - your store name is the full myshopify domain, e.g. mystore.myshopify.com\n"
                    f"    - your API key was copied from Stocky > Preferences > API Access\n"
                    f"    - Stocky API access is ACTIVATED on your account. If the API Access\n"
                    f"      section is missing in Stocky, email Shopify Support and ask them to\n"
                    f"      'activate Stocky API access'. This can take a day or two, so do it now.\n\n"
                    f"  Server said: {body}\n"
                )

            if e.code == 404:
                # Some endpoints may not exist on every account. Skip, don't die.
                return None

            if e.code == 429 or 500 <= e.code < 600:
                last_err = f"HTTP {e.code}"
                sleep = BACKOFF * (2 ** attempt)
                print(f"      {last_err}, retrying in {sleep:.0f}s "
                      f"({attempt + 1}/{RETRIES})", flush=True)
                time.sleep(sleep)
                continue

            raise SystemExit(f"\n  Request failed: HTTP {e.code}\n  {body}\n")

        except urllib.error.URLError as e:
            last_err = str(e.reason)
            sleep = BACKOFF * (2 ** attempt)
            print(f"      Network error ({last_err}), retrying in {sleep:.0f}s "
                  f"({attempt + 1}/{RETRIES})", flush=True)
            time.sleep(sleep)

        except json.JSONDecodeError as e:
            raise SystemExit(f"\n  Server returned something that isn't JSON: {e}\n")

    raise SystemExit(f"\n  Gave up after {RETRIES} attempts. Last error: {last_err}\n")


def unwrap(payload, resource):
    """
    Stocky may return either a bare list or an object wrapping the list.
    Find the list of records regardless of shape.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # Most likely: {"suppliers": [...]} or {"purchase_orders": [...]}
        if resource in payload and isinstance(payload[resource], list):
            return payload[resource]
        # Fall back to the first list-of-dicts value we find.
        for value in payload.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return value
        # A single record returned bare.
        return [payload]
    return []


def fetch_all(resource, store, key):
    """Page through a resource until it stops returning new records."""
    records = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        url = f"{API_BASE}/{resource}.json?page={page}&limit={PAGE_LIMIT}"
        payload = fetch(url, store, key)
        batch = unwrap(payload, resource)

        if not batch:
            break

        # Guard against servers that ignore ?page and hand back page 1 forever.
        new = []
        for rec in batch:
            rid = rec.get("id") if isinstance(rec, dict) else None
            if rid is not None:
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
            new.append(rec)

        if not new:
            break

        records.extend(new)
        print(f"      page {page}: {len(new)} records (total {len(records)})", flush=True)

        if len(batch) < PAGE_LIMIT:
            break

        time.sleep(THROTTLE)

    return records


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------

def flatten(obj, prefix="", out=None):
    """
    Turn nested JSON into flat dotted keys so it fits in a CSV.
    Lists of objects are left as JSON strings; they get their own CSV instead.
    """
    if out is None:
        out = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                flatten(v, key, out)
            elif isinstance(v, list):
                if v and isinstance(v[0], dict):
                    out[key] = json.dumps(v, ensure_ascii=False)
                else:
                    out[key] = "; ".join("" if i is None else str(i) for i in v)
            else:
                out[key] = "" if v is None else v
    else:
        out[prefix or "value"] = obj

    return out


def write_csv(path, rows):
    """Write rows to CSV using the union of all keys, so nothing is dropped."""
    if not rows:
        return 0

    flat = [flatten(r) for r in rows]

    fields = []
    seen = set()
    for row in flat:
        for k in row:
            if k not in seen:
                seen.add(k)
                fields.append(k)

    # Put the useful identifying columns first if present.
    preferred = ["id", "name", "number", "created_at", "updated_at"]
    fields.sort(key=lambda f: (preferred.index(f) if f in preferred else len(preferred), ))

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in flat:
            w.writerow(row)

    return len(flat)


def find_line_items(record):
    """
    Locate the line-item list on a purchase order. The exact key name is not
    documented, so accept any plausible list of dicts.
    """
    if not isinstance(record, dict):
        return []

    candidates = [
        "line_items", "purchase_order_line_items", "items",
        "purchase_order_items", "order_line_items", "lines",
    ]
    for key in candidates:
        val = record.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val

    # Last resort: any list of dicts on the record.
    for key, val in record.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            if any(hint in key.lower() for hint in ("item", "line", "product", "variant")):
                return val

    return []


def pick(d, *names, default=""):
    """Return the first present, non-empty value among candidate key names."""
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return default


def build_cost_history(purchase_orders, suppliers):
    """
    The file nobody else produces: one row per PO line item, joined to the
    supplier and the dates. This is your per-supplier cost history and lead
    times — it does not survive the shutdown in any other form.
    """
    supplier_by_id = {}
    for s in suppliers:
        if isinstance(s, dict) and s.get("id") is not None:
            supplier_by_id[str(s["id"])] = s

    rows = []
    for po in purchase_orders:
        if not isinstance(po, dict):
            continue

        sid = pick(po, "supplier_id", "vendor_id", default=None)
        supplier = supplier_by_id.get(str(sid), {}) if sid is not None else {}
        if not supplier and isinstance(po.get("supplier"), dict):
            supplier = po["supplier"]

        po_common = {
            "po_id": pick(po, "id"),
            "po_number": pick(po, "number", "po_number", "name", "reference"),
            "po_status": pick(po, "status", "state"),
            "supplier_id": sid if sid is not None else "",
            "supplier_name": pick(supplier, "name", "company_name", "title"),
            "supplier_email": pick(supplier, "email", "contact_email"),
            "supplier_notes": pick(supplier, "note", "notes", "description", "comment"),
            "ordered_at": pick(po, "ordered_at", "order_date", "created_at", "placed_at"),
            "expected_at": pick(po, "expected_at", "expected_date", "eta", "due_at"),
            "received_at": pick(po, "received_at", "received_date",
                                "completed_at", "landed_at", "closed_at"),
            "currency": pick(po, "currency", "currency_code"),
            "po_total": pick(po, "total", "total_cost", "grand_total", "amount"),
        }

        items = find_line_items(po)
        if not items:
            rows.append({**po_common, "sku": "", "barcode": "", "product_title": "",
                         "variant_title": "", "quantity_ordered": "",
                         "quantity_received": "", "unit_cost": "", "line_total": ""})
            continue

        for it in items:
            rows.append({
                **po_common,
                "sku": pick(it, "sku", "variant_sku", "product_sku"),
                "barcode": pick(it, "barcode", "variant_barcode"),
                "product_title": pick(it, "product_title", "title", "product_name", "name"),
                "variant_title": pick(it, "variant_title", "variant_name", "variant"),
                "quantity_ordered": pick(it, "quantity", "quantity_ordered",
                                         "ordered_quantity", "qty"),
                "quantity_received": pick(it, "quantity_received", "received_quantity",
                                          "received", "qty_received"),
                "unit_cost": pick(it, "cost", "unit_cost", "cost_price",
                                  "price", "unit_price"),
                "line_total": pick(it, "total", "line_total", "total_cost", "subtotal"),
            })

    return rows


def write_named_csv(path, rows):
    if not rows:
        return 0
    fields = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_dotenv(path):
    """Minimal .env reader. No dependency on python-dotenv."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def resolve_config(args):
    env_file = load_dotenv(Path(__file__).resolve().parent / ".env")

    store = (args.store
             or os.environ.get("STOCKY_STORE")
             or env_file.get("STOCKY_STORE", ""))
    key = (args.key
           or os.environ.get("STOCKY_API_KEY")
           or env_file.get("STOCKY_API_KEY", ""))

    store = store.strip().replace("https://", "").replace("http://", "").rstrip("/")
    key = key.strip()

    if not store or not key:
        raise SystemExit(
            "\n  Missing credentials.\n\n"
            "  Either pass them on the command line:\n"
            "    python3 stocky_export.py --store mystore.myshopify.com --key YOUR_KEY\n\n"
            "  Or create a file called .env next to this script containing:\n"
            "    STOCKY_STORE=mystore.myshopify.com\n"
            "    STOCKY_API_KEY=your_api_key_here\n\n"
            "  Your API key is in Stocky under Preferences > API Access.\n"
            "  If that section is missing, email Shopify Support and ask them to\n"
            "  'activate Stocky API access'.\n"
        )

    if not store.endswith(".myshopify.com"):
        print(f"  Warning: '{store}' doesn't look like a myshopify.com domain. "
              f"Continuing anyway.\n")

    return store, key


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Export all Stocky data to JSON + CSV before the 31 Aug 2026 shutdown.")
    p.add_argument("--store", help="yourstore.myshopify.com")
    p.add_argument("--key", help="Stocky API key (Preferences > API Access)")
    p.add_argument("--out", default="stocky-export", help="output folder")
    p.add_argument("--test", action="store_true",
                   help="just check the credentials work, fetch nothing")
    args = p.parse_args()

    store, key = resolve_config(args)

    print()
    print("  Stocky Export")
    print("  " + "-" * 58)
    print(f"  Store : {store}")
    print(f"  Key   : {key[:4]}{'*' * max(0, len(key) - 8)}{key[-4:] if len(key) > 8 else ''}")
    print()

    if args.test:
        print("  Testing connection...")
        fetch(f"{API_BASE}/suppliers.json?page=1&limit=1", store, key)
        print("  Credentials work. Run again without --test to export everything.\n")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    outdir = Path(args.out) / f"{store.split('.')[0]}-{stamp}"
    (outdir / "raw-json").mkdir(parents=True, exist_ok=True)

    collected = {}
    summary = []

    for resource in ENDPOINTS:
        print(f"  Fetching {resource} ...", flush=True)
        try:
            records = fetch_all(resource, store, key)
        except SystemExit:
            raise
        except Exception as e:
            print(f"      Skipped ({e})")
            summary.append((resource, "error", 0))
            continue

        collected[resource] = records

        if not records:
            print("      none found")
            summary.append((resource, "empty", 0))
            continue

        # Raw JSON first. If the CSV flattening ever misses a field,
        # the complete original payload is still on disk.
        raw_path = outdir / "raw-json" / f"{resource}.json"
        raw_path.write_text(json.dumps(records, indent=2, ensure_ascii=False),
                            encoding="utf-8")

        n = write_csv(outdir / f"{resource}.csv", records)
        print(f"      saved {n} rows -> {resource}.csv")
        summary.append((resource, "ok", n))
        time.sleep(THROTTLE)

    # The join that matters: per-supplier cost history and lead times.
    pos = collected.get("purchase_orders", [])
    sups = collected.get("suppliers", [])
    if pos:
        rows = build_cost_history(pos, sups)
        n = write_named_csv(outdir / "supplier_cost_history.csv", rows)
        print(f"\n  Built supplier_cost_history.csv ({n} line items)")
        summary.append(("supplier_cost_history", "derived", n))

    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "store": store,
        "api_base": API_BASE,
        "results": [{"resource": r, "status": s, "rows": n} for r, s, n in summary],
        "note": ("Stocky shuts down 31 August 2026. Raw JSON in raw-json/ is the "
                 "complete unmodified API response. CSVs are a flattened view."),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print("  " + "-" * 58)
    print(f"  Done. Everything is in: {outdir.resolve()}")
    print()
    for r, s, n in summary:
        print(f"    {r:28} {s:8} {n:>7} rows")
    print()
    print("  Keep the raw-json folder. If you later find a CSV is missing a")
    print("  field, the original API response is still there.")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Cancelled.\n")
        sys.exit(1)
