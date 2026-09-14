# 抖音公开评论采集器

这是一个 Python 3.11+ 项目，用 Playwright 驱动本机 Edge，采集**浏览器页面实际公开可见**的抖音视频评论，并导出为 CSV、Excel 或 JSONL。它不调用未公开接口，不计算签名，不绕过验证码、登录、风控或反爬。

## 已验证的网页情况（2026-09-10）

在公开桌面视频页实测到：

- 视频 ID 位于 `/video/<数字 ID>`；标题可从 `h1` 读取；作者卡片为 `data-e2e="user-info"`。
- 一级评论卡片为 `data-e2e="comment-item"`，昵称节点位于 `data-click-from="title"`。
- 未登录时页面仍可能显示一部分公开评论，同时出现“请先登录后发表评论”。这表示不能互动，不必然表示评论不可读。
- 未登录会话在实测中最多只显示 10 条，且“展开回复”会被登录层阻止。因此程序会如实保存已见评论并停止；它**不会**强制点击、隐藏登录层或尝试其它规避方式。

网页会更新，所以项目将稳定的语义属性写入默认解析器，并提供 `--probe-comments` 保存真实 DOM；不要依据网络文章或猜测填写选择器。

## 项目结构

```text
douyin_comment_crawler/
├── main.py
├── crawler/
│   ├── browser.py       # Edge 会话、低频等待、最多 3 次指数退避
│   ├── video.py         # 视频 ID、标题、作者解析
│   ├── comments.py      # 可见评论、滚动、回复展开
│   └── classifier.py    # 可替换的关键词规则分类器
├── utils/
│   ├── checkpoint.py    # 追加式 JSONL 断点和去重
│   ├── exporter.py      # CSV/XLSX/JSONL 导出
│   ├── logger.py
│   └── simple_yaml.py
├── config.example.yaml
├── requirements.txt
└── tests/
```

## 安装

确认 Windows 已安装 Microsoft Edge 和 Python 3.11+：

```powershell
python --version
python -m pip install -r requirements.txt
```

本项目使用已安装的 Edge，不需要下载 Playwright 自带浏览器。

## 使用

### 本地操作页（推荐）

不想手写命令时，在已激活虚拟环境的 PowerShell 中运行：

```powershell
python main.py
```

也可以使用等效命令 `python web_app.py`。

浏览器会打开 `http://127.0.0.1:8765`。在页面中粘贴一个或多个视频链接（每行一个），设置最大评论数、回复、匿名化和导出格式后点击“开始采集”。页面会显示实时日志，并在完成后提供下载链接。

需要登录时，勾选“先在弹出的 Edge 中手动登录”，在等待时间内按抖音正常流程完成登录；请不要勾选“无界面运行”。按 `Ctrl+C` 可停止本地操作页服务。

### 命令行

先采集单个视频，默认最多新增 20 条：

```powershell
python main.py --url "https://www.douyin.com/video/视频ID" --max-comments 20
```

首次需要登录或希望获取站点仅向已登录用户展示的评论时，不要加 `--headless`。程序会打开普通 Edge；请自行按站点正常流程登录，完成后在终端按 Enter：

```powershell
python main.py --url "https://www.douyin.com/video/视频ID" --login --max-comments 1000 --include-replies
```

批量模式：`videos.txt` 每行一个链接，可用 `#` 开头的行写注释。

```powershell
python main.py --input videos.txt --output data/comments.csv --max-comments 1000 --include-replies --output-format csv --output-format xlsx --output-format jsonl
```

使用已有浏览器登录会话后的无头模式：

```powershell
python main.py --input videos.txt --headless --max-comments 1000
```

研究数据集建议匿名化昵称，并提供由项目方保管的盐值：

```powershell
python main.py --url "https://www.douyin.com/video/视频ID" --anonymize --anonymize-salt "请替换为你的保密盐值"
```

## 输出字段

导出包含：`video_url`、`video_id`、`video_title`、`author_name`、`video_category`、`comment_id`、`comment_text`、`comment_category`、`comment_time`、`like_count`、`reply_count`、`parent_comment_id`、`comment_level`、`user_name`、`crawl_time`。

如果公开 DOM 没有 comment ID 或父评论 ID，字段保留为空；程序不会伪造 ID。没有公开 ID 时，断点去重使用内部哈希指纹，但该指纹不会写进 `comment_id`。

## 断点、限速与失败处理

- 每条新记录先追加到 `输出文件同名.checkpoint.jsonl`，即使中断也保留已采集数据；下次使用同一输出路径会自动去重恢复。
- 页面打开失败最多重试 3 次，退避为约 1、2 秒加随机抖动。
- 页面操作之间随机等待 1.2–2.5 秒；可在配置文件的 `crawl` 中调整。
- 日志在 `data/logs/crawler.log`；使用 `--verbose` 查看解析及点击细节。

## DOM 探针与选择器配置

出现页面改版、要确认回复节点，或解析字段为空时，先在你的正常登录会话下运行：

```powershell
python main.py --url "https://www.douyin.com/video/视频ID" --login --probe-comments --max-comments 20
```

DOM 会保存到 `data/dom_probes/<video_id>.html`。只可依据其中**已经可见的公开节点**更新 `config.example.yaml` 的副本，例如：

```powershell
Copy-Item config.example.yaml config.yaml
python main.py --url "https://www.douyin.com/video/视频ID" --config config.yaml
```

如果实测回复节点能与一级节点区分，可填 `reply_item_selector`，程序会写出 `comment_level=回复`。只有 DOM 公开了可验证的父 ID 属性时，才填写 `parent_id_selector` 和 `parent_id_attribute`；否则 `parent_comment_id` 必须为空。

## 分类模块

`crawler/classifier.py` 当前按关键词将视频分类为医生科普、疾病咨询、药物相关、医疗经历、健康知识或其他；评论分类为咨询症状、询问治疗、询问药物、分享经历、感谢医生、质疑/反对或其他。它通过 `classify_video` 与 `classify_comment` 两个接口隔离，后续可替换为本地 NLP 或获得授权后的 LLM 实现。

## 测试

```powershell
python -m unittest discover -s tests -v
```

本地已执行过单元测试，并在公开视频 `7633719890070867251` 完成端到端验证：成功解析并导出 10 条未登录公开可见评论（网站当时仅渲染 10 条，因而未达到 20 条上限）。要验证 20 条、继续滚动及展开回复，需要用你自己的正常登录会话运行上述命令；项目不会绕过该站点呈现的登录层。
