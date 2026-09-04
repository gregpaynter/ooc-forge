from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def parse_env(relative: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in read(relative).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_execution_stack_is_pinned_for_reference_3090():
    pins = parse_env("scripts/comfyui-runtime.env")
    assert pins == {
        "COMFYUI_VERSION": "v0.34.0",
        "COMFYUI_COMMIT": "12d5279438bfefc058a269eae805ceab6047777f",
        "TORCH_VERSION": "2.7.1",
        "TORCHVISION_VERSION": "0.22.1",
        "TORCHAUDIO_VERSION": "2.7.1",
        "PYTORCH_INDEX_URL": "https://download.pytorch.org/whl/cu126",
        "PYTORCH_CUDA_VERSION": "12.6",
    }


def test_comfyui_remains_local_and_uses_persistent_forge_data():
    service = read("systemd/comfyui.service")
    assert "--listen 127.0.0.1 --port 8188" in service
    assert "0.0.0.0" not in service
    assert "Requires=ooc-forge-init.service ooc-forge-gpu-init.service" in service
    assert "ExecStartPre=/opt/ooc-forge/.venv/bin/ooc-forge init" in service
    for path in (
        "/forge-data/comfyui-input",
        "/forge-data/comfyui-output",
        "/forge-data/comfyui-temp",
        "/forge-data/comfyui-user",
        "XDG_CACHE_HOME=/forge-data/cache",
    ):
        assert path in service


def test_gpu_bootstrap_proves_uvm_and_real_cuda_allocation():
    script = read("scripts/ooc-forge-gpu-init")
    assert "modprobe nvidia-current-uvm" in script
    assert 'awk \'$2 == "nvidia-uvm"' in script
    assert "mknod /dev/nvidia-uvm" in script
    assert "torch.cuda.is_available()" in script
    assert 'torch.zeros(1, device="cuda")' in script

    service = read("systemd/ooc-forge-gpu-init.service")
    assert "Before=comfyui.service" in service
    assert "ExecStart=/usr/local/sbin/ooc-forge-gpu-init" in service


def test_iso_owns_driver_ssh_apt_network_and_gpu_recovery():
    packages = read("iso/config/package-lists/forge.list.chroot").splitlines()
    for package in (
        "nvidia-driver",
        "nvidia-modprobe",
        "linux-headers-amd64",
        "dkms",
        "kmod",
        "openssh-server",
        "network-manager",
    ):
        assert package in packages

    hook = read("iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot")
    assert "blacklist nouveau" in hook
    assert "nvidia-current-uvm.ko" in hook
    assert "update-initramfs -u -k all" in hook
    assert "systemctl enable ssh.service" in hook
    assert "systemctl enable ooc-forge-gpu-init.service" in hook
    assert "systemctl enable comfyui.service" in hook
    assert "rm -f /etc/ssh/ssh_host_*" in hook
    assert "127.0.1.1\\tforge" in hook

    policy = read("scripts/ooc-forge-appliance-policy")
    assert "ssh-keygen -A" in policy
    assert "127.0.1.1\\tforge" in policy
    assert "deb https://deb.debian.org/debian trixie" in policy
    assert "iface lo inet loopback" in policy
    assert "managed=true" in policy

    policy_unit = read("systemd/ooc-forge-appliance-policy.service")
    assert "Before=NetworkManager.service ssh.service" in policy_unit
