from pathlib import Path
from typing import List
import hashlib
import re
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import DATA_DIR, connect, init_db
from .extractor import extract_invoice_placeholder

BASE_DIR = Path(__file__).resolve().parent.parent
ORIGINALS_DIR = DATA_DIR / "originals"
ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LEA Scan")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def safe_filename(name: str) -> str:
    name = Path(name or "factura").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name[:180] or "factura"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with connect() as conn:
        invoices = conn.execute(
            """
            SELECT i.*,
                   (SELECT COUNT(*) FROM invoice_items x WHERE x.invoice_id=i.id) AS item_count
            FROM invoices i
            ORDER BY i.id DESC
            """
        ).fetchall()

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "invoices": invoices},
    )


@app.post("/upload")
async def upload(
    batch_name: str = Form(""),
    files: List[UploadFile] = File(...),
):
    for upload_file in files:
        data = await upload_file.read()

        if not data:
            continue

        original_name = safe_filename(upload_file.filename or "factura")
        digest = hashlib.sha256(data).hexdigest()
        suffix = Path(original_name).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        stored_path = ORIGINALS_DIR / stored_name
        stored_path.write_bytes(data)

        extracted = extract_invoice_placeholder(stored_path)

        with connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO invoices (
                    batch_name,
                    original_filename,
                    stored_filename,
                    sha256,
                    mime_type,
                    file_size,
                    supplier,
                    supplier_tax_id,
                    invoice_number,
                    invoice_date,
                    currency,
                    net_amount,
                    tax_amount,
                    total_amount
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_name.strip(),
                    original_name,
                    stored_name,
                    digest,
                    upload_file.content_type or "",
                    len(data),
                    extracted.get("supplier", ""),
                    extracted.get("supplier_tax_id", ""),
                    extracted.get("invoice_number", ""),
                    extracted.get("invoice_date", ""),
                    extracted.get("currency", "ARS"),
                    float(extracted.get("net_amount", 0) or 0),
                    float(extracted.get("tax_amount", 0) or 0),
                    float(extracted.get("total_amount", 0) or 0),
                ),
            )

            invoice_id = cursor.lastrowid

            for item in extracted.get("items", []):
                conn.execute(
                    """
                    INSERT INTO invoice_items (
                        invoice_id,
                        item_code,
                        description,
                        quantity,
                        unit_price,
                        discount,
                        tax_rate,
                        subtotal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        invoice_id,
                        item.get("item_code", ""),
                        item.get("description", ""),
                        float(item.get("quantity", 0) or 0),
                        float(item.get("unit_price", 0) or 0),
                        float(item.get("discount", 0) or 0),
                        float(item.get("tax_rate", 0) or 0),
                        float(item.get("subtotal", 0) or 0),
                    ),
                )

            conn.commit()

    return RedirectResponse("/", status_code=303)


@app.get("/invoice/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int):
    with connect() as conn:
        invoice = conn.execute(
            "SELECT * FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone()

        if not invoice:
            raise HTTPException(404, "Factura no encontrada")

        items = conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id",
            (invoice_id,),
        ).fetchall()

    return templates.TemplateResponse(
        "invoice.html",
        {
            "request": request,
            "invoice": invoice,
            "items": items,
        },
    )


@app.get("/original/{invoice_id}")
def download_original(invoice_id: int):
    with connect() as conn:
        invoice = conn.execute(
            """
            SELECT original_filename, stored_filename
            FROM invoices
            WHERE id=?
            """,
            (invoice_id,),
        ).fetchone()

    if not invoice:
        raise HTTPException(404, "Factura no encontrada")

    path = ORIGINALS_DIR / invoice["stored_filename"]

    if not path.exists():
        raise HTTPException(404, "Archivo original no encontrado")

    return FileResponse(
        str(path),
        filename=invoice["original_filename"],
        media_type="application/octet-stream",
    )
