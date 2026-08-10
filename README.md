# stocky-export

Saves your Stocky data to CSV before Shopify shuts it down on **31 August 2026**.

Free. Read-only. Nothing to install.

## Why

Shopify's migration guide says suppliers **can't** be exported and old purchase orders **can't** be imported into native inventory. So your supplier notes, cost history, and received dates are lost on 31 August.

This gets them out.

## Step 1 — Get your API key

In Stocky: **Preferences → API Access**. Copy the key.

**No "API Access" section?** Email Shopify Support (Store Management → Retail and Shopify POS → Email Us) and ask them to *"activate Stocky API access."* Takes a day or two — do it now.

## Step 2 — Run it

You need Python. Mac already has it. [Windows: install here](https://www.python.org/downloads/), tick "Add Python to PATH".

Download this repo (green **Code** button → **Download ZIP**), unzip, open a terminal in that folder, and run:

```bash
python3 stocky_export.py --store yourstore.myshopify.com --key YOUR_API_KEY
```

Windows: use `python` instead of `python3`.

That's it. Your files appear in a `stocky-export` folder.

## What you get

| File | What's in it |
|---|---|
| `suppliers.csv` | Suppliers, contacts, notes |
| `purchase_orders.csv` | All your POs |
| `supplier_cost_history.csv` | Every PO line item with supplier, cost, ordered + received dates |
| `stock_adjustments.csv` | Adjustments |
| `raw-json/` | Untouched originals, in case a CSV misses something |

## Is it safe?

Only reads — it can't change or delete anything. Runs on your computer, nothing gets uploaded, your key never leaves your machine. One file, no dependencies, read it yourself.

Don't share your `.env` file or export folder — they contain your key and supplier pricing.

## Problems?

**Authentication failed** — check the store name is the full `yourstore.myshopify.com`, and that API access is activated.

**python3 not found** — try `python`.

**"none found"** — that section is empty on your account. Normal.

**Missing a column** — check `raw-json/`, then open an issue.

## Not included

Stocktakes and forecast settings aren't in Stocky's API. Export those by hand before the 31st.

## Did this help?

I'm building a replacement for Stocky's reorder and receiving side. If you used Stocky, I'd love 15 minutes to hear how. Nothing to sell.

**stocky@tappacific.com**

---

MIT licensed. Not affiliated with Shopify.
