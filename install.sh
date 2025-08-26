#!/bin/bash

sudo apt update
sudo apt install -y curl wget tar git ruby python3 python3-pip bc
# pip 升级不再需要，uv 会自动管理
sudo -u $USER uv pip install coloredlogs

# for docker
sudo apt install -y docker.io
sudo groupadd docker
sudo usermod -aG docker $USER

# postgresql
sudo apt install -y postgresql
sudo /etc/init.d/postgresql restart
sudo -u postgres bash -c "psql -c \"CREATE USER firmadyne WITH PASSWORD 'firmadyne';\""
sudo -u postgres createdb -O firmadyne firmware
sudo -u postgres psql -d firmware < ./database/schema
echo "listen_addresses = '172.17.0.1,127.0.0.1,localhost'" | sudo -u postgres tee --append /etc/postgresql/*/main/postgresql.conf
echo "host all all 172.17.0.1/24 trust" | sudo -u postgres tee --append /etc/postgresql/*/main/pg_hba.conf

sudo apt install -y libpq-dev
uv pip install psycopg2 psycopg2-binary

sudo apt install -y busybox-static bash-static fakeroot dmsetup kpartx netcat-openbsd nmap python3-psycopg2 snmp uml-utilities util-linux vlan

# for binwalk
sudo -u $USER /home/user/.local/bin/uv pip install git+https://github.com/blackh1/binwalk.git
sudo apt install -y mtd-utils gzip bzip2 tar arj lhasa p7zip p7zip-full cabextract fusecram cramfsswap squashfs-tools sleuthkit default-jdk cpio lzop lzma srecord zlib1g-dev liblzma-dev liblzo2-dev unzip

cd - # back to root of project

sudo cp core/unstuff /usr/local/bin/

uv pip install python-lzo cstruct ubi_reader
sudo apt install -y python3-magic openjdk-8-jdk unrar

# for analyzer, initializer
sudo apt install -y python3-bs4
uv pip install selenium
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i google-chrome-stable_current_amd64.deb; sudo apt -fy install
rm google-chrome-stable_current_amd64.deb
uv pip install -r ./analyses/routersploit/requirements.txt
cd ./analyses/routersploit && patch -p1 < ../routersploit_patch && cd -

# for qemu
sudo apt install -y qemu-system-arm qemu-system-mips qemu-system-x86 qemu-utils

if ! test -e "./analyses/chromedriver"; then
    wget https://chromedriver.storage.googleapis.com/2.38/chromedriver_linux64.zip
    unzip chromedriver_linux64.zip -d ./analyses/
    rm -rf chromedriver_linux64.zip
fi
