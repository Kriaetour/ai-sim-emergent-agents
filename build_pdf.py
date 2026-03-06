#!/usr/bin/env python3
"""
Build a publication-quality PDF from thalren_vale_paper_FINAL.md using Pandoc + XeLaTeX.

Usage:
    python build_pdf.py                  # build docs/thalren_vale_paper_FINAL.pdf
    python build_pdf.py --install        # install Pandoc + MiKTeX via winget, then build
    python build_pdf.py --check          # check dependencies only, don't build
    python build_pdf.py --output out.pdf # custom output path

Dependencies (auto-installed with --install):
    - Pandoc  >= 3.0   (winget install JohnMacFarlane.Pandoc)
    - MiKTeX  >= 24.0  (winget install MiKTeX.MiKTeX)
      After MiKTeX install, open MiKTeX Console once to enable auto-install of
      LaTeX packages, then run: miktex packages install adjustbox collectbox
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORKSPACE = Path(__file__).resolve().parent
PAPER_MD = WORKSPACE / "docs" / "thalren_vale_paper_FINAL.md"
DEFAULT_OUTPUT = WORKSPACE / "docs" / "thalren_vale_paper_FINAL.pdf"
FIGURES_DIR = WORKSPACE / "figures"

# ---------------------------------------------------------------------------
# YAML metadata block prepended to the markdown for Pandoc
# ---------------------------------------------------------------------------
YAML_HEADER = """\
---
title: "Thalren Vale: Civilizational-Scale Social Emergence from Survival-Scale Agent Heuristics"
author: Brandon Simms
date: March 2026
keywords:
  - agent-based model
  - emergent institutions
  - belief propagation
  - civilizational simulation
  - ODD protocol
  - social complexity
  - trust dynamics
  - reverse assimilation
geometry: "margin=1in"
fontsize: 11pt
mainfont: "Times New Roman"
monofont: "Consolas"
linestretch: 1.15
documentclass: article
classoption:
  - letterpaper
