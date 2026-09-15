"""Export the same normalized workflow summary using the shared report renderer."""

import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "inherited"))
from generate_audience_reports import make_docx
from d11lib.workflow import Workflow
from d11lib.common import file_hash, write

p = argparse.ArgumentParser()
p.add_argument("--run", required=True)
a = p.parse_args()
w = Workflow()
w.report(a.run)
out, _ = w.run(a.run)
make_docx(out / "summary.md", out / "summary.docx", "Drupal Upgrade Workflow Result")
from docx import Document
from docx.shared import RGBColor
from docx.oxml.ns import qn

doc = Document(out / "summary.docx")
for style in doc.styles:
    if style.name == "Title":
        style.font.color.rgb = RGBColor(0, 0, 0)
        for node in style.element.xpath(".//w:pBdr"):
            node.getparent().remove(node)
for paragraph in doc.paragraphs:
    if paragraph.style.name == "Title":
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(0, 0, 0)
        for node in paragraph._p.xpath(".//w:pBdr"):
            node.getparent().remove(node)
doc.save(out / "summary.docx")
write(out / "docx-source.json", {"markdownHash": file_hash(out / "summary.md")})
hashes = {name: file_hash(out / name) for name in w.artifacts(a.run) if name != "hashes.json"}
write(out / "hashes.json", hashes)
print(out / "summary.docx")
