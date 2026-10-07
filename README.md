# FoodConnect – Customer Portal

Flask app: menu → cart → order CSV → sent to the restaurant API → result CSV received and shown.

## Run locally
    pip install -r requirements.txt
    python app.py                       # http://127.0.0.1:5000
Expects the restaurant portal on http://127.0.0.1:5001 (override with `RESTAURANT_API_URL`).

## Environment variables
| Name | Purpose |
|---|---|
| `RESTAURANT_API_URL` | Base URL of the restaurant portal, e.g. `https://foodconnect-restaurant.onrender.com` |
| `API_KEY` | Shared secret sent as `X-API-Key`; must match the restaurant portal |

## Deploy on Render (free)
1. Push this folder to its own GitHub repo.
2. Render → New → Web Service → pick the repo. Runtime: Python, Instance type: Free.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --workers 1 --timeout 120`
5. Add the two environment variables above, then deploy.

## API
- `POST /api/orders/create` – JSON `{restaurant_id, items:[{item, quantity}]}` → creates order + CSV
- `POST /api/orders/upload` – JSON `{order_id}` → sends the CSV to the restaurant's `/api/orders/upload`
- `GET /api/orders/<id>` · `GET /api/orders/<id>/input` · `GET /api/orders/<id>/result` (`?download=1`)
- `POST /api/orders/result` – receives the restaurant's result CSV (multipart `file`, header `X-API-Key`)

Free-tier note: the filesystem is wiped on restart/redeploy/idle spin-down, so run the whole demo in one sitting.
