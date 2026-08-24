from pathlib import Path
import re

from pypdf import PdfReader


def _clean_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value or "").strip()


def _parse_amount(value: str) -> float:
    value = re.sub(r"[^\d,.\-]", "", value or "")
    if not value:
        return 0.0

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        parts = value.split(",")
        if len(parts[-1]) == 2:
            value = "".join(parts[:-1]).replace(".", "") + "." + parts[-1]
        else:
            value = value.replace(",", "")
    elif value.count(".") > 1:
        parts = value.split(".")
        value = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(value)
    except ValueError:
        return 0.0


def _read_pdf_text(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    pages = []

    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            pages.append(text)

    return "\n".join(pages)


def _find_first(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return _clean_text(match.group(1))
    return ""


def _extract_supplier(lines: list[str]) -> str:
    ignored = (
        "factura",
        "comprobante",
        "original",
        "duplicado",
        "triplicado",
        "cuit",
        "ingresos brutos",
        "inicio de actividades",
        "fecha",
        "punto de venta",
        "responsable",
        "iva",
    )

    for line in lines[:25]:
        candidate = _clean_text(line)
        if len(candidate) < 3:
            continue
        lower = candidate.lower()
        if any(word in lower for word in ignored):
            continue
        if re.fullmatch(r"[\d\s./\-:$]+", candidate):
            continue
        return candidate[:160]

    return ""


def _extract_items(lines: list[str]) -> list[dict]:
    items = []

    for line in lines:
        clean = _clean_text(line)
        if len(clean) < 8:
            continue

        match = re.match(
            r"^(?:(\w[\w./-]*)\s+)?(.+?)\s+"
            r"(\d+(?:[.,]\d+)?)\s+"
            r"\$?\s*([\d.,]+)\s+"
            r"\$?\s*([\d.,]+)$",
            clean,
        )

        if not match:
            continue

        code, description, quantity, unit_price, subtotal = match.groups()

        if any(
            word in description.lower()
            for word in ("subtotal", "total", "iva", "neto", "impuesto")
        ):
            continue

        qty = _parse_amount(quantity)
        price = _parse_amount(unit_price)
        sub = _parse_amount(subtotal)

        if qty <= 0 or sub <= 0:
            continue

        items.append(
            {
                "item_code": code or "",
                "description": description[:240],
                "quantity": qty,
                "unit_price": price,
                "discount": 0.0,
                "tax_rate": 0.0,
                "subtotal": sub,
            }
        )

    return items


def extract_invoice_placeholder(file_path: Path) -> dict:
    text = ""

    if file_path.suffix.lower() == ".pdf":
        try:
            text = _read_pdf_text(file_path)
        except Exception:
            text = ""

    text = text.replace("\r", "\n")
    lines = [_clean_text(line) for line in text.splitlines() if _clean_text(line)]

    supplier_tax_id = _find_first(
        [
            r"(?:CUIT|CUIL)\s*[:\-]?\s*(\d{2}[-\s]?\d{8}[-\s]?\d)",
            r"\b(\d{2}-\d{8}-\d)\b",
            r"(?:NIF|CIF)\s*[:\-]?\s*([A-Z0-9\-]{7,15})",
        ],
        text,
    )

    invoice_number = _find_first(
        [
            r"(?:Comp\.?\s*Nro\.?|Comprobante\s*Nro\.?|Factura\s*Nro\.?|Nro\.?\s*Factura)\s*[:#\-]?\s*([A-Z]?\s*\d{3,5}[-\s]\d{6,10})",
            r"(?:Comp\.?\s*Nro\.?|Comprobante\s*Nro\.?|Factura\s*Nro\.?|Nro\.?\s*Factura)\s*[:#\-]?\s*([A-Z0-9\-]{4,20})",
            r"\b([A-Z]\s+\d{4,5}-\d{6,10})\b",
            r"\b(\d{4,5}-\d{6,10})\b",
        ],
        text,
    )

    invoice_date = _find_first(
        [
            r"(?:Fecha\s+de\s+Emisi[oó]n|Fecha\s+Emisi[oó]n|Fecha)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b",
        ],
        text,
    )

    net_value = _find_first(
        [
            r"(?:Importe\s+Neto\s+Gravado|Neto\s+Gravado|Importe\s+Neto|Subtotal)\s*[:$]?\s*\$?\s*([\d.,]+)",
        ],
        text,
    )

    tax_value = _find_first(
        [
            r"(?:IVA(?:\s+\d+(?:[.,]\d+)?\s*%)?|Importe\s+IVA|Total\s+IVA)\s*[:$]?\s*\$?\s*([\d.,]+)",
        ],
        text,
    )

    total_value = _find_first(
        [
            r"(?:Importe\s+Total|Total\s+a\s+Pagar|TOTAL)\s*[:$]?\s*\$?\s*([\d.,]+)",
        ],
        text,
    )

    upper_text = text.upper()
    if "USD" in upper_text or "U$S" in upper_text or "DOLAR" in upper_text or "DÓLAR" in upper_text:
        currency = "USD"
    elif "EUR" in upper_text or "€" in text:
        currency = "EUR"
    else:
        currency = "ARS"

    return {
        "supplier": _extract_supplier(lines),
        "supplier_tax_id": supplier_tax_id,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "currency": currency,
        "net_amount": _parse_amount(net_value),
        "tax_amount": _parse_amount(tax_value),
        "total_amount": _parse_amount(total_value),
        "items": _extract_items(lines),
    }
