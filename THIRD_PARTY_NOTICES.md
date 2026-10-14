# 第三方许可与来源

USB 显示协议参考 Blackwood416/MSU_Mini_UsbScreen：
https://github.com/Blackwood416/MSU_Mini_UsbScreen

核对提交：ae303c6b783c7f5b3019d5993ce2438f9704e906。
实际压缩字节流与本地官方 MSU2_MINI_DemoV1.6 程序静态核对。
USB 六字节短包的传输方式也参考 SadYuyuko/MSU2-USB-Screen-Android 的实现思路。
https://github.com/SadYuyuko/MSU2-USB-Screen-Android

Android 参考项目采用 GPLv3。这里只对照协议描述与分包策略，程序实现独立编写。

以下保留 MSU_Mini_UsbScreen 的 MIT 许可：

MIT License

Copyright (c) 2025 Blackwood416

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

运行依赖包括 Python、Pillow、pyserial、pystray 和 six；便携版 licenses 文件夹保留其许可。
PyInstaller 使用其 GPL 引导程序例外条款打包程序，许可随包提供。
Windows 中文字体由本机系统加载，本项目不重新分发微软雅黑等系统字体。
原厂演示程序与 PDF 属于用户提供的参考资料，不包含在本项目便携包中。

工具识别图标来自本机安装的官方应用资源：
- Codex：OpenAI.Codex Windows 应用的 OpenAI 标志。
- Claude：Claude Windows 应用的 Claude 星形标志。

图标仅用于识别对应任务来源；标志和相关商标属于 OpenAI 与 Anthropic，图标不受本项目 USB 协议的 MIT 许可覆盖。

新增 Zstandard 解码使用 python-zstandard，许可随便携包附带。
只读桌面观察器使用 Windows 自带的 .NET Framework UI Automation 系统库，源码由本项目独立编写。
其他客户端图标仅在本机提取并保存在用户数据目录，不随源码或发布包分发；名称缩写为本项目绘制的识别占位符。
