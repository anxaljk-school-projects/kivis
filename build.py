#!/usr/bin/env python3
"""Scan resources/ and update index.html.

- Every folder in resources/ is a topic. Subfolders of a topic are its exercises;
  a topic without subfolders is a single exercise.
- Files named v1…, v2…, final… are versions ("v1_Seite2-3.heic" → V1 · Seite 2–3).
  Exercises whose versions consist of several "Seite…" files are shown as a booklet.
- HEIC photos get a JPEG preview and every PDF page is rendered to a JPEG in
  previews/ (browsers can't show HEIC, and the pages are shown on the site).
  Previews of renamed or deleted files are removed. Needs macOS (sips, PDFKit).
- Texts come from a notizen.txt in each folder:

      Datum: 24.09.2026
      Titel: Optional, otherwise the folder name is used

      What I've learnt – each line becomes a paragraph.

  A topic's notizen.txt applies to all its exercises that have none of their own.
  Exercises without any notes get an empty notizen.txt to fill in.

Run:  python3 build.py
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
RES = "resources"
PREV = "previews"
INDEX = os.path.join(ROOT, "index.html")
MAX_PX = 2400

IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"}
CONVERT = {".heic", ".heif", ".tif", ".tiff"}
VIDEO = {".mp4", ".mov", ".webm", ".m4v"}
TEXT = {".txt", ".md"}
NOTES = "notizen.txt"
NOTES_TEMPLATE = "Datum: \n\n"

# Renders every page of a PDF to <prefix>1.jpg, <prefix>2.jpg, … with PDFKit.
PDF_JXA = r"""
ObjC.import('PDFKit'); ObjC.import('AppKit');
function run(argv) {
  const [src, prefix, maxPx] = argv;
  const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(src));
  const n = doc.pageCount, max = Number(maxPx);
  for (let i = 0; i < n; i++) {
    const page = doc.pageAtIndex(i);
    const b = page.boundsForBox(1).size;
    const turned = page.rotation % 180 !== 0;
    const w = turned ? b.height : b.width, h = turned ? b.width : b.height;
    const k = max / Math.max(w, h);
    const img = page.thumbnailOfSizeForBox($.NSMakeSize(Math.round(w * k), Math.round(h * k)), 1);
    const rep = $.NSBitmapImageRep.imageRepWithData(img.TIFFRepresentation);
    const data = rep.representationUsingTypeProperties($.NSBitmapImageFileTypeJPEG, $({ NSImageCompressionFactor: 0.82 }));
    data.writeToFileAtomically(prefix + (i + 1) + '.jpg', true);
  }
  return String(n);
}
"""


def nfc(s):
    return unicodedata.normalize("NFC", s)


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def listdir(path):
    return sorted((e for e in os.listdir(path) if not e.startswith(".")), key=natural)


def pretty(name):
    """'03_Schriftvergleich' -> 'Schriftvergleich', 'Seite6-7' -> 'Seite 6–7'."""
    name = re.sub(r"^\d+[_\- ]+", "", name)
    name = re.sub(r"[_]+", " ", name)
    name = re.sub(r"([A-Za-zÄÖÜäöü])(\d)", r"\1 \2", name)
    name = re.sub(r"(\d)-(\d)", "\\1–\\2", name)
    return name.strip()


def version(stem):
    """Return (sort key, version key, label, booklet pages or None) for a file stem."""
    m = re.match(r"^(final|v(\d+))(?:[_\- ]+(.*))?$", stem, re.I)
    if not m:
        label = pretty(stem.replace("-", " "))
        return (1, 0, natural(stem)), stem.lower(), label[:1].upper() + label[1:], None
    rest = m.group(3) or ""
    page = pretty(rest)
    if m.group(2):
        key, label, rank = "v" + m.group(2), "V" + m.group(2), (0, int(m.group(2)))
    else:
        key, label, rank = "final", "Final", (2, 0)
    s = re.match(r"^seite[_\- ]*(\d+)(?:-(\d+))?$", rest, re.I)
    seite = [int(s.group(1)), int(s.group(2) or s.group(1))] if s else None
    return rank + (natural(rest),), key, label + (" · " + page if page else ""), seite


def fresh(out, src):
    return os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src)


def convert(src):
    """JPEG preview for image formats browsers can't show."""
    out = os.path.join(PREV, os.path.relpath(src, RES)) + ".jpg"
    if not fresh(out, src):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print("  preview", out)
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "82", "-Z", str(MAX_PX), src, "--out", out],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return [out]


def pdf_pages(src):
    """One JPEG per PDF page: previews/<path>.pdf.p1.jpg, .p2.jpg, …"""
    prefix = os.path.join(PREV, os.path.relpath(src, RES)) + ".p"
    pages = sorted(glob.glob(glob.escape(prefix) + "*.jpg"), key=natural)
    if not pages or not fresh(pages[0], src):
        for p in pages:
            os.remove(p)
        os.makedirs(os.path.dirname(prefix), exist_ok=True)
        print("  pages  ", src)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as js:
            js.write(PDF_JXA)
        try:
            n = int(subprocess.run(["osascript", "-l", "JavaScript", js.name, os.path.abspath(src),
                                    os.path.abspath(prefix), str(MAX_PX)],
                                   check=True, capture_output=True, text=True).stdout.strip())
        finally:
            os.remove(js.name)
        pages = [prefix + "%d.jpg" % (i + 1) for i in range(n)]
    return pages


