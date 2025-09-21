#!/usr/bin/env python3

import sys
import threading
import subprocess as sp
import time
import os
import signal
import scripts.util as util
import multiprocessing as mp
import logging, coloredlogs

# 设置日志
coloredlogs.install(level=logging.DEBUG)
coloredlogs.install(level=logging.INFO)

class docker_helper:
    def __init__(self, firmae_root, remove_image=False, docker_image="fcore", auto_remove=False, with_dev=False, network_mode=None):
        self.firmae_root = firmae_root
        self.count = 0
        self.last_core = None
        self.docker_image = docker_image
        self.__sync_status()
        self.remove_image = remove_image
        # whether to run containers with --rm
        self.auto_remove = auto_remove
        # whether to bind host /dev into container
        self.with_dev = with_dev
        # docker network mode, e.g., 'host'
        self.network_mode = network_mode

    def __sync_status(self):
        containers = self.get_container_list(with_pause=True)
        self.count = len(containers)
        logging.debug("[*] current core : {}".format(self.count))

    def get_container_list(self, with_pause=False, all_containers=False):
        """获取容器列表，all_containers=True时包括停止的容器"""
        try:
            cmd = ["docker", "ps", "-a"] if all_containers else ["docker", "ps"]
            # 仅输出名称，避免解析列宽问题
            cmd += ["--format", "{{.Names}} {{.Status}}"]
            result = sp.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logging.error("[-] Docker command failed: {}".format(result.stderr))
                return []
            lines = [l for l in result.stdout.split('\n') if l.strip()]
        except Exception as e:
            logging.error("[-] Error getting container list: {}".format(e))
            return []
        
        ret = []
        for line in lines:
            try:
                name, status = line.split(' ', 1)
            except ValueError:
                name, status = line.strip(), ''
            if not with_pause and 'Paused' in status:
                continue
            if name:
                ret.append(name)

        return ret[::-1]

    def check_existing_container(self, firmware):
        """检查是否已经存在该固件的容器（包括停止的）"""
        import re
        sanitized = firmware.replace('/', '_').replace(' ', '_').replace('.', '_')
        # 匹配 docker<number>_<sanitized_firmware>
        pattern = re.compile(rf"^docker\d+_{re.escape(sanitized)}$")
        
        # 首先检查运行中的容器
        containers = self.get_container_list()
        for container in containers:
            if pattern.match(container):
                logging.info("[+] Found running container: {}".format(container))
                return container, "running"
        
        # 检查所有容器（包括停止的）
        all_containers = self.get_container_list(all_containers=True)
        for container in all_containers:
            if pattern.match(container):
                logging.info("[+] Found stopped container: {}".format(container))
                return container, "stopped"
        
        return None, None

    def start_stopped_container(self, container_name):
        """启动已停止的容器"""
        try:
            result = sp.run(["docker", "start", container_name], capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("[+] Container {} started successfully".format(container_name))
                return True
            else:
                logging.error("[-] Failed to start container {}: {}".format(container_name, result.stderr))
                return False
        except Exception as e:
            logging.error("[-] Exception starting container {}: {}".format(container_name, e))
            return False

    def remove_container(self, container_name):
        """删除容器"""
        try:
            result = sp.run(["docker", "rm", "-f", container_name], capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("[+] Container {} removed successfully".format(container_name))
                return True
            else:
                logging.error("[-] Failed to remove container {}: {}".format(container_name, result.stderr))
                return False
        except Exception as e:
            logging.error("[-] Exception removing container {}: {}".format(container_name, e))
            return False

    def get_container_ip(self, container_name):
        """获取容器的IP地址"""
        try:
            cmd = ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container_name]
            result = sp.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logging.error("[-] Failed to get container IP: {}".format(e))
        return None

    def setup_network_access(self, container_name):
        """设置网络访问，包括端口转发和路由"""
        container_ip = self.get_container_ip(container_name)
        if not container_ip:
            logging.error("[-] Could not get container IP")
            return
        
        logging.info("[+] Container IP: {}".format(container_ip))
        
        # 添加到固件设备的路由（假设固件IP是192.168.0.1）
        try:
            # 先删除可能存在的旧路由
            sp.run(["sudo", "ip", "route", "del", "192.168.0.1/32"], capture_output=True)
            
            # 添加新路由
            result = sp.run(["sudo", "ip", "route", "add", "192.168.0.1/32", "via", container_ip], 
                          capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("[+] Added route to firmware device: 192.168.0.1 via {}".format(container_ip))
            else:
                logging.warning("[!] Failed to add route: {}".format(result.stderr))
        except Exception as e:
            logging.error("[-] Exception setting up route: {}".format(e))
        
        # 设置端口转发
        self.setup_port_forwarding(container_name, container_ip)
        
        # 显示访问信息
        logging.info("\n" + "="*60)
        logging.info("[+] Firmware Access Information:")
        logging.info("="*60)
        logging.info("    Direct access (if route is working):")
        logging.info("      - http://192.168.0.1")
        logging.info("      - telnet 192.168.0.1")
        logging.info("    ")
        logging.info("    Via container IP:")
        logging.info("      - http://{}:8080 (forwarded to 192.168.0.1:80)".format(container_ip))
        logging.info("      - http://{}:8443 (forwarded to 192.168.0.1:443)".format(container_ip))
        logging.info("      - telnet {} 2323 (forwarded to 192.168.0.1:23)".format(container_ip))
        logging.info("      - gdb {} 11337 (forwarded to 192.168.0.1:1337)".format(container_ip))
        logging.info("    ")
        logging.info("    Container shell access:")
        logging.info("      - docker exec -it {} bash".format(container_name))
        logging.info("="*60 + "\n")

    def setup_port_forwarding(self, container_name, container_ip):
        """在容器内设置端口转发"""
        # 首先检查socat是否已安装
        check_socat = ["docker", "exec", container_name, "which", "socat"]
        result = sp.run(check_socat, capture_output=True)
        
        if result.returncode != 0:
            logging.info("[*] Installing socat in container...")
            install_cmd = ["docker", "exec", container_name, "bash", "-c", 
                          "apt-get update > /dev/null 2>&1 && apt-get install -y socat > /dev/null 2>&1"]
            sp.run(install_cmd, capture_output=True)
        
        # 端口映射配置
        port_mappings = [
            (8080, 80, "HTTP"),
            (8443, 443, "HTTPS"),
            (2323, 23, "Telnet"),
            (2222, 22, "SSH"),
            (11337,1337,"GDB")
        ]
        
        for listen_port, target_port, service in port_mappings:
            # 先检查端口是否已经在转发
            check_cmd = ["docker", "exec", container_name, "pgrep", "-f", "socat.*{}".format(listen_port)]
            if sp.run(check_cmd, capture_output=True).returncode == 0:
                logging.debug("[*] {} forwarding already running".format(service))
                continue
            
            forward_cmd = [
                "docker", "exec", "-d", container_name,
                "socat", 
                "TCP-LISTEN:{},fork,reuseaddr".format(listen_port),
                "TCP:192.168.0.1:{}".format(target_port)
            ]
            
            try:
                result = sp.run(forward_cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    logging.debug("[+] {} forwarding: {}:{} -> 192.168.0.1:{}".format(
                        service, container_ip, listen_port, target_port))
                else:
                    logging.warning("[!] Failed to setup {} forwarding: {}".format(service, result.stderr))
            except Exception as e:
                logging.warning("[!] Exception setting up {} forwarding: {}".format(service, e))

    def stop_core(self, container_name):
        try:
            result = sp.run("docker stop {}".format(container_name), shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("[+] Container {} stopped successfully".format(container_name))
                return result.stdout
            else:
                logging.error("[-] Failed to stop container {}: {}".format(container_name, result.stderr))
        except Exception as e:
            logging.error("[-] Exception stopping container {}: {}".format(container_name, e))
            return None

    def run_core(self, idx, mode, brand, firmware_path):
        firmware_root = os.path.dirname(firmware_path)
        firmware = os.path.basename(firmware_path)
        docker_name = 'docker{}_{}'.format(idx, firmware.replace('/', '_').replace(' ', '_').replace('.', '_'))
        
        # 首先检查容器是否已经存在（无论运行还是停止）
        try:
            # 检查容器是否存在
            check_cmd = ["docker", "ps", "-a", "--filter", f"name={docker_name}", "--format", "{{.Names}}"]
            result = sp.run(check_cmd, capture_output=True, text=True)
            
            if result.stdout.strip():
                # 容器存在，检查状态
                status_cmd = ["docker", "ps", "--filter", f"name={docker_name}", "--format", "{{.Names}}"]
                status_result = sp.run(status_cmd, capture_output=True, text=True)
                
                if status_result.stdout.strip():
                    # 容器正在运行
                    logging.info("[+] Container {} is already running".format(docker_name))
                    self.setup_network_access(docker_name)
                    
                    if mode == "-d":
                        logging.info("[*] Attaching to existing container...")
                        attach_cmd = ["docker", "exec", "-it", docker_name, "bash"]
                        try:
                            sp.run(attach_cmd)
                        except KeyboardInterrupt:
                            logging.info("[*] Detached from container")
                    
                    return docker_name
                else:
                    # 容器已停止
                    logging.info("[*] Found stopped container: {}".format(docker_name))
                    response = input("[?] Container exists but is stopped. (s)tart, (r)emove and recreate, or (q)uit? [s]: ").lower() or 's'
                    
                    if response == 's':
                        if self.start_stopped_container(docker_name):
                            time.sleep(5)
                            self.setup_network_access(docker_name)
                            
                            if mode == "-d":
                                attach_cmd = ["docker", "exec", "-it", docker_name, "bash"]
                                try:
                                    sp.run(attach_cmd)
                                except KeyboardInterrupt:
                                    logging.info("[*] Detached from container")
                            
                            return docker_name
                    elif response == 'r':
                        logging.info("[*] Removing existing container...")
                        self.remove_container(docker_name)
                        # 继续创建新容器
                    else:
                        logging.info("[*] Exiting...")
                        return None
                        
        except Exception as e:
            logging.error("[-] Error checking container existence: {}".format(e))
        # 创建新容器
        logging.info("[*] Starting new container {} for firmware {}".format(docker_name, firmware))
        
        # 检查Docker镜像是否存在
        try:
            result = sp.run("docker images -q {}".format(self.docker_image), shell=True, capture_output=True, text=True)
            if not result.stdout.strip():
                logging.error("[-] Docker image '{}' not found. Please build the image first.".format(self.docker_image))
                return docker_name
        except Exception as e:
            logging.error("[-] Error checking docker image: {}".format(e))
            return docker_name
        
        # 构建docker run命令（默认不使用 --rm，避免 stop 后容器被删除，便于 checkpoint/restore）
        cmd = [
            "docker", "run", "-dit",
        ]

        if self.auto_remove:
            cmd.append("--rm")

        # network mode
        if self.network_mode:
            cmd.extend(["--network", self.network_mode])

        # bind mounts and security options
        cmd.extend([
            "-v", "{}:/work/FirmAE".format(self.firmae_root),
            "-v", "{}:/work/firmwares".format(firmware_root),
        ])

        if self.with_dev:
            cmd.extend(["-v", "/dev:/dev"])

        # Avoid CRIU unsupported hugetlbfs; attempt to override via tmpfs
        cmd.extend([
            "--mount", "type=tmpfs,destination=/dev/hugepages",
            # Relax seccomp to avoid unexpected denials during checkpoint
            "--security-opt", "seccomp=unconfined",
            "--privileged=true",
            "--name", docker_name,
            self.docker_image
        ])

        logging.debug("[*] Docker command: {}".format(' '.join(cmd)))

        try:
            result = sp.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logging.error("[-] Failed to start container {}: {}".format(docker_name, result.stderr))
                return docker_name
            
            logging.info("[+] Container {} started successfully".format(docker_name))
            logging.debug("[*] Container ID: {}".format(result.stdout.strip()))
            
        except Exception as e:
            logging.error("[-] Exception starting container {}: {}".format(docker_name, e))
            return docker_name

        time.sleep(5)

        # 检查容器是否真的在运行
        try:
            result = sp.run("docker ps --filter name={}".format(docker_name), shell=True, capture_output=True, text=True)
            if docker_name not in result.stdout:
                logging.error("[-] Container {} is not running after start".format(docker_name))
                return docker_name
        except Exception as e:
            logging.error("[-] Error checking container status: {}".format(e))

        # 初始化PostgreSQL
        init_db_cmd = [
            "docker", "exec", docker_name, "bash", "-c",
            "service postgresql start && sleep 2 && sudo -u postgres createdb firmware || true && sudo -u postgres psql -d firmware -c \"CREATE EXTENSION IF NOT EXISTS pgcrypto;\" || true"
        ]
        
        try:
            result = sp.run(init_db_cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logging.debug("[+] PostgreSQL initialized for {}".format(docker_name))
            else:
                logging.warning("[!] PostgreSQL initialization warning for {}: {}".format(docker_name, result.stderr))
        except Exception as e:
            logging.warning("[!] PostgreSQL initialization exception for {}: {}".format(docker_name, e))

        # 执行分析命令
        docker_mode = "-it" if mode == "-d" else "-id"
        exec_cmd = [
            "docker", "exec", docker_mode, docker_name, "bash", "-c",
            "cd /work/FirmAE && ./run.sh {} {} /work/firmwares/{}".format(mode, brand, firmware)
        ]

        if mode == "-d":
            # 交互模式
            logging.info("[*] Starting interactive mode for {}".format(docker_name))
            
            # 设置网络访问（等待一段时间让固件启动）
            logging.info("[*] Waiting for firmware to start...")
            time.sleep(10)
            self.setup_network_access(docker_name)
            
            try:
                sp.run(exec_cmd)
            except KeyboardInterrupt:
                logging.info("[*] Interactive mode interrupted")
            return docker_name
        else:
            # 后台模式
            log_file = "/work/FirmAE/scratch/{}.log".format(firmware)
            exec_cmd[-1] += " 2>&1 > {} &".format(log_file)
            
            try:
                result = sp.run(exec_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logging.error("[-] Failed to execute analysis in {}: {}".format(docker_name, result.stderr))
                    
                # 等待固件启动并设置网络
                logging.info("[*] Waiting for firmware to start...")
                time.sleep(20)
                self.setup_network_access(docker_name)
                
            except Exception as e:
                logging.error("[-] Exception executing analysis in {}: {}".format(docker_name, e))

        return docker_name

    def run_command(self, core, cmd):
        result = '[-] failed'
        try:
            result = sp.run('docker exec -it {} bash -c "{}"'.format(core, cmd), shell=True, capture_output=True, text=True)
            return result.stdout if result.returncode == 0 else result.stderr
        except Exception as e:
            logging.error("[-] Failed to run command in {}: {}".format(core, e))
        return result

    def create_checkpoint(self, container_name, checkpoint_name="warm1", leave_running=True, checkpoint_dir=None):
        """为运行中的容器创建 checkpoint（需要 Docker daemon 开启 Experimental）。"""
        # 预检：daemon Experimental
        try:
            info = sp.run(["docker", "info", "--format", "{{.ExperimentalBuild}}"], capture_output=True, text=True)
            if info.returncode == 0 and info.stdout.strip().lower() not in ("true", "1"):
                logging.error("[-] Docker daemon experimental features are disabled. Enable 'experimental': true in /etc/docker/daemon.json and restart docker.")
                return False
        except Exception:
            pass

        # 预检：容器内是否存在 hugetlbfs 挂载（CRIU 不支持）
        try:
            mnt = sp.run(["docker", "exec", container_name, "sh", "-lc", "grep -w 'hugetlbfs' /proc/mounts || true"], capture_output=True, text=True)
            if mnt.returncode == 0 and mnt.stdout.strip():
                logging.error("[-] Detected hugetlbfs mount inside container (e.g., /dev/hugepages). Restart this container with '--mount type=tmpfs,destination=/dev/hugepages' (already default when using this helper). Re-run the workload, then checkpoint again.")
                return False
        except Exception:
            pass

        args = ["docker", "checkpoint", "create"]
        if checkpoint_dir:
            args += ["--checkpoint-dir", checkpoint_dir]
        if leave_running:
            args.append("--leave-running")
        args.extend([container_name, checkpoint_name])
        try:
            result = sp.run(args, capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("[+] Checkpoint '{}' created for {}".format(checkpoint_name, container_name))
                return True
            logging.error("[-] Failed to create checkpoint: {}".format(result.stderr.strip()))
        except Exception as e:
            logging.error("[-] Exception creating checkpoint: {}".format(e))
        return False

    def restore_checkpoint(self, container_name, checkpoint_name="warm1"):
        """停止容器并从 checkpoint 还原"""
        try:
            # 尝试停止（容器可能已停止）
            sp.run(["docker", "stop", container_name], capture_output=True)
        except Exception:
            pass

        try:
            result = sp.run(["docker", "start", "--checkpoint", checkpoint_name, container_name], capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("[+] Container {} restored from checkpoint '{}'".format(container_name, checkpoint_name))
                # 还原后重新设置路由/端口
                time.sleep(2)
                self.setup_network_access(container_name)
                return True
            logging.error("[-] Failed to restore checkpoint: {}".format(result.stderr.strip()))
        except Exception as e:
            logging.error("[-] Exception restoring checkpoint: {}".format(e))
        return False

def print_usage(argv0):
    print("[*] Usage:")
    print("  {} -ec [brand] [firmware_path]    # Extract and emulate".format(argv0))
    print("  {} -ea [brand] [firmware_path]    # Extract, emulate and analyze".format(argv0))
    print("  {} -ed [firmware_path]            # Extract and debug".format(argv0))
    print("  {} -er [firmware_path]            # Extract and run".format(argv0))
    print("  {} -d [brand] [firmware_path]     # Debug mode (same as -ed)".format(argv0))
    print("  {} -c [command]                   # Run command in all containers".format(argv0))
    print("  {} -s [script]                    # Run script in all containers".format(argv0))
    print("  {} -ckc [firmware_path] [name]    # Create checkpoint for container (default name: warm1)".format(argv0))
    print("  {} -ckr [firmware_path] [name]    # Restore container from checkpoint (default name: warm1)".format(argv0))
    print("  {} -reset [firmware_path] [clean] # Reset: kill QEMU, umount, restart run.sh (clean=use image.clean)".format(argv0))
    print("\n[Global options]\n  --rm                                # Auto-remove container on stop (default: keep)\n  --with-dev                          # Bind host /dev into container (required for some emulations)\n  --net=host                          # Use host network mode (may help checkpoint/restore)\n")

def runner(args):
    (idx, dh, mode, brand, firmware) = args
    if os.path.isfile(firmware):
        docker_name = dh.run_core(idx, mode, brand, firmware)
        if mode not in ["-d", "-r"]:  # 不在调试/运行模式时才自动停止
            dh.stop_core(docker_name)
    else:
        logging.error("[-] Can't find firmware file: {}".format(firmware))

def main():
    if len(sys.argv) < 2:
        print_usage(sys.argv[0])
        exit(1)

    # 解析全局选项（目前仅支持 --rm）
    auto_remove = False
    with_dev = False
    network_mode = None
    argv = []
    for a in sys.argv:
        if a == '--rm':
            auto_remove = True
            continue
        if a == '--with-dev':
            with_dev = True
            continue
        if a.startswith('--net='):
            network_mode = a.split('=', 1)[1]
            continue
        argv.append(a)

    sys.argv = argv

    firmae_root = os.path.abspath('.')
    docker_image = os.environ.get('FIRMAE_DOCKER_IMAGE', 'fcore')
    dh = docker_helper(firmae_root, remove_image=False, docker_image=docker_image, auto_remove=auto_remove, with_dev=with_dev, network_mode=network_mode)

    # 处理 -d 参数（调试模式）
    if sys.argv[1] == '-d':
        if len(sys.argv) < 4:
            print_usage(sys.argv[0])
            exit(1)

        if not os.path.exists(os.path.join(firmae_root, "scratch")):
            os.makedirs(os.path.join(firmae_root, "scratch"), exist_ok=True)

        brand = sys.argv[2]
        firmware_path = os.path.abspath(sys.argv[3])
        
        if os.path.isfile(firmware_path):
            argv = (0, dh, "-d", brand, firmware_path)
            runner(argv)
        else:
            logging.error("[-] Firmware file not found: {}".format(firmware_path))

    elif sys.argv[1] in ['-ec', '-ea']:
        if len(sys.argv) < 4:
            print_usage(sys.argv[0])
            exit(1)

        if not os.path.exists(os.path.join(firmae_root, "scratch")):
            os.makedirs(os.path.join(firmae_root, "scratch"), exist_ok=True)

        brand = sys.argv[2]
        mode = '-' + sys.argv[1][-1]
        firmware_path = os.path.abspath(sys.argv[3])
        
        if os.path.isfile(firmware_path) and not firmware_path.endswith('.list'):
            argv = (0, dh, mode, brand, firmware_path)
            runner(argv)
        else:
            logging.error("[-] Invalid firmware path: {}".format(firmware_path))

    elif sys.argv[1] in ['-er', '-ed']:
        if len(sys.argv) < 3:
            print_usage(sys.argv[0])
            exit(1)

        mode = '-' + sys.argv[1][-1]
        firmware_path = os.path.abspath(sys.argv[2])
        if os.path.isfile(firmware_path):
            argv = (0, dh, mode, "auto", firmware_path)
            runner(argv)
        else:
            logging.error("[-] Firmware file not found: {}".format(firmware_path))

    elif sys.argv[1] == '-c':
        if len(sys.argv) != 3:
            print_usage(sys.argv[0])
            exit(1)

        cmd = sys.argv[2]
        containers = dh.get_container_list()
        if not containers:
            logging.info("[*] No running containers found")
        for core in containers:
            print(core)
            print(dh.run_command(core, cmd))

    elif sys.argv[1] == '-ckc':
        # Create checkpoint for a container corresponding to the firmware file
        if len(sys.argv) < 3:
            print_usage(sys.argv[0])
            exit(1)

        firmware_path = os.path.abspath(sys.argv[2])
        checkpoint_name = sys.argv[3] if len(sys.argv) >= 4 else 'warm1'
        firmware = os.path.basename(firmware_path)

        container, status = dh.check_existing_container(firmware)
        if not container:
            logging.error("[-] No container found for firmware: {}".format(firmware))
            exit(1)

        if status == 'stopped':
            logging.info("[*] Container is stopped. Starting before checkpoint...")
            if not dh.start_stopped_container(container):
                exit(1)
            time.sleep(5)

        if not dh.create_checkpoint(container, checkpoint_name=checkpoint_name):
            exit(1)

    elif sys.argv[1] == '-ckr':
        # Restore container from a checkpoint
        if len(sys.argv) < 3:
            print_usage(sys.argv[0])
            exit(1)

        firmware_path = os.path.abspath(sys.argv[2])
        checkpoint_name = sys.argv[3] if len(sys.argv) >= 4 else 'warm1'
        firmware = os.path.basename(firmware_path)

        container, status = dh.check_existing_container(firmware)
        if not container:
            logging.error("[-] No container found for firmware: {}".format(firmware))
            exit(1)

        if not dh.restore_checkpoint(container, checkpoint_name=checkpoint_name):
            exit(1)

    elif sys.argv[1] == '-reset':
        # Reset emulated device: stop QEMU, umount image, optionally restore clean image, restart run.sh
        if len(sys.argv) < 3:
            print_usage(sys.argv[0])
            exit(1)

        firmware_path = os.path.abspath(sys.argv[2])
        use_clean = False
        if len(sys.argv) >= 4:
            use_clean = sys.argv[3].lower() in ("clean", "true", "1", "yes", "y")

        firmware = os.path.basename(firmware_path)
        base_noext = firmware.rsplit('.', 1)[0]

        container, status = dh.check_existing_container(firmware)
        if not container:
            logging.error("[-] No container found for firmware: {}".format(firmware))
            exit(1)

        # Ensure running
        if status == 'stopped':
            logging.info("[*] Starting stopped container {}".format(container))
            if not dh.start_stopped_container(container):
                exit(1)
            time.sleep(3)

        # Find IID by matching scratch/*/name equals base_noext
        find_iid_cmd = (
            "bash -lc 'set -e; for d in /work/FirmAE/scratch/*; do "
            "[ -f \"$d/name\" ] || continue; n=$(cat \"$d/name\"); "
            f"if [ \"$n\" = \"{base_noext}\" ]; then basename \"$d\"; exit 0; fi; done; exit 1'"
        )
        iid_proc = sp.run(["docker", "exec", container, "bash", "-lc",
                           f"for d in /work/FirmAE/scratch/*; do [ -f \"$d/name\" ] || continue; n=$(cat \"$d/name\"); if [ \"$n\" = \"{base_noext}\" ]; then basename \"$d\"; exit 0; fi; done; exit 1"],
                          capture_output=True, text=True)
        if iid_proc.returncode != 0:
            logging.error("[-] Could not find IID for firmware base '{}' in container {}".format(base_noext, container))
            exit(1)
        iid = iid_proc.stdout.strip().splitlines()[0]
        logging.info("[+] Reseting IID {} (firmware base {}) in {}".format(iid, base_noext, container))

        # Stop QEMU, umount
        sp.run(["docker", "exec", container, "bash", "-lc", "pkill -f qemu-system || true"], capture_output=True)
        time.sleep(1)
        sp.run(["docker", "exec", container, "bash", "-lc", f"/work/FirmAE/scripts/umount.sh {iid} || true"], capture_output=True)

        if use_clean:
            # Restore from clean baseline if present; if not present, create it now from current image.raw
            make_clean = (
                f"set -e; sd=/work/FirmAE/scratch/{iid}; "
                f"if [ ! -f \"$sd/image.clean\" ]; then cp --reflink=auto --sparse=always \"$sd/image.raw\" \"$sd/image.clean\"; fi; "
                f"cp --reflink=auto --sparse=always \"$sd/image.clean\" \"$sd/image.raw\""
            )
            r = sp.run(["docker", "exec", container, "bash", "-lc", make_clean], capture_output=True, text=True)
            if r.returncode != 0:
                logging.warning("[!] Failed to restore clean image: {}".format(r.stderr.strip()))

        # Restart firmware (background)
        runcmd = f"nohup /work/FirmAE/scratch/{iid}/run.sh >/work/FirmAE/scratch/{iid}/reset.log 2>&1 &"
        r = sp.run(["docker", "exec", container, "bash", "-lc", runcmd], capture_output=True, text=True)
        if r.returncode != 0:
            logging.error("[-] Failed to restart run.sh: {}".format(r.stderr.strip()))
            exit(1)
        logging.info("[+] Restarted firmware, waiting briefly...")
        time.sleep(5)
        try:
            dh.setup_network_access(container)
        except Exception:
            pass
        logging.info("[+] Reset complete. Tail logs with: docker exec -it {} bash -lc 'tail -f /work/FirmAE/scratch/{}/reset.log'".format(container, iid))

if __name__ == "__main__":
    main()
