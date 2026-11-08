# Pricewise

A simple, mobile-friendly price tracker for Indian shopping sites. This first version includes an Amazon.in product reader, a SQLite watchlist, six-hour price checks, price history charts, in-app target alerts, and sample products so the dashboard is useful immediately.

## Run it

1. Install Python 3.10 or newer.
2. In this folder, create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows, activate with `.venv\Scripts\activate`.

3. Install packages and start the app:

   ```bash
   pip install -r requirements.txt
   uvicorn app.main:app --reload
   ```

4. Open <http://127.0.0.1:8000>.

The database is created as `price_tracker.db` the first time the server starts. The first launch seeds three demo products and a sample cheaper alternative. Deleting the database resets this sample dashboard.

## What works

- Paste an Amazon.in product page URL to add it. The app reads the title, image, and common page price fields.
- Product prices are refreshed in the background every six hours; reloads and dashboard visits do not trigger retailer requests.
- The dashboard shows saved price snapshots, a compact history chart, high/low prices, change since the first snapshot, and an in-app banner when a target is reached.
- Demo items use fictional/sample values for the display and are marked DEMO.

## Retailer access and limitations

Amazon does not provide a generally available public product price API for this personal use case. The Amazon module therefore reads the public product page as a best-effort fallback. Amazon may block automated requests, change page markup, omit prices, or show an unavailable/out-of-stock page. The app stores the last known price and shows the latest check status when a refresh fails. It waits between retailer requests and caches a fetched page for 15 minutes. Please use it for a small personal watchlist and follow retailer terms.

Cheaper alternatives are represented in the app and seeded with one clearly illustrative demo match. Live alternative discovery is not enabled yet: retailers do not share one open Indian product-search API, and reliable matching usually needs an approved comparison API or retailer-specific integrations. Any future live result should carry a confidence note because title matches can refer to different variants.

Flipkart, Myntra, Croma, Reliance Digital, Tata CLiQ, Ajio, and Nykaa are not fetched yet. Amazon is isolated in `app/scrapers/amazon.py`, ready for additional per-site modules. Alerts are in-app only; no email service or address is configured.
