# kAIrOS testrig (dev/rig/testrig) — everything needed to develop AND hardware-test kAIrOS lives in
# this image; the host only provides Docker, /dev/kvm and (optionally) the vfio-bound GPU.
# The baked /rig copies of vm.sh + expose-ui.sh go stale when the tree changes: docker-compose.yml runs
# the mounted /workspace/repo/dev/rig/testrig/vm.sh instead, so a rebuild is only needed for packages.
#
#  * QEMU/KVM + OVMF        -> boots the kairos-hwtest .img (UEFI/systemd-boot),
#                              with the real RTX 4070 passed through via VFIO
#  * python3 + aiohttp      -> run the kAIrOS kernel + full mock test suite
#  * build toolchain        -> rebuild engines (llama.cpp / sd.cpp) if needed
#  * ubuntu:24.04 base      -> a fast, widely mirrored base; the guest VM is still genuine
#                              Debian trixie (the image under test), and the prebuilt
#                              engines run inside the VM, not in this container.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# slow flaky uplink: retry + generous timeouts instead of dying mid-download
RUN printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\n' > /etc/apt/apt.conf.d/99-kairos-net \
    && apt-get update && apt-get install -y --no-install-recommends \
    # --- VM stack ---
    qemu-system-x86 qemu-utils ovmf \
    # --- guest interaction / verification ---
    openssh-client sshpass socat netcat-openbsd expect \
    # --- kAIrOS kernel + test suite ---
    python3 python3-aiohttp python3-pip python3-venv \
    # --- dev toolchain (engine rebuilds, misc) ---
    build-essential cmake pkg-config git \
    # --- utilities ---
    curl wget ca-certificates jq ripgrep tmux htop procps psmisc \
    pciutils usbutils iproute2 iputils-ping dnsutils \
    aria2 pigz unzip zstd rsync file less vim-tiny \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /rig
COPY vm.sh expose-ui.sh /rig/
RUN chmod +x /rig/vm.sh /rig/expose-ui.sh

# extras for full test-suite coverage (own layer: keeps the big apt layer cached)
#   ffmpeg  -> video test [R6]; python3-pygame -> game forge test [R7, R11]
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg python3-pygame \
    && rm -rf /var/lib/apt/lists/*

# workspace mount points (populated by docker-compose volumes; /workspace/engines = a built engine set)
RUN mkdir -p /images /workspace/repo /workspace/models /workspace/knowledge /workspace/engines \
    # tests/run-tests.sh looks for the knowledge base + kiwix-serve at the appliance paths:
    && mkdir -p /opt/vibeos \
    && ln -s /workspace/knowledge /opt/vibeos/knowledge \
    && ln -s /workspace/bin /opt/vibeos/bin

CMD ["/bin/bash"]
