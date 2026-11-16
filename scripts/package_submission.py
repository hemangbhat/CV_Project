"""Package compiled submission reports and render contact-sheet previews."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import subprocess
import tempfile
from PIL import Image, ImageOps, ImageDraw

paper = Path("paper")
with ZipFile(paper / "overleaf_upload.zip", "w", ZIP_DEFLATED) as archive:
    for name in ("main.tex", "main_journal.tex", "README.md"):
        archive.write(paper / name, name)
    for path in sorted((paper / "figures").glob("*")):
        if path.is_file():
            archive.write(path, f"figures/{path.name}")
previews = paper / "previews"
previews.mkdir(exist_ok=True)
for stem, label in (
    ("Vision_Measured_Queue_Tails_Paper", "conference"),
    ("Vision_Measured_Queue_Tails_Paper_journal", "journal"),
):
    with tempfile.TemporaryDirectory() as directory:
        prefix = str(Path(directory) / "page")
        subprocess.run(["pdftoppm", "-r", "65", "-jpeg", str(paper / f"{stem}.pdf"), prefix], check=True)
        paths = sorted(Path(directory).glob("page-*.jpg"))
        if not paths:
            raise RuntimeError(f"No rendered pages for {label}")
        width, height = 570, 790
        canvas = Image.new("RGB", (2 * width, ((len(paths) + 1) // 2) * height), "#d8d8d8")
        for i, path in enumerate(paths):
            page = Image.open(path).convert("RGB")
            page.thumbnail((width - 20, height - 35))
            x = (i % 2) * width + (width - page.width) // 2
            y = (i // 2) * height + 22
            canvas.paste(page, (x, y))
            ImageDraw.Draw(canvas).text(((i % 2) * width + 10, (i // 2) * height + 4),
                                        f"{label}, page {i + 1}", fill="black")
        canvas.save(previews / f"{label}.jpg", quality=88)
        print(f"{label}: {len(paths)} pages rendered")
