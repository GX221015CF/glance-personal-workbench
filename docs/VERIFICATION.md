# 本地初稿验证

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

媒体通过 ffprobe 元数据、全片解码和代表帧人工审查。新实例的真实 Google OAuth、学校扩展安装、WorkBuddy 实际生成和 ima 云端写入留待使用者配置后验证。真实浏览器与桌面界面在本批未操作，实际阅读和连续播放效果由作者审阅。