---
"""

# LaTeX preamble written to a temp .tex file and passed via --include-in-header
# (avoids YAML escaping issues with curly braces)
LATEX_HEADER = r"""
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{graphicx}
\usepackage{float}
\usepackage{hyperref}
\hypersetup{colorlinks=true, linkcolor=blue, urlcolor=blue, citecolor=blue}
\usepackage[export]{adjustbox}
\let\oldincludegraphics\includegraphics
\renewcommand{\includegraphics}[2][]{\oldincludegraphics[max width=\textwidth, #1]{#2}}
"""


def check_cmd(name: str) -> bool:
    """Return True if *name* is found on PATH."""
    return shutil.which(name) is not None


def check_dependencies() -> dict:
    """Check all required external tools and return status dict."""
    status = {
        "pandoc": check_cmd("pandoc"),
        "xelatex": check_cmd("xelatex"),
        "winget": check_cmd("winget"),
    }
    return status


def print_status(status: dict) -> None:
    for tool, found in status.items():
        icon = "OK" if found else "MISSING"
        print(f"  {tool:12s} [{icon}]")


def install_dependencies() -> None:
    """Install Pandoc and MiKTeX via winget."""
    if not check_cmd("winget"):
        print("ERROR: winget not found. Install dependencies manually:")
        print("  Pandoc:  https://pandoc.org/installing.html")
        print("  MiKTeX:  https://miktex.org/download")
        sys.exit(1)

    installs = []
    if not check_cmd("pandoc"):
        installs.append(("JohnMacFarlane.Pandoc", "Pandoc"))
    if not check_cmd("xelatex"):
        installs.append(("MiKTeX.MiKTeX", "MiKTeX"))

    if not installs:
        print("All dependencies already installed.")
        return

    for pkg_id, name in installs:
        print(f"Installing {name} via winget...")
        result = subprocess.run(
            ["winget", "install", "--id", pkg_id, "-e", "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"WARNING: winget returned exit code {result.returncode} for {name}.")
            print(f"  You may need to install {name} manually.")

    # Refresh PATH for the current session
    print()
    print("=" * 60)
    print("IMPORTANT: After installation completes, you must either:")
    print("  1. Close and reopen your terminal/VS Code, OR")
    print("  2. Run:  $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')")
    print()
    print("Then re-run:  python build_pdf.py")
    print()
    print("If this is your first MiKTeX install, also:")
    print("  - Open MiKTeX Console (search Start menu)")
    print("  - Go to Settings -> 'Always install missing packages on-the-fly'")
    print("  - This lets MiKTeX auto-download LaTeX packages as needed.")
    print("=" * 60)


def preprocess_markdown(md_text: str, md_dir: Path) -> str:
    """Prepare the markdown for Pandoc consumption.

    - Strip the manual title/author/keywords block (replaced by YAML header)
    - Convert image paths from relative (../figures/) to absolute paths
    - Remove the '/* Lines X-Y omitted */' artifacts if any
    """
    lines = md_text.split("\n")
    processed = []
    skip_header = True  # skip until we hit the first ---

    i = 0
    header_dashes_seen = 0
    while i < len(lines):
        line = lines[i]

        # Skip the manual header block (everything before the first ---)
        if skip_header:
            if line.strip() == "---":
                header_dashes_seen += 1
                if header_dashes_seen == 1:
                    # Skip everything up to and including the first ---
                    # (the title/author/keywords block ends here)
                    i += 1
                    skip_header = False
                    continue
            i += 1
            continue

        # Convert relative image paths to absolute
        # ![alt](../figures/fig1.png) -> ![alt](C:/.../figures/fig1.png)
        img_match = re.match(r'^(!\[.*?\])\((\.\.\/figures\/[^)]+)\)(.*)$', line)
        if img_match:
            alt_text = img_match.group(1)
            rel_path = img_match.group(2)
            rest = img_match.group(3)
            abs_path = (md_dir / rel_path).resolve()
            # Use forward slashes for LaTeX compatibility
            abs_str = str(abs_path).replace("\\", "/")
            line = f"{alt_text}({abs_str}){rest}"

        processed.append(line)
        i += 1

    return YAML_HEADER + "\n".join(processed)


def build_pdf(output_path: Path) -> None:
    """Run the full Pandoc build pipeline."""
    if not PAPER_MD.exists():
        print(f"ERROR: Source markdown not found: {PAPER_MD}")
        sys.exit(1)

    status = check_dependencies()
    if not status["pandoc"]:
        print("ERROR: Pandoc not found. Run:  python build_pdf.py --install")
        sys.exit(1)
    if not status["xelatex"]:
        print("ERROR: XeLaTeX not found. Run:  python build_pdf.py --install")
        sys.exit(1)

    # Read and preprocess
    print(f"Reading {PAPER_MD.name}...")
    md_text = PAPER_MD.read_text(encoding="utf-8")
    processed = preprocess_markdown(md_text, PAPER_MD.parent)

    # Write preprocessed markdown to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8", dir=PAPER_MD.parent
    ) as tmp:
        tmp.write(processed)
        tmp_path = Path(tmp.name)

    # Write LaTeX header to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tex", delete=False, encoding="utf-8", dir=PAPER_MD.parent
    ) as htmp:
        htmp.write(LATEX_HEADER)
        header_path = Path(htmp.name)

    try:
        print(f"Building PDF with Pandoc + XeLaTeX...")
        print(f"  Source:  {PAPER_MD}")
        print(f"  Output:  {output_path}")
        print()

        cmd = [
            "pandoc",
            str(tmp_path),
            "-o", str(output_path),
            "--pdf-engine=xelatex",
            "--from=markdown+pipe_tables+yaml_metadata_block+implicit_figures",
            "--standalone",
            "--toc",
            "--toc-depth=3",
            "--include-in-header", str(header_path),
            "--resource-path", str(FIGURES_DIR),
            "--resource-path", str(PAPER_MD.parent),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print("=" * 60)
            print("BUILD FAILED")
            print("=" * 60)
            if result.stderr:
                print(result.stderr)
            if "adjustbox" in (result.stderr or ""):
                print("\nHINT: Missing LaTeX package. Run in terminal:")
                print("  miktex packages install adjustbox collectbox")
            sys.exit(1)

        size_kb = output_path.stat().st_size / 1024
        print(f"SUCCESS: {output_path.name} ({size_kb:.0f} KB)")

    finally:
        tmp_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Build PDF from Thalren Vale paper markdown"
    )
    parser.add_argument(
        "--install", action="store_true",
        help="Install Pandoc and MiKTeX via winget"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check dependencies and exit"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=DEFAULT_OUTPUT,
        help=f"Output PDF path (default: {DEFAULT_OUTPUT.name})"
    )
    args = parser.parse_args()

    print("Thalren Vale Paper — PDF Build Script")
    print("=" * 40)
    print()

    if args.install:
        install_dependencies()
        # Re-check after install attempt
        status = check_dependencies()
        print("\nDependency status after install:")
        print_status(status)
        if all(status[k] for k in ("pandoc", "xelatex")):
            print("\nAll dependencies found. Building PDF...")
            build_pdf(args.output)
        return

    if args.check:
        status = check_dependencies()
        print("Dependency status:")
        print_status(status)
        missing = [k for k, v in status.items() if not v and k != "winget"]
        if missing:
            print(f"\nMissing: {', '.join(missing)}")
            print("Run:  python build_pdf.py --install")
        else:
            print("\nAll dependencies found. Ready to build.")
        return

    build_pdf(args.output)


if __name__ == "__main__":
    main()
