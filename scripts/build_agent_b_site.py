#!/usr/bin/env python
"""Stage the vault into a Quartz checkout ready for `npx quartz build` (S-B5).

The Python half of the publish step: it writes the config-driven `quartz.config.ts` (baseUrl from
`AgentBConfig.publish`, AD-4), the custom stylesheet for the AI-suggested callout, and copies the
vault's notes into Quartz's `content/`. The actual Node build runs off-box (CI) or in the nightly job
via `deploy/build_site.sh`, never on the 1 GB Droplet (AD-21).

    .venv/bin/python scripts/build_agent_b_site.py --quartz-dir /opt/agent/quartz
    .venv/bin/python scripts/build_agent_b_site.py --quartz-dir ./quartz --build   # also run npx build
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from agent_b.config import load_agent_b_config_file
    from agent_b.pipeline import (
        render_custom_css,
        render_index_md,
        set_quartz_base_url,
        stage_content,
    )

    parser = argparse.ArgumentParser(description="Stage the vault for a Quartz build (S-B5).")
    parser.add_argument(
        "--quartz-dir",
        required=True,
        help="path to a Quartz v4 checkout (see deploy/build_site.sh)",
    )
    parser.add_argument("--build", action="store_true", help="also run `npx quartz build`")
    args = parser.parse_args()

    config = load_agent_b_config_file(ROOT / "config" / "registry.yaml")
    if config is None:
        print("No 'agent_b:' block in config/registry.yaml — Agent B is not configured here.")
        return 1

    quartz = Path(args.quartz_dir)
    if not quartz.is_dir():
        print(
            f"Quartz checkout not found at {quartz}. Run deploy/build_site.sh, or clone it first."
        )
        return 1

    # Patch only the baseUrl in Quartz's OWN config (build_site.sh restores it to pristine first), so
    # we keep Quartz's version-correct theme/layout/plugins — a hand-written config shipped an unstyled
    # site (missing theme palette).
    cfg_path = quartz / "quartz.config.ts"
    cfg_path.write_text(
        set_quartz_base_url(cfg_path.read_text(encoding="utf-8"), config.publish.base_url),
        encoding="utf-8",
    )
    styles = quartz / "quartz" / "styles"
    styles.mkdir(parents=True, exist_ok=True)
    (styles / "custom.scss").write_text(render_custom_css(), encoding="utf-8")
    staged = stage_content(config.vault_dir, str(quartz / "content"))
    # Quartz builds the site root (index.html) only from content/index.md — the vault has none, so
    # write one, or the homepage 404s (per-note URLs still resolve).
    (quartz / "content" / "index.md").write_text(render_index_md(), encoding="utf-8")
    print(
        f"staged {staged} note(s) + index.md into {quartz / 'content'}; "
        "wrote quartz.config.ts + custom.scss"
    )

    if args.build:
        out = str((ROOT / config.publish.output_dir).resolve())
        print(f"building site into {out} ...")
        subprocess.run(["npx", "quartz", "build", "--output", out], cwd=quartz, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
