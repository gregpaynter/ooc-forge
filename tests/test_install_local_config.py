from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_installer_preserves_existing_default_checkpoint():
    installer = (ROOT / "scripts" / "install-local.sh").read_text(encoding="utf-8")

    assert '[[ -z "${FORGE_DEFAULT_CHECKPOINT+x}" && -f /etc/ooc-forge/forge.env ]]' in installer
    assert "sed -n 's/^FORGE_DEFAULT_CHECKPOINT=//p' /etc/ooc-forge/forge.env" in installer
    assert 'FORGE_DEFAULT_CHECKPOINT=${FORGE_DEFAULT_CHECKPOINT:-}' in installer
    assert "FORGE_DEFAULT_CHECKPOINT=$FORGE_DEFAULT_CHECKPOINT" in installer
