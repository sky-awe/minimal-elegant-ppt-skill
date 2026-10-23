"""Validate this distributable skill and its PPTX template; Python standard library only."""
from __future__ import annotations

import re
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(root: Path) -> dict:
    skill = root / "skills" / "research-seminar"
    required = [
        root / "LICENSE", root / "README.md", skill / "SKILL.md",
        skill / "agents" / "openai.yaml", skill / "references" / "design-guide.md",
        skill / "assets" / "template.pptx", skill / "assets" / "preview.png",
    ]
    for path in required:
        require(path.is_file(), f"Missing file: {path.relative_to(root)}")
    instructions = (skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", instructions, flags=re.S)
    require(match is not None, "SKILL.md needs YAML frontmatter")
    frontmatter = match.group(1)
    require(bool(re.search(r"^name: research-seminar$", frontmatter, re.M)), "Unexpected skill name")
    require(bool(re.search(r"^description: .+", frontmatter, re.M)), "Missing description")
    require("$research-seminar" in (skill / "agents" / "openai.yaml").read_text(encoding="utf-8"), "UI prompt needs the skill name")
    for target in re.findall(r"\]\(([^)]+)\)", instructions):
        if "://" not in target and not target.startswith("#"):
            resolved = (skill / target.split("#", 1)[0]).resolve()
            require(resolved.is_relative_to(skill.resolve()) and resolved.exists(), f"Broken resource link: {target}")
    preview = (skill / "assets" / "preview.png").read_bytes()
    require(preview[:8] == b"\x89PNG\r\n\x1a\n", "Preview is not PNG")
    width, height = struct.unpack(">II", preview[16:24])
    require(width >= 1000 and height >= 500, "Preview is too small")
    with zipfile.ZipFile(skill / "assets" / "template.pptx") as package:
        require(package.testzip() is None, "Corrupt PPTX ZIP member")
        names = set(package.namelist())
        # A blank evidence template should not carry source figures or embedded files.
        forbidden = [n for n in names if n.startswith(("ppt/media/", "ppt/embeddings/", "ppt/comments/", "customXml/"))]
        require(not forbidden, f"Unexpected media, embedding or hidden payload: {forbidden}")
        slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        notes = [n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)]
        require(len(slides) == len(notes) == 15, "Expected 15 slides and 15 speaker notes")
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        size = presentation.find("p:sldSz", NS)
        require(size is not None and (size.get("cx"), size.get("cy")) == ("12192000", "6858000"), "Expected 16:9 template size")
        table_count = 0
        placeholder_count = 0
        for name in slides:
            xml = ET.fromstring(package.read(name))
            require(xml.get("show", "1") != "0", f"Hidden slide: {name}")
            require(not xml.findall(".//p:pic", NS), f"Unexpected source image: {name}")
            table_count += len(xml.findall(".//a:tbl", NS))
            for shape in xml.findall(".//p:sp", NS):
                prop = shape.find("p:nvSpPr/p:cNvPr", NS)
                if prop is not None and prop.get("name", "").startswith("figure-placeholder-"):
                    placeholder_count += 1
        require(table_count == 2, "Expected two native editable tables")
        require(placeholder_count == 12, f"Expected 12 figure/legend placeholders, found {placeholder_count}")
        for name in notes:
            xml = ET.fromstring(package.read(name))
            require(len("".join(xml.itertext())) > 50, f"Empty speaker notes: {name}")
        for name in names:
            if not name.endswith(".rels"):
                continue
            xml = ET.fromstring(package.read(name))
            for rel in xml.findall(f"{{{RELS}}}Relationship"):
                require(rel.get("TargetMode") != "External", f"External relationship: {name}")
                target = rel.get("Target", "")
                base = PurePosixPath() if name == "_rels/.rels" else PurePosixPath(name).parent.parent
                parts = []
                for part in (base / target).parts:
                    if part == "..":
                        require(bool(parts), f"Escaping relationship: {name}")
                        parts.pop()
                    elif part not in (".", "/"):
                        parts.append(part)
                require("/".join(parts) in names, f"Missing relationship target: {name} -> {target}")
    return {"slides": 15, "speaker_notes": 15, "native_tables": table_count, "figure_placeholders": placeholder_count}


if __name__ == "__main__":
    try:
        result = validate(Path(__file__).resolve().parents[1])
    except (ValueError, OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    print("PASS:", ", ".join(f"{key}={value}" for key, value in result.items()))