def size(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                         capture_output=True, text=True).stdout
    w, h = re.search(r"pixelWidth: (\d+)", out), re.search(r"pixelHeight: (\d+)", out)
    return [int(w.group(1)), int(h.group(1))] if w and h else [4, 3]


def read_notes(rel):
    """Parse <rel>/notizen.txt: 'Key: value' lines, a blank line, then the learnings."""
    notes = {"title": pretty(os.path.basename(rel)), "date": "", "learn": ""}
    path = os.path.join(rel, NOTES)
    if not os.path.exists(path):
        return notes, False
    with open(path, encoding="utf-8") as f:
        lines = f.read().replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*(Datum|Titel)\s*:\s*(.*?)\s*$", lines[i], re.I)
        if not m:
            break
        if m.group(2):
            notes["date" if m.group(1).lower() == "datum" else "title"] = nfc(m.group(2))
        i += 1
    notes["learn"] = nfc("\n".join(l.strip() for l in lines[i:] if l.strip()))
    return notes, True


def scan_exercise(rel):
    files = []
    for name in listdir(rel):
        path = os.path.join(rel, name)
        if os.path.isdir(path):
            continue
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext in IMG:
            kind, pages = "img", [path]
        elif ext in CONVERT:
            kind, pages = "img", convert(path)
        elif ext == ".pdf":
            kind, pages = "pdf", pdf_pages(path)
        elif ext in VIDEO:
            kind, pages = "video", [path]
        elif ext in TEXT:
            continue
        else:
            print("  skipped (unknown type):", path)
            continue
        sort, ver, label, seite = version(stem)
        f = {"sort": sort, "src": nfc(path), "kind": kind, "ver": ver, "label": nfc(label),
             "pages": [{"src": nfc(p), "size": [16, 9] if kind == "video" else size(p)} for p in pages]}
        if seite:
            f["seite"] = seite
        files.append(f)
    files.sort(key=lambda f: f.pop("sort"))
    return {"dir": nfc(rel), "files": files}


def add_notes(topic, exercises):
    """Attach texts; a topic's notes are the fallback for its exercises."""
    rel = topic["dir"]
    single = len(exercises) == 1 and exercises[0]["dir"] == rel
    tnotes, has_topic = read_notes(rel)
    topic.update(tnotes if not single else {"title": tnotes["title"]})
    for e in exercises:
        notes, has = read_notes(e["dir"])
        if not has and not has_topic:
            print("  created", os.path.join(e["dir"], NOTES))
            with open(os.path.join(e["dir"], NOTES), "w", encoding="utf-8") as f:
                f.write(NOTES_TEMPLATE)
        e.update(notes)


def scan():
    topics = []
    for name in listdir(RES):
        rel = os.path.join(RES, name)
        if not os.path.isdir(rel):
            continue
        subs = [s for s in listdir(rel) if os.path.isdir(os.path.join(rel, s))]
        if subs:
            loose = [f for f in listdir(rel) if f not in subs and os.path.splitext(f)[1].lower() not in TEXT]
            if loose:
                print("  ignored (topic has subfolders):", ", ".join(os.path.join(rel, f) for f in loose))
            exercises = [scan_exercise(os.path.join(rel, s)) for s in subs]
        else:
            exercises = [scan_exercise(rel)]
        topic = {"dir": nfc(rel)}
        add_notes(topic, exercises)
        topic["exercises"] = exercises
        topics.append(topic)
    return topics


def clean_previews(topics):
    """Delete previews whose source file was renamed or removed."""
    used = {nfc(p["src"]) for t in topics for e in t["exercises"] for f in e["files"] for p in f["pages"]}
    for base, dirs, names in os.walk(PREV, topdown=False):
        for n in names:
            p = os.path.join(base, n)
            if nfc(p) not in used:
                print("  removed", p)
                os.remove(p)
        if base != PREV and not os.listdir(base):
            os.rmdir(base)


def block(html, block_id):
    m = re.search(r'(<script type="application/json" id="%s">)(.*?)(</script>)' % block_id, html, re.S)
    if not m:
        sys.exit('index.html: <script id="%s"> block not found' % block_id)
    return m


def main():
    os.chdir(ROOT)
    print("Scanning", RES)
    topics = scan()
    clean_previews(topics)
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    m = block(html, "files")
    html = html[:m.start(2)] + "\n" + json.dumps(topics, ensure_ascii=False).replace("</", "<\\/") + "\n" + html[m.end(2):]
    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(html)

    n = sum(len(t["exercises"]) for t in topics)
    print("Done: %d Themen, %d Übungen." % (len(topics), n))
    missing = [e["dir"] for t in topics for e in t["exercises"] if not e["learn"] and not t.get("learn")]
    if missing:
        print("Notes still empty (write them in notizen.txt):")
        for d in missing:
            print("  -", d)


if __name__ == "__main__":
    main()
