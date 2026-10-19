"""Package vscode/ into dist/yousef-ai.vsix without Node or vsce.

A .vsix is a zip (Open Packaging Conventions) holding the extension under
extension/, an extension.vsixmanifest and [Content_Types].xml — the same
layout vsce produces. Install it with:
    code --install-extension dist/yousef-ai.vsix
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "vscode"
OUT = ROOT / "dist" / "yousef-ai.vsix"
INCLUDE = ["package.json", "extension.js", "README.md", "lib/*.js", "media/*"]

CONTENT_TYPES = {
    ".json": "application/json", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml",
    ".png": "image/png", ".md": "text/markdown", ".vsixmanifest": "text/xml", ".html": "text/html",
}


def manifest(pkg: dict) -> str:
    kinds = ",".join(pkg.get("extensionKind", ["workspace"]))
    icon = ""
    if pkg.get("icon"):
        icon = f'\n    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/{escape(pkg["icon"])}" Addressable="true"/>'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{escape(pkg["name"])}" Version="{escape(pkg["version"])}" Publisher="{escape(pkg["publisher"])}"/>
    <DisplayName>{escape(pkg.get("displayName", pkg["name"]))}</DisplayName>
    <Description xml:space="preserve">{escape(pkg.get("description", ""))}</Description>
    <Tags>{escape(",".join(pkg.get("keywords", [])))}</Tags>
    <Categories>{escape(",".join(pkg.get("categories", ["Other"])))}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{escape(pkg["engines"]["vscode"])}"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="{escape(kinds)}"/>
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value=""/>
      <Property Id="Microsoft.VisualStudio.Services.GitHubFlavoredMarkdown" Value="true"/>
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true"/>{icon}
  </Assets>
</PackageManifest>
"""


def content_types(extensions: set[str]) -> str:
    rows = "".join(f'<Default Extension="{ext}" ContentType="{CONTENT_TYPES.get(ext, "application/octet-stream")}"/>'
                   for ext in sorted(extensions))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">{rows}</Types>\n')


def collect() -> list[Path]:
    files: list[Path] = []
    for pattern in INCLUDE:
        files.extend(sorted(p for p in SRC.glob(pattern) if p.is_file()))
    return files


def build(out: Path = OUT) -> Path:
    pkg = json.loads((SRC / "package.json").read_text(encoding="utf-8"))
    files = collect()
    missing = [name for name in ("package.json", "extension.js") if not (SRC / name).exists()]
    if missing:
        raise SystemExit(f"missing {missing}")
    out.parent.mkdir(parents=True, exist_ok=True)
    extensions = {p.suffix.lower() for p in files} | {".vsixmanifest"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types(extensions))
        z.writestr("extension.vsixmanifest", manifest(pkg))
        for path in files:
            z.write(path, "extension/" + path.relative_to(SRC).as_posix())
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    path = build(target)
    print(f"Built {path} ({path.stat().st_size // 1024} KB)")
