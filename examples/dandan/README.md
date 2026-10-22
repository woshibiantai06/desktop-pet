# 诞诞：实际完成的 Windows 桌宠示例

这里是诞诞 **0.3.2 的真实 Python / PySide6 应用源码**，包含它的姿态路线、动作调度、透明窗口、鼠标互动和键盘联动。它作为制作结果示例，与仓库里的照片到动作规划 Skill 配合使用。替换角色时需要按新宠物的素材重新整理配置；运行这份示例不会自动生成新视频。

![诞诞动作素材示意](preview.png)

## 只想使用成品

前往 [诞诞 0.3.2 发布页](https://github.com/woshibiantai06/desktop-pet/releases/tag/dandan-v0.3.2)，下载 **dandan-0.3.2-windows-x64.zip**。在 Windows 11 x64 上完整解压，运行其中的 `诞诞桌宠.exe`，保留 `_internal` 文件夹。成品不需要安装 Python。

发布 ZIP 为原先完成并验证的完整便携包，只更改下载文件名，文件内容不变：

```text
文件大小：943479014 bytes
SHA256：cb8e1ed69e2268a3b16d211d5a9a37542d8bfc7776fb979030ab23c85d0e286a
```

源码仓库没有放入约 2.84 GB 的动画数组、EXE 和随成品分发的运行依赖。克隆源码后必须准备动画资源，才能运行完整桌宠。

## 从源码运行

以下命令在 Windows PowerShell、仓库根目录执行。本例已在 Python 3.13、Windows 11 x64 验证；下面使用锁定的依赖版本。其他 Python 或系统版本需另行测试。

```powershell
cd examples/dandan
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-tested.txt
.venv\Scripts\python.exe prepare_assets.py "你的下载目录\dandan-0.3.2-windows-x64.zip"
.venv\Scripts\python.exe pet_alpha.py --interactive
```

`prepare_assets.py` 只使用 Python 标准库。下载 ZIP 由使用者自行选择和提供，脚本会核对整包 SHA256，读取发布包里的同一份 `config.json`，检查 40 个数组的类型、尺寸、帧范围、裁切还原信息和动作路线，再提取配置所需的资源。它不联网、不运行 ZIP 中的 EXE 或脚本、不提取 DLL，也不会覆盖已经修改过的配置或素材。需要约 2.85 GB 额外空间。

如只想核对下载包、配置和本地已有素材：

```powershell
.venv\Scripts\python.exe prepare_assets.py "你的下载目录\dandan-0.3.2-windows-x64.zip" --check-only
```

需要无终端启动时，在依赖和素材准备完毕后使用 `.venv\Scripts\pythonw.exe launch_pet.pyw --interactive`。启动日志在本示例目录的 `logs/launcher.log`；可用 `DOGPET_DATA_DIR` 指定输出目录，`DOGPET_CACHE_DIR` 指定可丢弃帧缓存目录。

## 包含哪些动作和互动

- 基础姿态：正面站立、坐姿、抬头趴姿、低头枕爪，以及自然坐下、趴下、抬头、起身。
- 日常动作：舔鼻、歪头、汪叫、挠耳、左右看、侧身待机、嗅地、抬头看等。
- 主人互动：三种姿态双击头部回应；左右拖动时进入对应走路动作；持续打字时进入敲键盘循环，停止后收尾。
- 陪伴与生命周期：自然陪伴、全动作演示、自由活动模式、出场和正常离场。

键盘联动只判断聚合活动和按住状态，不保存输入文字；可在菜单关闭。自由活动期间屏蔽特殊互动，仍保留拖动、菜单和正常退出。

`config.json` 有 57 个播放片段，对应 40 组来源素材。走路和打字的一段连续来源视频会拆成进入、循环、收尾三个区间；倒放片段与静帧停留会复用同一数组。每段标有 `start` / `end` 姿态，不能绕过路线直接换姿态。

## 精确的资源结构

```text
examples/dandan/
  pet_alpha.py, launch_pet.pyw, ...  # 实际应用模块
  config.json                      # 与 0.3.2 发布包一致的便携配置
  assets.lock.json                  # 发布整包及 43 个资源的大小、哈希
  prepare_assets.py                 # 经核对后提取白名单资源
  runtime/
    asset_001.npy ... asset_040.npy # 预乘 Alpha，uint8，形状 N×H×W×4
    app.ico
    app_icon.png
    stand_idle_first.png
```

数组是发布包中的无损裁边存储，不是原始 MOV。配置中的 `crop_rect` 与 `canvas_size` 负责还原到完整透明画布，`storage_shape` 记录存储尺寸。不要拿工作过程中的未裁边 NPZ 替换这些数组，或只更换文件名而保留旧配置。`assets.lock.json` 是完整资源清单。

## 验证与源码范围

```powershell
.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py"
```

保留了 152 项实际应用单元回归，覆盖动作优先级、姿态路径、取消和恢复、自然活动节奏、键盘活动判断、缓存和启动器。另有 16 项准备脚本测试，覆盖路径穿越、符号链接、大小写重复、对象数组、缺失素材、非法帧范围、配置修改与哈希失败。单元测试使用小型合成数组或替代 I/O，不要求下载完整动画，也不证明所有素材视觉衔接完美。

本次整理后，168 项测试全部通过；已用准备脚本从核对过的完整 ZIP 实际提取全部 43 个资源，并在新源码目录启动应用自检。实际窗口与帧渲染完成 40 个来源动作、三种姿态摸头、左右拖动、打字取消与恢复、自由活动和正常退出，结果通过。该自检加速推进素材时钟、向本应用发送 Qt 测试事件并关闭原生键盘轮询，因此不能当作正常速度性能测量、长期运行或另一台电脑上的完整实测。

相对本机工作版的整理只有：移除私人来源绝对路径；使用同一版发布包的裁边数组配置；将开发时指定磁盘的缓存默认位置改为应用数据目录。窗口、渲染、调度和互动逻辑直接保留，未换成另外编写的演示程序。完整便携 ZIP 不随这些源码整理而重打包。

## 素材与第三方依赖

本示例源码适用仓库根目录 MIT 许可。角色参考、AI 生成图片、动作素材以及发行包中的第三方依赖保留其来源权利和原有适用条款；根目录 MIT 不自动重新授权这些内容，也不附加新的商业授权承诺。制作自己的宠物时，请使用有权使用的照片和素材，并核对所用生成服务条款。发布包包含原有第三方许可文件。
