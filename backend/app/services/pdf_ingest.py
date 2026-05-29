import re
import unicodedata
from io import BytesIO

from pypdf import PdfReader

from app.config import settings
from app.services.rag_sources import dedupe_near_identical_chunks


def extract_text_from_pdf(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        parts.append(t)
    return "\n\n".join(parts).strip()


def clean_pdf_text(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = re.sub(r"\r\n?", "\n", t)
    # Guiones de salto de línea típicos de PDF
    t = re.sub(r"-\s*\n\s*", "", t)
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _slide_window_chunks(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        chunk = text[start:end]
        if end < n:
            last_sentence = max(chunk.rfind("."), chunk.rfind("!"), chunk.rfind("?"))
            if last_sentence > size // 2:
                end = start + last_sentence + 1
                chunk = text[start:end]
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(0, end - overlap)
    return chunks


def chunk_text(text: str) -> list[str]:
    """
    Normaliza texto, agrupa párrafos pequeños y divide bloques grandes en ventanas con solapamiento.
    """
    text = clean_pdf_text(text)
    if not text:
        return []
    size = settings.chunk_size
    overlap = settings.chunk_overlap
    raw_paras = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not raw_paras:
        return dedupe_near_identical_chunks(_slide_window_chunks(text, size, overlap))

    chunks: list[str] = []
    buffer = ""

    def flush_buffer() -> None:
        nonlocal buffer
        if buffer:
            part = re.sub(r"\s+", " ", buffer).strip()
            if len(part) <= size:
                chunks.append(part)
            else:
                chunks.extend(_slide_window_chunks(part, size, overlap))
            buffer = ""

    for p in raw_paras:
        plen = len(p)
        if plen > size * 2:
            flush_buffer()
            chunks.extend(_slide_window_chunks(p, size, overlap))
            continue
        candidate = buffer + ("\n\n" if buffer else "") + p
        if len(candidate) <= size:
            buffer = candidate
        else:
            flush_buffer()
            if plen <= size:
                buffer = p
            else:
                chunks.extend(_slide_window_chunks(p, size, overlap))
    flush_buffer()
    return dedupe_near_identical_chunks(chunks)
