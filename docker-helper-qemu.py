#!/usr/bin/env python3

import os
import sys
import time
import subprocess as sp
import logging
import re

try:
    import coloredlogs
    coloredlogs.install(level=logging.INFO)
except Exception:
    logging.basicConfig(level=logging.INFO)


def sh(cmd, capture=True):
    return sp.run(cmd, capture_output=capture, text=True)


def print_usage(argv0):
    print("[*] Usage (QEMU live snapshots):")
    print(f"  {argv0} -prepare <firmware_path>        # Convert to qcow2 and restart QEMU to support savevm")
    print(f"  {argv0} -qmsave <firmware_path> <name>  # Save live snapshot via QEMU monitor (no restart)")
    print(f"  {argv0} -qmload <firmware_path> <name>  # Load snapshot via QEMU monitor (no restart)")
    print(f"  {argv0} -qmls <firmware_path>           # List snapshots via QEMU monitor")
    print(f"  {argv0} -qmdel <firmware_path> <name>  # Delete a snapshot via QEMU monitor")
    print("\nNotes:")
    print("- Works with containers started by docker-helper.py. The container must be running.")
    print("- Requires the VM to use qcow2 (image.qcow2). Use -prepare once per IID to enable.")


def get_container_list(all_containers=False):
    cmd = ["docker", "ps", "-a"] if all_containers else ["docker", "ps"]
    cmd += ["--format", "{{.Names}} {{.Status}}"]
    p = sh(cmd)
    if p.returncode != 0:
        logging.error("[-] docker ps failed: %s", p.stderr.strip())
        return []
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    out = []
    for line in lines:
        try:
            name, status = line.split(' ', 1)
        except ValueError:
            name, status = line.strip(), ''
        out.append((name, status))
    return out


def find_container_for_firmware(firmware_basename):
    sanitized = firmware_basename.replace('/', '_').replace(' ', '_').replace('.', '_')
    pat = re.compile(rf"^docker\d+_{re.escape(sanitized)}$")
    for name, _ in get_container_list():
        if pat.match(name):
            return name, 'running'
    for name, _ in get_container_list(all_containers=True):
        if pat.match(name):
            return name, 'stopped'
    return None, None


def ensure_container_running(name, status):
    if status == 'running':
        return True
    p = sh(["docker", "start", name])
    if p.returncode == 0:
        time.sleep(2)
        return True
    logging.error("[-] Failed to start container %s: %s", name, p.stderr.strip())
    return False


def find_iid(container, firmware_basestem):
    cmd = [
        "docker", "exec", container, "bash", "-lc",
        f"for d in /work/FirmAE/scratch/*; do [ -f \"$d/name\" ] || continue; n=$(cat \"$d/name\"); if [ \"$n\" = \"{firmware_basestem}\" ]; then basename \"$d\"; exit 0; fi; done; exit 1"
    ]
    p = sh(cmd)
    if p.returncode != 0:
        return None
    return p.stdout.strip().splitlines()[0]


def docker_exec(container, script):
    return sh(["docker", "exec", container, "bash", "-lc", script])


def ensure_qcow2_and_restart(container, iid):
    sd = f"/work/FirmAE/scratch/{iid}"
    raw = f"{sd}/image.raw"
    qcow2 = f"{sd}/image.qcow2"

    # Create qcow2 if missing
    r = docker_exec(container, f"[ -f '{qcow2}' ] || which qemu-img >/dev/null 2>&1 || (apt-get update >/dev/null 2>&1 && apt-get install -y qemu-utils >/dev/null 2>&1) || true")
    if r.returncode != 0:
        logging.warning("[!] qemu-img ensure returned: %s", r.stderr.strip())

    p = docker_exec(container, f"[ -f '{qcow2}' ] || qemu-img convert -p -O qcow2 '{raw}' '{qcow2}' && chmod a+rw '{qcow2}'")
    if p.returncode != 0:
        logging.error("[-] Failed to convert raw->qcow2: %s", p.stderr.strip())
        return False

    # Patch scratch run.sh to use qcow2 explicitly (covers existing scripts)
    patch_cmd = (
        f"set -e; sd=/work/FirmAE/scratch/{iid}; rs=$sd/run.sh; "
        # mips: -drive if=ide,format=raw,file=${IMAGE} -> qcow2
        "sed -i 's@-drive if=ide,format=raw,file=\\${IMAGE}@-drive if=ide,format=qcow2,file=\\${WORK_DIR}/image.qcow2@g' \"$rs\" || true; "
        # arm: -drive if=none,file=${IMAGE},format=raw,id=rootfs -> qcow2
        "sed -i 's@-drive if=none,file=\\${IMAGE},format=raw,id=rootfs@-drive if=none,file=\\${WORK_DIR}/image.qcow2,format=qcow2,id=rootfs@g' \"$rs\" || true; "
    )
    docker_exec(container, patch_cmd)

    # Restart QEMU to pick qcow2
    docker_exec(container, "pkill -f qemu-system || true")
    time.sleep(1)
    # ensure working directory is /work/FirmAE so relative paths in scripts resolve
    p = docker_exec(container, f"cd /work/FirmAE && nohup ./scratch/{iid}/run.sh >./scratch/{iid}/qemu.relaunch.log 2>&1 & disown")
    if p.returncode != 0:
        logging.error("[-] Failed to relaunch QEMU: %s", p.stderr.strip())
        return False

    # wait monitor (up to 180s, firmware may take time to boot to start QEMU)
    for _ in range(180):
        ok = docker_exec(container, f"[ -S '/tmp/qemu.{iid}' ] && echo ok || true")
        if ok.returncode == 0 and 'ok' in ok.stdout:
            return True
        time.sleep(1)
    # show last lines of relaunch log to help diagnose
    log_tail = docker_exec(container, f"tail -n 80 /work/FirmAE/scratch/{iid}/qemu.relaunch.log || true")
    if log_tail.stdout:
        sys.stderr.write(log_tail.stdout)
    logging.error("[-] QEMU monitor socket not ready: /tmp/qemu.%s", iid)
    return False


