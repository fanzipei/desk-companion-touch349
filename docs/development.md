# 构建、验证与部署

## PC

需要 Windows 10/11、带 Tkinter 的 Python 3.12–3.14，以及 BLE。

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-pc.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions 在 Windows 上运行隔离 PC 测试，不连接个人设备、微信或模型 API。`pc/hardware.py` 在时钟和数据同步前校验触屏型号；广播名称仍保留 `DeskCompanion`，请在多设备环境中指定正确地址。

## 唯一固件目标

`firmware/` 是独立 ESP-IDF 工程；其中 `main/` 和 `components/` 已包含所有自有源码及本地板级组件。ESP-IDF 组件管理器按 `main/idf_component.yml` 和 `dependencies.lock` 获取 LVGL、AXS15231B 和 TCA9554 驱动。

推荐 ESP-IDF **5.3.2**，ESP32-S3 工具链及 Python 环境通过其官方安装器建立。使用已激活的 ESP-IDF PowerShell 时，最好在英文路径克隆仓库，然后运行：

```powershell
cd firmware
idf.py set-target esp32s3
idf.py build
```

若使用用户目录下 `esp/esp-idf`、`esp/tools` 的标准布局，也可在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\build-touch349.ps1
```

该包装脚本将当前仓库的固件复制到英文临时路径构建，输出在 `firmware/dist/`，**只构建，不刷写**；不会读取邻接项目的固件或组件。

## 刷写前核验与备份

确认开发板标签为 ESP32-S3-Touch-LCD-3.49 V2 / Rev1.1，并通过设备管理器确定自己的端口。不要照抄另一台机器的 COM 口或 MAC。

在激活的 ESP-IDF 环境中，用自己的端口读取芯片身份并备份（以下 `COM_PORT` 是占位符）：

```powershell
python -m esptool --chip esp32s3 --port COM_PORT read_mac
python tools/backup-device.py --port COM_PORT --output backups/touch349-full.bin
```

备份工具读取完整 16 MB Flash、校验摘要，不覆盖已有备份；镜像包含个人数据，不可上传。备份前退出 PC 托盘后台，避免 BLE 和用户操作在备份期间更新存储。

首次安装，在已确认的板卡上从 `firmware/` 执行 `idf.py -p COM_PORT flash`。正常升级保持同一分区表；不要执行 `erase-flash`。原便签布局和 NVS 键保留，以兼容已经部署的 Touch349。刷写完成后先检查现有内容，再启动 PC。

包装脚本的构建目录为 `%TEMP%/desk-touch349-standalone-build`。在同一个 ESP-IDF 环境中可用 `idf.py -B BUILD_DIRECTORY -p COM_PORT flash` 对其构建产物执行同样操作。

若把 PC 现有缓存迁移到一块已刷入本项目但尚未存放便签的新屏，先运行 `tools/migrate-touch349.py --help`，指定自己的 BLE 地址。该工具验证板型、保存迁移前备份并检查内容冲突；不要把它作为普通同步的替代品。

## 字库与状态逻辑

`firmware/main/touch_fallback.bin` 是可公开分发的字体资源，原始字体和产物的 SHA256 见相邻 JSON。普通构建直接使用它，开发依赖不进入 PC 后台。

```powershell
.\.venv\Scripts\python.exe -m pip install -r tools/requirements-fonts.txt
.\.venv\Scripts\python.exe tools/make-touch-fallback-font.py path/to/NotoSansCJKsc-Regular.otf
.\.venv\Scripts\python.exe tools/check-touch-font.py
```

离线字形测试编译生产适配器，需要 GCC；在 Windows 使用 MSYS2 UCRT64。它检查四种字号、间隔号、扩展汉字、特殊空格、不可见符号、双倍像素和可选本机内容的缺字情况，只导出合成样例图。C 状态逻辑测试位于 `tests/*_test.c`，可用 `tools/test-device-logic.py` 编译执行；均不需要实体小屏。

## 目录边界与隐私

源仓库不包含实际密钥、微信会话、已学习的个人偏好、便签、新闻队列、设备地址、串口日志、原始截图或 Flash 备份。只有 `pc/news_preference_skill.md` 的通用偏好模板随代码分发。

PC 进程锁和运行数据属于本克隆的触屏应用，不沿用其他版本的锁名称。系统全局快捷键仍应避免由多个应用同时注册。修改协议、字体或存储后，应构建固件并在自己的目标板上核验；测试不得覆盖个人便签、新闻和显示设置。
