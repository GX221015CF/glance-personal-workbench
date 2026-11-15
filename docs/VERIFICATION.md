# 验证与审阅记录

验证日期为 2026 年 11 月 15 日。使用新建 Python 3.12 虚拟环境安装声明依赖后复跑，Python 217 项、Node 186 项全部通过。

| 检查 | 实际结果 |
|---|---|
| 后台导入与数据路径 | 核心模块成功导入，默认存储均位于独立数据根，邮箱账号为空 |
| Python 离线回归 | 217项通过，覆盖12个选定测试模块 |
| Node 离线回归 | 186项通过，覆盖12个选定测试文件 |
| Glance 实际 HTTP | 六页外壳、六页异步正文、30个资源及未配置接口，共50次成功请求 |
| 权限失败路径 | 缺少本机会话令牌的邮件请求返回403 |
| 公开信源读取 | 通过实际天气适配器从Open-Meteo取得两日数据 |
| 媒体 | 元数据符合68秒1080p/30fps/H.264，无音轨，全片解码通过，五张代表帧已审查 |
| 文件与来源 | 明确清单、来源和输出指纹、常见凭据格式及个人路径／邮箱扫描通过 |

Python 源码、配置 include、相对链接和 JavaScript 模块引用另由静态入口检查。精简机器可读结果见[验证摘要](verification-summary.json)。

## 可复跑入口

```powershell
python -m pip install -r requirements.txt
npm ci --ignore-scripts
python tools/install_glance.py
python -X utf8 tools/verify.py --http
```

验证工具使用 `.local/test-data` 和 `.local/http-check`，详细日志位于 `.local/verification`。日志、缓存、数据库及本机安装程序属于 Git 忽略范围，公开结果采用精简摘要。

## 证据范围

Python 检查覆盖公众号解析与正文、AI 总结和解读、HTML 封装、ima 规则与恢复、资讯、天气、行情及日历。Node 检查覆盖相关模型、受控 DOM、Markdown、邮箱重连和重置消息状态。

HTTP 检查通过实际 Glance 官方程序和本地桥接，读取六页外壳及异步正文、页面资源、未配置账号与学校接口。该检查同时核对缺少令牌时邮件接口返回403，运行结束后停止本次启动的服务。

媒体通过 ffprobe 元数据、全片解码和代表帧人工审查。新实例的真实 Google OAuth、学校扩展安装、WorkBuddy 实际生成和 ima 云端写入留待使用者配置后验证。Agent 验证使用文件、CLI、受控测试与 HTTP，真实浏览器与桌面交互仍属独立验证范围。项目作者于2026年11月15日确认初稿内容审阅通过，并授权公开发布。

## 公开访问验收

公开仓库为 [GX221015CF/glance-personal-workbench](https://github.com/GX221015CF/glance-personal-workbench)，默认分支为 `main`。GitHub API 用于核对归属、public状态、默认分支和完整文件树，Git远程引用用于核对提交。

匿名HTTP取得仓库首页的实际README渲染HTML，核对标题、代表图片链接和Mermaid架构图代码。源码归档中的156个文件逐项与Git内容指纹匹配，五张PNG和68秒MP4另通过公开原文件链接读取，内容与本地一致。

2026年11月15日发布验收：README封面和“在线播放”入口指向[GitHub Pages播放器](https://gx221015cf.github.io/glance-personal-workbench/watch/)，匿名HTTP返回200和text/html，实际HTML包含HTML5 video controls、现有首页封面和MP4来源。页面媒体地址返回200、video/mp4，完整3,403,764字节下载后的SHA-256为76967856557f4794d92b0a97ba6ca46d573dcd7cbd7251ce5641e807fff5bfc6，与本地审阅版本一致；Range请求返回206及bytes 0-1023/3403764。原始下载链接继续返回200和3,403,764字节，内容类型为application/octet-stream。Pages部署工作流运行43727519171全部步骤成功，首个自动触发因站点尚未启用而在配置步骤失败，启用workflow来源后重新触发成功。播放器和Pages工作流由提交3ba34acd1e81e0949df60e10fc8073205035527b部署，后续验收记录仍在main历史中。以上为GitHub API、Git、静态页面和HTTP证据，未使用真实浏览器连续观看，播放体验仍待用户实际观察。
