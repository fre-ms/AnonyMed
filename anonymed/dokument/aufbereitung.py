"""Image clean-up before OCR: straighten, unwarp, upscale, even out the background.

Scans arrive tilted, photos are taken at an angle under uneven light, and both
are often small.  Every step here is optional, has an automatic suggestion
and is meant to be checked by eye in the desktop's "Aufbereiten" step.  The
steps work on a rendered page (an image); the processed image replaces the
page, so that OCR, redaction and the output all see the same corrected page.
Needs OpenCV and NumPy (they come with the ``ocr`` extra); without them only
masks are available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pymupdf

Ecke = tuple[float, float]
Rechteck = tuple[float, float, float, float]


def verfuegbar() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class Aufbereitung:
    """What to do with one page.  Angles in degrees; corners in points of the source
    page (after ``drehung``), masks as ``(x0, y0, x1, y1)`` in points of the result
    page (see :func:`seite_ersetzen`)."""

    drehung: int = 0  # 0, 90, 180, 270 clockwise, applied first
    begradigen: bool = False  # rotate by -winkel
    winkel: float = 0.0  # estimated tilt, positive = text runs downhill to the right
    entzerren: bool = False  # perspective correction with ``ecken``
    ecken: list[Ecke] | None = None  # four corners of the page in the (rotated) image
    hochskalieren: int = 1  # 1 or 2: OCR reads the page at that multiple of the resolution
    hintergrund: bool = False  # even out illumination, white paper
    schwarzweiss: bool = False  # binarise after that
    masken: list[Rechteck] = field(default_factory=list)  # black regions, result page points

    @property
    def aktiv(self) -> bool:
        return bool(self.drehung or self.begradigen or self.entzerren or self.hochskalieren > 1
                    or self.hintergrund or self.schwarzweiss or self.masken)

    def als_dict(self) -> dict[str, Any]:
        return {"drehung": self.drehung, "begradigen": self.begradigen,
                "winkel": round(self.winkel, 2), "entzerren": self.entzerren,
                "hochskalieren": self.hochskalieren, "hintergrund": self.hintergrund,
                "schwarzweiss": self.schwarzweiss, "masken": len(self.masken)}


# -- rendering -----------------------------------------------------------------------------------


def rendere(page: pymupdf.Page, dpi: int = 300, max_pixel: int = 4000) -> tuple[Any, float]:
    """The page as a BGR NumPy image and the pixels-per-point scale."""
    import numpy as np

    skala = dpi / 72.0
    laengste = max(page.rect.width, page.rect.height) * skala
    if laengste > max_pixel:
        skala *= max_pixel / laengste
    pix = page.get_pixmap(matrix=pymupdf.Matrix(skala, skala), alpha=False,
                          colorspace=pymupdf.csRGB)
    bild = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return bild[:, :, ::-1].copy(), skala


def _grau(bild: Any) -> Any:
    import cv2

    return bild if bild.ndim == 2 else cv2.cvtColor(bild, cv2.COLOR_BGR2GRAY)


def _verkleinert(grau: Any, laengste: int = 1200) -> tuple[Any, float]:
    import cv2

    h, w = grau.shape[:2]
    f = min(1.0, laengste / max(h, w))
    if f < 1.0:
        return cv2.resize(grau, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA), f
    return grau, 1.0


# -- analysis ------------------------------------------------------------------------------------


def schaetze_winkel(bild: Any, maximal: float = 10.0) -> float:
    """Tilt of the text lines in degrees (positive: the lines run downhill to the
    right).  Projection profile: the angle at which the row sums of the binarised,
    rotated image vary most is the one at which the lines are horizontal."""
    import cv2
    import numpy as np

    grau, _ = _verkleinert(_grau(bild), 800)
    _, bw = cv2.threshold(grau, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    mitte = (w / 2, h / 2)

    def guete(winkel: float) -> float:
        m = cv2.getRotationMatrix2D(mitte, winkel, 1.0)
        gedreht = cv2.warpAffine(bw, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        profil = gedreht.sum(axis=1, dtype=np.float64)
        return float(np.var(profil))

    beste, bester_wert = 0.0, -1.0
    for a in np.arange(-maximal, maximal + 0.01, 0.5):  # coarse, then fine around the best
        g = guete(float(a))
        if g > bester_wert:
            beste, bester_wert = float(a), g
    for a in np.arange(beste - 0.5, beste + 0.51, 0.1):
        g = guete(float(a))
        if g > bester_wert:
            beste, bester_wert = float(a), g
    # ``beste`` is the counter-clockwise correction; the tilt itself has the same sign
    # (content turned clockwise by 2° needs 2° counter-clockwise)
    return round(beste, 2) if abs(beste) >= 0.05 else 0.0


def finde_ecken(bild: Any, skala: float = 1.0) -> list[Ecke] | None:
    """The four corners of a sheet photographed against a background, ordered
    top-left, top-right, bottom-right, bottom-left, in points (pixels / ``skala``);
    None when no convincing quadrilateral is found (a scan that fills the image)."""
    import cv2
    import numpy as np

    grau_voll = _grau(bild)
    grau, f = _verkleinert(grau_voll, 1000)
    h, w = grau.shape
    weich = cv2.GaussianBlur(grau, (5, 5), 0)
    kanten = cv2.Canny(weich, 50, 150)
    kanten = cv2.dilate(kanten, np.ones((3, 3), np.uint8), iterations=2)
    konturen, _ = cv2.findContours(kanten, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    beste: Any = None
    beste_flaeche = 0.0
    for k in sorted(konturen, key=cv2.contourArea, reverse=True)[:10]:
        flaeche = cv2.contourArea(k)
        if flaeche < 0.2 * w * h:
            break
        umfang = cv2.arcLength(k, True)
        naeherung = cv2.approxPolyDP(k, 0.02 * umfang, True)
        if len(naeherung) == 4 and cv2.isContourConvex(naeherung) and flaeche > beste_flaeche:
            beste, beste_flaeche = naeherung.reshape(4, 2).astype(np.float64), flaeche
    if beste is None or beste_flaeche > 0.97 * w * h:
        return None  # nothing, or the whole image: no correction needed
    beste /= f * skala
    summe = beste.sum(axis=1)
    diff = np.diff(beste, axis=1).ravel()
    geordnet = [beste[np.argmin(summe)], beste[np.argmin(diff)], beste[np.argmax(summe)],
                beste[np.argmax(diff)]]
    return [(float(x), float(y)) for x, y in geordnet]


def hintergrund_ungleich(bild: Any) -> bool:
    """True when the paper is not evenly white (photo, shadow, grey scan)."""
    import cv2
    import numpy as np

    grau, _ = _verkleinert(_grau(bild), 600)
    hintergrund = cv2.medianBlur(grau, 51)
    return bool(np.std(hintergrund) > 12 or np.median(hintergrund) < 215)


def native_dpi(page: pymupdf.Page) -> float | None:
    """Resolution of the largest image on the page, or None without images."""
    beste: float | None = None
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        if bbox.width < 20 or bbox.height < 20:
            continue
        dpi = info["width"] / (bbox.width / 72.0)
        if beste is None or dpi > beste:
            beste = dpi
    return beste


def vorschlag(bild: Any, foto: bool = False, skala: float = 1.0,
              aufloesung: float | None = None) -> Aufbereitung:
    """Automatic suggestion for one page: straighten a visible tilt, unwarp a
    photographed sheet, upscale low-resolution images, even out an uneven background.
    ``skala`` is pixels per point of ``bild``, ``aufloesung`` the native dpi."""
    a = Aufbereitung()
    if foto:
        ecken = finde_ecken(bild, skala)
        if ecken is not None:
            a.ecken, a.entzerren = ecken, True
    grundlage = wende_an(bild, Aufbereitung(entzerren=a.entzerren, ecken=a.ecken), skala) \
        if a.entzerren else bild
    a.winkel = schaetze_winkel(grundlage)
    a.begradigen = abs(a.winkel) >= 0.3
    a.hochskalieren = 2 if aufloesung is not None and aufloesung < 150 else 1
    a.hintergrund = foto or hintergrund_ungleich(grundlage)
    return a


def beschreibung(a: Aufbereitung) -> str:
    """What a page clean-up does, for reports and the interface."""
    teile = []
    if a.drehung:
        teile.append(f"um {a.drehung}° gedreht")
    if a.entzerren and a.ecken:
        teile.append("entzerrt")
    if a.begradigen and abs(a.winkel) >= 0.05:
        teile.append(f"begradigt ({a.winkel:+.1f}°)".replace(".", ","))
    if a.hochskalieren > 1:
        teile.append(f"{a.hochskalieren}× hochskaliert")
    if a.hintergrund:
        teile.append("Hintergrund geglättet")
    if a.schwarzweiss:
        teile.append("Schwarz-Weiß")
    if a.masken:
        teile.append(f"{len(a.masken)} Maske(n)")
    return ", ".join(teile) if teile else "unverändert"


# -- application ---------------------------------------------------------------------------------


def _entzerre(bild: Any, ecken: list[Ecke], skala: float) -> Any:
    import cv2
    import numpy as np

    quelle = np.array(ecken, dtype=np.float32) * skala
    (ol, orr, ur, ul) = quelle
    breite = int(max(np.linalg.norm(orr - ol), np.linalg.norm(ur - ul)))
    hoehe = int(max(np.linalg.norm(ul - ol), np.linalg.norm(ur - orr)))
    if breite < 10 or hoehe < 10:
        return bild
    ziel = np.array([[0, 0], [breite - 1, 0], [breite - 1, hoehe - 1], [0, hoehe - 1]],
                    dtype=np.float32)
    m = cv2.getPerspectiveTransform(quelle, ziel)
    return cv2.warpPerspective(bild, m, (breite, hoehe), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _drehe(bild: Any, winkel: float) -> Any:
    """Rotate by ``winkel`` degrees counter-clockwise around the centre, canvas enlarged
    so that nothing is cut off, white border."""
    import cv2

    h, w = bild.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), winkel, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    weiss = (255, 255, 255) if bild.ndim == 3 else 255
    return cv2.warpAffine(bild, m, (nw, nh), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=weiss)


def _hintergrund(bild: Any) -> Any:
    """Divide by the blurred background: shadows and grey paper become white, ink stays."""
    import cv2
    import numpy as np

    grau = _grau(bild)
    k = max(31, (min(grau.shape) // 20) | 1)
    hinter = cv2.medianBlur(grau, k)
    hinter = np.maximum(hinter, 1)
    norm = cv2.divide(grau.astype(np.float32), hinter.astype(np.float32), scale=255.0)
    return np.clip(norm, 0, 255).astype(np.uint8)


def wende_an(bild: Any, a: Aufbereitung, skala: float = 1.0) -> Any:
    """Apply the steps in their fixed order: rotate, unwarp, straighten, upscale,
    background, black-and-white, masks.  ``skala`` is pixels per point of ``bild``
    (corners) and of the result (masks)."""
    import cv2
    import numpy as np

    aus = bild
    if a.drehung in (90, 180, 270):
        aus = cv2.rotate(aus, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                               270: cv2.ROTATE_90_COUNTERCLOCKWISE}[a.drehung])
    if a.entzerren and a.ecken and len(a.ecken) == 4:
        aus = _entzerre(aus, a.ecken, skala)
    if a.begradigen and abs(a.winkel) >= 0.05:
        aus = _drehe(aus, a.winkel)  # counter-clockwise by the tilt
    if a.hochskalieren > 1:
        aus = cv2.resize(aus, None, fx=a.hochskalieren, fy=a.hochskalieren,
                         interpolation=cv2.INTER_CUBIC)
    if a.hintergrund:
        aus = _hintergrund(aus)
    if a.schwarzweiss:
        grau = _grau(aus)
        aus = cv2.adaptiveThreshold(grau, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 31, 15)
    if a.masken:
        if aus.ndim == 2:
            aus = cv2.cvtColor(aus, cv2.COLOR_GRAY2BGR)
        else:
            aus = aus.copy()
        for x0, y0, x1, y1 in a.masken:
            cv2.rectangle(aus, (int(x0 * skala), int(y0 * skala)), (int(x1 * skala),
                          int(y1 * skala)), (0, 0, 0), thickness=-1)
    return np.ascontiguousarray(aus)


def als_png(bild: Any) -> bytes:
    import cv2

    ok, puffer = cv2.imencode(".png", bild)
    if not ok:
        raise ValueError("PNG-Kodierung fehlgeschlagen.")
    return bytes(puffer)


def _verarbeite(page: pymupdf.Page, a: Aufbereitung, dpi: int,
                mit_hochskalieren: bool = True) -> tuple[Any, float, float]:
    """Processed image of ``page``, its width in points and its pixels per point."""
    bild, skala = rendere(page, dpi)
    schritte = Aufbereitung(**{**a.__dict__, "masken": [],
                               "hochskalieren": a.hochskalieren if mit_hochskalieren else 1})
    aus = wende_an(bild, schritte, skala)
    breite = page.rect.width if a.drehung in (0, 180) else page.rect.height
    neue_skala = aus.shape[1] / breite
    if a.masken:
        aus = wende_an(aus, Aufbereitung(masken=a.masken), neue_skala)
    return aus, breite, neue_skala


def seiten_ersetzen(docs: list[pymupdf.Document], nummer: int, a: Aufbereitung,
                    dpi: int = 300) -> float:
    """Replace page ``nummer`` in every document of ``docs`` (copies of the same file)
    by its processed image, rendered once from the first.  The page keeps its width
    in points; the height follows the image.  Returns the pixels per point of the
    new page."""
    # upscaling is not baked into the page (no information gained, the file would bloat);
    # the pipeline renders such pages for OCR at the higher resolution instead
    aus, breite, neue_skala = _verarbeite(docs[0][nummer], a, dpi, mit_hochskalieren=False)
    png = als_png(aus)
    hoehe = aus.shape[0] / neue_skala
    for doc in docs:
        neu = doc.new_page(pno=nummer, width=breite, height=hoehe)
        neu.insert_image(neu.rect, stream=png)
        doc.delete_page(nummer + 1)
    return neue_skala


def seite_ersetzen(doc: pymupdf.Document, nummer: int, a: Aufbereitung, dpi: int = 300) -> float:
    return seiten_ersetzen([doc], nummer, a, dpi)


def vorschau_png(page: pymupdf.Page, a: Aufbereitung, dpi: int = 100) -> bytes:
    """The processed page as PNG for the before/after view, masks included."""
    aus, _breite, _skala = _verarbeite(page, a, dpi)
    return als_png(aus)


def winkel_grad(ecken: list[Ecke]) -> float:
    """Tilt of the top edge of a quadrilateral, for display."""
    (x0, y0), (x1, y1) = ecken[0], ecken[1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0))
