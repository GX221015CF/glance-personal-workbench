# 第三方代码与素材归属

## Glance

使用 Glance v0.8.7 官方 Windows x64 版本。上游使用 GNU Affero General Public License v3.0，许可全文随本仓库保存在 `THIRD_PARTY_LICENSES/Glance-AGPL-3.0.txt`。官方发行包通过安装脚本下载，程序未纳入 Git 文件清单。若后续分发其程序或改作版本，应按相应许可提供要求的声明和对应源码。[Glance 源码与许可证](https://github.com/glanceapp/glance/tree/v0.8.7)

## 实际收录代码

`app/assets/components` 中的按钮与折叠代码来自配套 Design Lab，随附 MIT `LICENSE` 和 `SOURCE.json`，保留版权及来源。其余自定义业务代码的统一许可证待项目作者选择，当前初稿保留为本地审阅版本。

天气 SVG 由维护项目中的几何符号实现构成，源文件未含第三方署名。它随自定义源码登记来源，统一许可选择时一并核对归属。当前未额外打包字体文件，界面使用系统字体和 Glance 自带资源。

## 通过包管理器安装的依赖

| 依赖 | 声明许可 | 分发方式 |
|---|---|---|
| requests | Apache-2.0 | pip 安装 |
| beautifulsoup4 | MIT | pip 安装 |
| cryptography | Apache-2.0 / BSD | pip 安装 |
| tzdata | Apache-2.0，IANA时区数据依其声明 | pip 安装 |
| jsdom | MIT | npm 开发依赖 |

锁文件用于固定回归环境，依赖和传递依赖的完整许可随各安装包提供。若今后打包发布运行环境，需要同时整理这些声明。

## 媒体与公开数据

介绍视频和截图记录本项目界面及使用流程，画面中的文章标题、摘要、封面、YouTube 缩略图、服务商标和学校材料仍由各自权利人持有。Gmail 的账号、头像及邮件字段为演示替换，相关说明保留在画面中。

BestBlogs 维护公众号 RSS 清单，AIHOT 提供公开资讯接口，Open-Meteo 提供天气，行情读取公开数据源。来源链接在配置或界面中保留。第三方服务的访问条件与内容权利分别适用。

## 待作者决定

对自定义代码采用哪一种统一许可证，需由项目作者决定。选择时应结合实际派生关系和现有第三方许可核对。媒体引用、第三方商标和新闻封面继续按其原有权利处理。
