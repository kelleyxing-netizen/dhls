"""Check publishing invariants without requiring Word or external services."""

import re
import struct
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from dhls.utils.paths import ROOT, artifact_paths
from dhls.core.target_flow import NODES

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def local_link_errors(path, root=ROOT):
    errors = []
    text = Path(path).read_text(encoding="utf-8")
    for raw in re.findall(r'!?\[(?:\\.|[^\]\\])*\]\(([^)]+)\)', text):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = unquote(raw.split("#", 1)[0]).strip("<>")
        if not target:
            continue
        resolved = (Path(path).parent / target).resolve()
        if not resolved.is_relative_to(Path(root).resolve()):
            errors.append(f"Link escapes repository: {path}: {target}")
        elif not resolved.exists():
            errors.append(f"Broken local link: {path}: {target}")
    return errors


def check_repository(root=ROOT):
    root = Path(root).resolve()
    paths = artifact_paths(root)
    errors = []
    for key, path in paths.items():
        if not path.is_file():
            errors.append(f"Missing artifact {key}: {path}")
    if errors:
        return errors
    for path in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        errors.extend(local_link_errors(path, root))
    with ZipFile(paths["report_docx"]) as z:
        for name in ("word/document.xml", "word/styles.xml", "word/header1.xml", "word/footer1.xml"):
            xml = ET.fromstring(z.read(name))
            for font in xml.iter(W + "rFonts"):
                if any(value != "Microsoft YaHei" for value in font.attrib.values()):
                    errors.append(f"Mixed or theme font in {name}")
                    break
        xml = ET.fromstring(z.read("word/document.xml"))
        if len(list(xml.iter(W + "tbl"))) != 11:
            errors.append("Expected 11 report tables")
    with paths["flow_png"].open("rb") as file:
        header = file.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (1800, 3650):
        errors.append("Unexpected flowchart PNG header or dimensions")
    text = paths["flow_md"].read_text(encoding="utf-8")
    if "```mermaid" not in text or any(f"N{i:02d}" not in text for i in range(1, 16)):
        errors.append("Flowchart Markdown lacks its 15-node Mermaid source")
    if "合成" not in paths["source"].read_text(encoding="utf-8"):
        errors.append("Source lacks synthetic-interview disclaimer")
    with paths["ai_flow_png"].open("rb") as file:
        header = file.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (1800, 4050):
        errors.append("Unexpected AI target flowchart PNG header or dimensions")
    target_md = paths["ai_flow_md"].read_text(encoding="utf-8")
    if "```mermaid" not in target_md or any(node.id not in target_md for node in NODES):
        errors.append("AI target flowchart Markdown lacks its matching Mermaid source")
    if "尚未实施" not in target_md or "脱敏只读" not in target_md:
        errors.append("AI target flowchart must disclose proposed status and read-only scope")
    return errors
