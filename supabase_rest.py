"""Minimal PostgREST client for the additional Supabase projects.

The customer statistics upload talks to two Supabase projects that are separate
from the dashboard one. Creating a second/third `supabase.create_client()` in the
same process caused problems in the legacy upload script, and we only need a
handful of calls — so this module speaks REST directly.

Everything is batched: the legacy script issued one SELECT + one INSERT + one
UPSERT per session (~30.000 requests per run on a full mac), which is why it
regularly failed halfway through.
"""

import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

# Status codes worth retrying (rate limit / transient backend trouble)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3


class SupabaseRestError(RuntimeError):
    """A PostgREST request failed."""


class SupabaseRest:
    """Tiny PostgREST wrapper with batching and retry/backoff."""

    def __init__(self, url: str, key: str, timeout: int = DEFAULT_TIMEOUT):
        self.root = (url or "").strip().rstrip("/")
        self.base = f"{self.root}/rest/v1"
        self.key = (key or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        """True when both URL and key are set."""
        return bool(self.root and self.key)

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        payload=None,
        headers: dict | None = None,
        retries: int = MAX_RETRIES,
    ) -> requests.Response:
        """Perform a request, retrying transient failures with backoff."""
        last_error: Exception | None = None

        for attempt in range(1, retries + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._headers(headers),
                    params=params,
                    data=json.dumps(payload) if payload is not None else None,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = e
                if attempt >= retries:
                    break
                wait = 2**attempt
                logger.warning(
                    f"{method} {url} failed (attempt {attempt}/{retries}): {e}. "
                    f"Retrying in {wait}s"
                )
                time.sleep(wait)
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < retries:
                wait = 2**attempt
                logger.warning(
                    f"{method} {url} → HTTP {response.status_code} "
                    f"(attempt {attempt}/{retries}). Retrying in {wait}s"
                )
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                raise SupabaseRestError(
                    f"HTTP {response.status_code}: {response.text[:400]}"
                )

            return response

        raise SupabaseRestError(f"Request failed after {retries} attempts: {last_error}")

    # ── Schema ────────────────────────────────────────────────────────

    def columns(self, table: str) -> set[str]:
        """Discover the columns of a table.

        Tries the OpenAPI spec first (works for empty tables), then falls back to
        reading one row. Returns an empty set when neither works — callers treat
        that as "unknown schema, send everything".
        """
        try:
            response = self._request("GET", f"{self.base}/", retries=1)
            definitions = response.json().get("definitions", {})
            if table in definitions:
                cols = set(definitions[table].get("properties", {}).keys())
                if cols:
                    return cols
        except Exception as e:
            logger.debug(f"OpenAPI column discovery failed for '{table}': {e}")

        try:
            response = self._request(
                "GET", f"{self.base}/{table}", params={"select": "*", "limit": 1}, retries=1
            )
            rows = response.json()
            if rows:
                return set(rows[0].keys())
        except Exception as e:
            logger.debug(f"Row-based column discovery failed for '{table}': {e}")

        return set()

    def count(self, table: str) -> int | None:
        """Return the row count of a table, or None when unavailable."""
        try:
            response = self._request(
                "GET",
                f"{self.base}/{table}",
                params={"select": "*", "limit": 1},
                headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
                retries=1,
            )
            content_range = response.headers.get("content-range", "")
            if "/" in content_range:
                total = content_range.split("/")[-1]
                return int(total) if total.isdigit() else None
        except Exception as e:
            logger.debug(f"Count failed for '{table}': {e}")
        return None

    # ── Reads ─────────────────────────────────────────────────────────

    def select_existing(
        self, table: str, column: str, values: list[str], chunk_size: int = 100
    ) -> set[str]:
        """Return the subset of `values` that already exists in `table.column`.

        Replaces the legacy script's one-SELECT-per-session duplicate check.
        """
        found: set[str] = set()
        for i in range(0, len(values), chunk_size):
            chunk = [v for v in values[i : i + chunk_size] if v]
            if not chunk:
                continue
            quoted = ",".join('"' + str(v).replace('"', '\\"') + '"' for v in chunk)
            response = self._request(
                "GET",
                f"{self.base}/{table}",
                params={"select": column, column: f"in.({quoted})"},
            )
            for row in response.json():
                if row.get(column) is not None:
                    found.add(str(row[column]))
        return found

    def select(self, table: str, params: dict) -> list[dict]:
        """Run a raw select with PostgREST query parameters."""
        response = self._request("GET", f"{self.base}/{table}", params=params)
        return response.json()

    # ── Writes ────────────────────────────────────────────────────────

    def _write_batch(
        self, table: str, rows: list[dict], params: dict, prefer: str
    ) -> None:
        self._request(
            "POST",
            f"{self.base}/{table}",
            params=params,
            payload=rows,
            headers={"Prefer": prefer},
        )

    def _write(
        self, table: str, rows: list[dict], params: dict, prefer: str, batch_size: int
    ) -> dict:
        """Write rows in batches; fall back to single rows when a batch fails.

        A failing batch usually means one bad row (a value that violates a
        constraint). Retrying row by row keeps the remaining rows of that batch
        instead of losing all of them.
        """
        written = 0
        errors: list[str] = []

        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            try:
                self._write_batch(table, batch, params, prefer)
                written += len(batch)
                continue
            except Exception as e:
                logger.warning(
                    f"Batch write to '{table}' failed ({len(batch)} rows), "
                    f"retrying row by row: {e}"
                )

            for row in batch:
                try:
                    self._write_batch(table, [row], params, prefer)
                    written += 1
                except Exception as e:
                    message = str(e)[:200]
                    errors.append(message)
                    logger.error(f"Row write to '{table}' failed: {message}")

        return {"written": written, "failed": len(errors), "errors": errors[:10]}

    def insert_many(self, table: str, rows: list[dict], batch_size: int = 100) -> dict:
        """Plain INSERT (no conflict handling)."""
        if not rows:
            return {"written": 0, "failed": 0, "errors": []}
        return self._write(table, rows, {}, "return=minimal", batch_size)

    def upsert_many(
        self, table: str, rows: list[dict], on_conflict: str, batch_size: int = 200
    ) -> dict:
        """UPSERT with conflict resolution on `on_conflict`."""
        if not rows:
            return {"written": 0, "failed": 0, "errors": []}
        return self._write(
            table,
            rows,
            {"on_conflict": on_conflict},
            "resolution=merge-duplicates,return=minimal",
            batch_size,
        )


# ── Connection test ───────────────────────────────────────────────────

# Adding columns needs DDL, which PostgREST cannot do with any key. So instead of
# creating them we report what is missing and hand out ready-made SQL.
MAX_SQL_COLUMNS = 40


def build_alter_sql(table: str, columns: dict[str, str]) -> str:
    """Build an ALTER TABLE statement for the missing columns."""
    if not columns:
        return ""

    items = list(columns.items())
    truncated = len(items) > MAX_SQL_COLUMNS
    items = items[:MAX_SQL_COLUMNS]

    additions = ",\n".join(
        f'  ADD COLUMN IF NOT EXISTS "{name}" {sql_type}' for name, sql_type in items
    )
    statement = f'ALTER TABLE "{table}"\n{additions};'
    if truncated:
        statement += f"\n-- gekürzt: nur die ersten {MAX_SQL_COLUMNS} Spalten"
    return statement


def describe_target(
    url: str,
    key: str,
    table: str,
    expected: dict[str, str] | None = None,
    unique_column: str | None = None,
) -> dict:
    """Check whether a target table is reachable and which columns are missing."""
    if not table:
        return {"status": "error", "error": "Kein Tabellenname gesetzt"}

    api = SupabaseRest(url, key)
    if not api.configured:
        return {
            "status": "not_configured",
            "table": table,
            "error": "URL oder Key fehlt",
        }

    columns = api.columns(table)
    if not columns:
        return {
            "status": "error",
            "table": table,
            "error": (
                f"Tabelle '{table}' nicht erreichbar — URL, Key und Tabellenname prüfen"
            ),
        }

    result = {
        "status": "ok",
        "table": table,
        "columns": len(columns),
        "rows": api.count(table),
        "missing": [],
    }

    if expected:
        missing = {name: t for name, t in expected.items() if name not in columns}
        result["missing"] = list(missing)
        if missing:
            result["status"] = "incomplete"
            result["sql"] = build_alter_sql(table, missing)

    if unique_column:
        result["hint"] = (
            f"Für das UPSERT braucht '{table}' einen UNIQUE-Index auf "
            f"'{unique_column}':\nCREATE UNIQUE INDEX IF NOT EXISTS "
            f'{table}_{unique_column}_key ON "{table}" ("{unique_column}");'
        )

    return result
