# 贡献指南

本仓库只有 Waveshare ESP32-S3-Touch-LCD-3.49 V2 / Rev1.1 这一硬件目标。
新增功能应保持 Windows 托盘优先，不因普通划选发送弹出主窗口。

提交前运行 PC 单元测试，修改触屏状态逻辑时运行纯 C 测试，修改固件时完整构建：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe tools/test-device-logic.py
powershell -ExecutionPolicy Bypass -File .\tools\build-touch349.ps1
```

修改字体时另运行 `tools/check-touch-font.py`。真实设备验收需先备份，测试只操作自有临时内容，不得清空或覆盖个人便签、新闻、显示设置、阅读状态或累计提示。

不要提交 `data/`、`logs/`、`backups/`、密钥、微信凭据、设备地址、个人偏好档案、截图原文或 Flash 镜像。PR 说明实际行为、验证结果及尚未验证部分。

原创代码贡献按 Apache-2.0 提交；修改第三方代码和字形时保留对应版权、许可、来源和修改说明。
