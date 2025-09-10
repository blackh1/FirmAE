#!/bin/bash

docker build -t fcore ./core
echo "[*] Completed initializing core docker"

# 修复Python脚本的shebang行
echo "[*] Fixing Python shebangs..."
if [ -d "sources" ]; then
    find sources/ -name "*.py" -type f -exec sed -i '1s|#!/.*python.*|#!/usr/bin/python3|' {} \;
    echo "[*] Fixed Python shebangs in sources/ directory"
fi

# 检查其他可能需要修复的Python文件
for dir in scripts util analyses; do
    if [ -d "$dir" ]; then
        find "$dir" -name "*.py" -type f -exec sed -i '1s|#!/.*python.*|#!/usr/bin/python3|' {} \; 2>/dev/null
        echo "[*] Fixed Python shebangs in $dir/ directory"
    fi
done

echo "[*] All Python shebangs updated to use #!/usr/bin/python3"