def hmp(container, iid, commands):
    if isinstance(commands, (list, tuple)):
        payload = "\n".join(commands) + "\n"
    else:
        payload = str(commands).rstrip("\n") + "\n"
    cmd = ["docker", "exec", "-i", container, "socat", "-", f"UNIX-CONNECT:/tmp/qemu.{iid}"]
    return sp.run(cmd, input=payload, text=True, capture_output=True)


def qmsave(container, iid, name):
    # stop; savevm; cont keeps VM consistent
    r = hmp(container, iid, ["stop", f"savevm {name}", "cont", "info snapshots"])
    if r.returncode != 0:
        logging.error("[-] savevm failed: %s", r.stderr.strip())
        return False
    logging.info(r.stdout.rstrip())
    if "{name}" in r.stdout:
        logging.info("[+] Saved snapshot '%s'", name)
    return True


def qmload(container, iid, name):
    r = hmp(container, iid, ["stop", f"loadvm {name}", "cont"])
    if r.returncode != 0:
        logging.error("[-] loadvm failed: %s", r.stderr.strip())
        return False
    logging.info("[+] Loaded snapshot '%s'", name)
    return True


def qmls(container, iid):
    r = hmp(container, iid, ["info snapshots"])
    if r.returncode != 0:
        logging.error("[-] list snapshots failed: %s", r.stderr.strip())
        return False
    print(r.stdout.rstrip())
    return True


def qmdel(container, iid, name):
    r = hmp(container, iid, [f"delvm {name}", "info snapshots"])
    if r.returncode != 0:
        logging.error("[-] delete snapshot failed: %s", r.stderr.strip())
        return False
    logging.info("[+] Deleted snapshot '%s'", name)
    return True


def main():
    if len(sys.argv) < 3:
        print_usage(sys.argv[0])
        sys.exit(1)

    mode = sys.argv[1]
    fw = os.path.abspath(sys.argv[2])
    if not os.path.isabs(fw):
        fw = os.path.abspath(fw)
    fw_base = os.path.basename(fw)
    stem = fw_base.rsplit('.', 1)[0]

    container, status = find_container_for_firmware(fw_base)
    if not container:
        logging.error("[-] No container found for firmware: %s", fw_base)
        sys.exit(1)
    if not ensure_container_running(container, status):
        sys.exit(1)

    iid = find_iid(container, stem)
    if not iid:
        logging.error("[-] Could not find IID for firmware base '%s' in %s", stem, container)
        sys.exit(1)
    logging.info("[*] Target container=%s IID=%s", container, iid)

    if mode == '-prepare':
        if not ensure_qcow2_and_restart(container, iid):
            sys.exit(1)
        # probe savevm support
        r = hmp(container, iid, ["info snapshots"])  # after restart with qcow2
        if r.returncode == 0:
            print(r.stdout.rstrip())
        logging.info("[+] Prepared qcow2 and monitor for live snapshots")
        return

    # ensure monitor exists
    mon = docker_exec(container, f"[ -S '/tmp/qemu.{iid}' ] && echo ok || true")
    if mon.returncode != 0 or 'ok' not in mon.stdout:
        logging.error("[-] QEMU monitor not found. Run '-prepare' first or ensure firmware is running.")
        sys.exit(1)

    if mode == '-qmsave':
        if len(sys.argv) < 4:
            print_usage(sys.argv[0])
            sys.exit(1)
        name = sys.argv[3]
        if not qmsave(container, iid, name):
            sys.exit(1)
        return

    if mode == '-qmload':
        if len(sys.argv) < 4:
            print_usage(sys.argv[0])
            sys.exit(1)
        name = sys.argv[3]
        if not qmload(container, iid, name):
            sys.exit(1)
        return

    if mode == '-qmls':
        if not qmls(container, iid):
            sys.exit(1)
        return

    if mode == '-qmdel':
        if len(sys.argv) < 4:
            print_usage(sys.argv[0])
            sys.exit(1)
        name = sys.argv[3]
        if not qmdel(container, iid, name):
            sys.exit(1)
        return

    print_usage(sys.argv[0])
    sys.exit(1)


if __name__ == '__main__':
    main()
