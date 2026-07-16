# 微博科技新闻采集程序

这个程序会按关键词搜索微博科技类内容，并导出包含链接、粉丝数、转发数、点赞数、话题、原始微博、图片和视频链接的数据表。

## 安装

```bash
python3 -m pip install -r requirements.txt
```

## 网页前端

启动本地网页控制台：

```bash
python3 app.py
```

然后打开：

```text
http://127.0.0.1:8000
```

页面里可以切换“微博正文”和“话题指标”，填写关键词、页数、Cookie 等参数，采集完成后会生成 CSV 或 JSON 下载链接。导出的文件保存在 `outputs` 文件夹。

## 命令行运行

默认采集科技新闻、AI、芯片、手机、互联网等关键词，每个关键词 3 页：

```bash
python3 weibo_spider.py
```

指定关键词和页数：

```bash
python3 weibo_spider.py -k 科技新闻 人工智能 芯片 -p 5
```

指定某个微博用户 UID 后，在该用户微博内搜索关键词：

```bash
python3 weibo_spider.py --uid 1234567890 -k 人工智能 芯片 -p 5
```

如果微博限制公开访问，可以从浏览器登录后的请求里复制 Cookie：

```bash
python3 weibo_spider.py --cookie "SUB=...; SUBP=...;"
```

## 导出字段

- `keyword`：命中的搜索关键词
- `post_id` / `mid`：微博 ID
- `link`：微博正文链接
- `created_at`：发布时间
- `author_id` / `author_name`：作者信息
- `followers_count`：作者粉丝数
- `reposts_count`：转发数
- `comments_count`：评论数
- `likes_count`：点赞数
- `topics`：微博话题
- `original_weibo`：原始微博正文；如果是转发微博，则优先保存被转发的原微博正文
- `image_urls`：图片链接，多个链接用分号分隔
- `video_urls`：视频链接，多个链接用分号分隔
- `source`：数据来源

## 采集指定微博话题词数据

如果要按话题词采集阅读量、讨论量、互动量、原创量、主持人、热搜位置等维度，使用：

```bash
python3 weibo_topic_spider.py 人工智能 芯片 科技新闻
```

同时导出 JSON：

```bash
python3 weibo_topic_spider.py 人工智能 芯片 --json weibo_topic_metrics.json
```

如果希望记录“本次运行期间看到的热搜最高位置”，可以增加热搜监测轮数。例如每 5 分钟看一次，共看 12 次：

```bash
python3 weibo_topic_spider.py 人工智能 芯片 --monitor-rounds 12 --monitor-interval 300
```

话题采集导出字段：

- `topic`：话题词
- `topic_link`：话题页或搜索页链接
- `containerid`：微博移动端话题容器 ID，能识别到时会写入
- `read_count`：阅读量
- `discussion_count`：讨论量
- `interaction_count`：互动量；优先取话题页官方值，拿不到时按抽样微博的转发、评论、点赞合计
- `original_count`：原创量；优先取话题页官方值，拿不到时按抽样微博中非转发微博数量估算
- `hosts`：主持人/管理员
- `current_hotsearch_rank`：当前热搜位置
- `highest_hotsearch_rank`：本次运行监测到的最高热搜位置
- `sampled_posts`：用于估算互动量和原创量的微博条数
- `raw_stat_text`：原始解析文本片段，方便检查微博页面结构变化

## 注意

微博接口和页面结构可能变化，也可能触发访问频率限制。建议保留 `--sleep` 间隔，不要高频请求；采集和使用数据时请遵守微博平台规则和相关法律法规。
