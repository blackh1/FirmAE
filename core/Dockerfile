FROM ubuntu:20.04
LABEL maintainer="Mingeun Kim <pr0v3rbs@kaist.ac.kr>, Minkyo Seo <0xsaika@gmail.com>"

ENV DEBIAN_FRONTEND=noninteractive
ENV USER=root
ENV FIRMAE_DOCKER=true

# ===== 基础依赖 =====
RUN apt-get update && \
    apt-get install -y apt-utils curl wget tar bc psmisc ruby telnet git && \
    apt-get install -y socat net-tools iputils-ping iptables iproute2 && \
    apt-get install -y python3 python3-pip && \
    apt-get install -y libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# 升级 pip 并安装 uv
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install uv && \
    uv pip install --system psycopg2 psycopg2-binary

# ===== 其他工具 =====
RUN apt-get update && \
    apt-get install -y busybox-static bash-static fakeroot kpartx netcat-openbsd nmap \
    python3-psycopg2 snmp uml-utilities util-linux vlan && \
    apt-get install -y mtd-utils gzip bzip2 tar arj lhasa p7zip p7zip-full cabextract \
    fusecram cramfsswap squashfs-tools sleuthkit default-jdk cpio lzop lzma srecord \
    zlib1g-dev liblzma-dev liblzo2-dev unrar python3-magic openjdk-8-jdk

# ===== 自定义 Binwalk 安装（你的 repo）=====
RUN uv pip install --system git+https://github.com/blackh1/binwalk.git
RUN uv pip install --system python-lzo cstruct ubi_reader

# ===== QEMU =====
RUN apt-get install -y qemu-system-arm qemu-system-mips qemu-system-x86 qemu-utils

# ===== 分析工具 =====
RUN uv pip install --system selenium bs4 requests future paramiko pysnmp==4.4.6 pycryptodome

# ===== Google Chrome =====
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo 'deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y google-chrome-stable ntfs-3g && \
    ln -s /bin/ntfs-3g /bin/mount.ntfs-3g

# ===== ChromeDriver =====
RUN mkdir -p analyses && \
    wget https://chromedriver.storage.googleapis.com/2.38/chromedriver_linux64.zip && \
    unzip chromedriver_linux64.zip -d ./analyses/ && \
    rm -f chromedriver_linux64.zip

# ===== Routersploit 依赖（可选）=====
COPY analyses/routersploit/requirements.txt /tmp/routersploit_requirements.txt
RUN test -f /tmp/routersploit_requirements.txt && uv pip install --system -r /tmp/routersploit_requirements.txt || true

# ===== Routersploit 补丁（可选）=====
COPY analyses/routersploit_patch /tmp/routersploit_patch
RUN test -f /tmp/routersploit_patch && cd ./analyses/routersploit && patch -p1 < ../routersploit_patch || true

# ===== sudo 脚本 & 目录结构 =====
COPY sudo /usr/bin/sudo
RUN chmod 777 /usr/bin/sudo
RUN mkdir -p /work/FirmAE /work/firmwares
COPY unstuff /usr/local/bin/
RUN chmod +x /usr/local/bin/unstuff

# ===== PostgreSQL 安装（不初始化）=====
RUN apt-get update && \
    apt-get install -y postgresql && \
    rm -rf /var/lib/apt/lists/*

# 容器启动后才能初始化数据库，参见 docker-init.sh
