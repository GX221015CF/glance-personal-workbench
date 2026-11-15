# 运行与接入

## 软件和目录

- Windows 10/11 x64，Python 3.12。个人授权使用 Windows 当前用户的 DPAPI。
- Glance v0.8.7 官方 Windows x64 发行包。`tools/install_glance.py` 下载并核验已记录的 SHA-256，再写入 `bin/glance.exe`。
- Node.js 24 用于回归测试。WorkBuddy MCP 另需该工具自身的有效安装和登录。
- Python 依赖固定在 `requirements.txt`，Node 测试依赖固定在 `package-lock.json`。

在仓库根目录按 README 创建虚拟环境并安装，执行 `python run.py`。Glance 使用 8180，桥接使用 8182，均绑定回环地址。启动器在前台等待，Ctrl+C 会清理本次启动的子进程。若任何端口已被占用，启动检查会退出。

```powershell
python run.py --offline --data-dir .local/review
python run.py --bridge-only --offline --data-dir .local/api-review
```

`--offline` 暂停桥接的后台轮询，学校和邮箱保留未授权状态。Glance 的 arXiv RSS 组件仍按其独立策略读取公开 RSS，手动刷新或打开外链也可能访问网络。该选项适合核对本地状态，完全断网时外部组件可能显示来源错误。

展示版默认数据根为 `.local`，也可通过 `--data-dir` 或 `WORKBENCH_DATA_DIR` 指定。直接执行 `app` 中的配置命令时，应使用与启动器相同的数据根。各存储都落在该目录，旧版账号存储的回退路径也限定在它的 `legacy` 子目录。

| 子目录或文件 | 用途 |
|---|---|
| `Google/` | Google 客户端及账号授权，DPAPI 加密 |
| `SchoolData/` | 学校会话和结果，DPAPI 加密 |
| `Integrations/IMA/` | ima 连接、映射与恢复记录，DPAPI 加密 |
| `Calendar/` | 个人事件，DPAPI 加密 |
| `wechat/` | 本次收录的文章、图片与 SQLite |
| `wechat-analysis/`、`wechat-summary/` | AI 任务与生成结果 |
| `bridge-cache/` | 最近成功的公开数据 |

## 公开信息

联网启动后，AIHOT 关注资讯、公众号公开 RSS、天气和行情按原有调度更新。关注词、订阅清单和布局在 `app/config` 中，均为可审阅的公开配置。校历 JSON 保存公开学校校历的整理结果，含来源链接与核验日期。

首次同步前显示等待或读取中。接口失败时保留已经成功取得的有效缓存，按模块显示更新时间、过期或来源错误。新数据目录没有历史缓存，外部源失败时会呈现不可用或空状态。行情刷新间隔与来源延迟各自反映在界面，价格用于信息观察。

## Gmail 与 YouTube

1. 在自己的 Google Cloud 项目创建桌面 OAuth 客户端，启用 Gmail API 与 YouTube Data API v3，按应用状态配置测试用户。
2. 将 Google 下载的客户端 JSON 放在仓库外，导入本地加密存储。

```powershell
python app/gmail_bridge.py import-client --file "D:\private\google-desktop-client.json"
```

3. 联网启动工作台，从 Gmail 或 YouTube 页面分别添加自己的账号并完成授权。

回调为 `http://127.0.0.1:8182/oauth/gmail/callback` 和 `http://127.0.0.1:8182/oauth/youtube/callback`。Gmail 申请邮件元数据权限，正文在 Gmail 原站查看，YouTube 申请只读权限。

自定义数据目录时，在该终端先设置 `$env:WORKBENCH_DATA_DIR` 为同一绝对路径，再执行导入和启动命令。默认空配置通过 HTTP 验证。真实 OAuth 授权和云端读取需使用者自己的配置，本批保留其验证范围。

## 学校连接

适配对象为上财教务、Canvas 和学业系统。需要本人正常登录学校系统、Edge 扩展和 Native Messaging Host。源码位于 `app/extensions/school-connector` 和 `app/school_connector`，页面匹配及桥接地址已调整为展示端口。

此初稿收录连接器源码与协议，默认启动提供“需要登录”状态。扩展使用独立 Host 名称 `com.personalworkbench.showcase_connector`，Native Messaging 的操作系统注册与扩展 ID 仍需按目标设备配置，现有日常 Host 注册属于另一实例。Host 的 `host-config.json` 需要填入安装后取得的扩展 ID，按 `verify_caller` 的版本1字段读取。本批验收覆盖导入、未登录接口和选定离线夹具。跨设备的扩展安装与真实三路登录仍待专项配置和验收。

## WorkBuddy 总结与解读

启动器把 `config/workbuddy-mcp.example.json` 和偏好示例复制到数据目录。编辑 `.local/workbuddy-mcp.json`，填入本机 WorkBuddy MCP 的 `server.js` 和 `node.exe` 绝对路径。模型、超时及回合数按文件中的字段配置。

需要可用的 WorkBuddy MCP 与其自身登录环境。初稿中的路径为空，发起 AI 任务会提示尚未配置有效入口。离线测试通过受控 MCP 夹具检查任务防重、完成、失败、Markdown 与 HTML 清理流程。

偏好功能默认关闭，偏好读取根限定在数据目录的 `workbuddy-preferences` 子目录。需要时可向该位置放入自己选择的偏好文件，并编辑数据目录内的配置。示例只用于说明字段。

## ima

需要使用者自己的官方客户端 ID、API Key、可写知识库权限，以及可以取得文件的学校会话。可运行 `python app/ima_probe.py` 按终端提示配置和检查，交互中涉及写入的步骤逐次说明目标。课程与资料页的保存由用户选择目标并确认。

未配置时接口返回 `configured=false`。上传、目录操作或结果不确定时采用原有结果回读和恢复规则。真实密钥、目标库和课程映射都由使用者在独立数据根建立。此初稿的同步验证使用合成夹具，详见案例和验证记录。

## 本机动作与桌面版

电脑模式按钮对应数据目录下 `desktop-actions/game.bat`、`office.bat`、`status.bat`。首稿未提供这些机器专属脚本，接口返回各动作不可用。需要者可自行配置并审阅脚本内容。

本地初稿的入口为浏览器版，Electron 构建和打包文件未收录。维护版本的桌面入口在项目经历中有单独验收记录。
